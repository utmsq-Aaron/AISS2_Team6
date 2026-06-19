"""Regression test for the LangGraph + A2A agent layer — runs WITHOUT the LLM gateway.

Deterministic checks (fake chat model + fake MCP host, no gateway, no live MCP):
  1. build_trace() emits the exact UI/chart/route trace contract.
  2. build_trace() flattens peer ``sub_artifacts`` (mesh) into agents + tool_calls.
  3. _peers_for(): full mesh at depth 1, capped at the depth limit, env toggle.
  4. A full in-process A2A two-hop (adapter -> orchestrator -> recovery).
  5. Peer-to-peer mesh: orchestrator -> recovery -> (consults) context, and the
     context peer's MCP call surfaces in the final trace.

Run:  python tests/test_agent_layer.py
Uses test ports 9100/9101/9103 (overridden via *_A2A_URL env) to avoid a running stack.
"""

import os
import sys
import json
import asyncio
from pathlib import Path

os.environ.setdefault("ORCHESTRATOR_A2A_URL", "http://127.0.0.1:9100/")
os.environ.setdefault("RECOVERY_A2A_URL", "http://127.0.0.1:9101/")
os.environ.setdefault("CONTEXT_A2A_URL", "http://127.0.0.1:9103/")
os.environ.setdefault("ORCHESTRATOR_SPECIALISTS", "recovery")
sys.path.insert(0, str(Path(__file__).parent.parent))

import uvicorn
from pydantic import PrivateAttr
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatResult, ChatGeneration


# ── fakes ─────────────────────────────────────────────────────────────────────

_TOOL_FOR = {"garmin": "garmin__get_garmin_sleep", "weather": "weather__get_weather_forecast",
             "strava": "strava__get_activities", "routes": "routes__plan_route",
             "calendar": "calendar__list_events"}


class _FakeHost:
    def __init__(self, names=None):
        self.names = names or ["garmin"]

    async def alist_tools(self):
        name = _TOOL_FOR.get(self.names[0], f"{self.names[0]}__tool")
        return [{"type": "function", "function": {
            "name": name, "description": f"{self.names[0]} tool",
            "parameters": {"type": "object", "properties": {"date": {"type": "string"}}, "required": []}}}]

    async def acall_tool(self, name, args):
        return json.dumps({"tool": name, "ok": True, "value": 42})


def _fill(tool):
    args = {}
    for k, sch in (getattr(tool, "args", {}) or {}).items():
        if isinstance(sch, dict) and sch.get("type") == "string":
            args[k] = "Need this domain's input." if k == "question" else "2026-06-16"
    return args


class _ScriptedModel(BaseChatModel):
    """Human -> call first bound tool; ToolMessage -> final answer."""
    _bound: list = PrivateAttr(default_factory=list)

    def bind_tools(self, tools, **kw):
        self._bound = list(tools)
        return self

    def _pick(self, messages):
        return self._bound[0] if self._bound else None

    def _make(self, messages):
        last = messages[-1] if messages else None
        if isinstance(last, ToolMessage):
            return AIMessage(content=f"FINAL ANSWER ({str(last.content)[:40]})")
        t = self._pick(messages)
        if t is not None:
            return AIMessage(content="", tool_calls=[{"name": t.name, "args": _fill(t), "id": "c1", "type": "tool_call"}])
        return AIMessage(content="direct answer")

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        return ChatResult(generations=[ChatGeneration(message=self._make(messages))])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kw):
        return ChatResult(generations=[ChatGeneration(message=self._make(messages))])

    @property
    def _llm_type(self):
        return "scripted"


class _MeshModel(_ScriptedModel):
    """Prefers the ask_context peer tool when available — to exercise the mesh."""
    def _pick(self, messages):
        peer = next((t for t in self._bound if t.name == "ask_context"), None)
        return peer or (self._bound[0] if self._bound else None)


# ── A2A server helpers ─────────────────────────────────────────────────────────

def _app(executor, name, port):
    from a2a.server.apps import A2AStarletteApplication
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.tasks import InMemoryTaskStore
    from a2a.types import AgentCard, AgentSkill, AgentCapabilities
    card = AgentCard(name=name, description=name, url=f"http://127.0.0.1:{port}/", version="1.0.0",
                     capabilities=AgentCapabilities(streaming=True),
                     default_input_modes=["text"], default_output_modes=["text"],
                     skills=[AgentSkill(id=name, name=name, description=name, tags=[name])])
    h = DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore())
    return A2AStarletteApplication(agent_card=card, http_handler=h).build()


async def _run_servers(specs, question):
    """specs: list of (executor, name, port). Start them, call the adapter, stop."""
    import core.orchestrator as orch
    servers = [uvicorn.Server(uvicorn.Config(_app(ex, n, p), host="127.0.0.1", port=p, log_level="error"))
               for ex, n, p in specs]
    tasks = [asyncio.create_task(s.serve()) for s in servers]
    for _ in range(160):
        if all(s.started for s in servers):
            break
        await asyncio.sleep(0.05)
    assert all(s.started for s in servers), "servers did not start"
    status = []
    answer, trace = await asyncio.get_running_loop().run_in_executor(
        None, lambda: orch.FitDashOrchestrator().run(question, [], progress_cb=status.append))
    for s in servers:
        s.should_exit = True
    await asyncio.gather(*tasks)
    return answer, trace, status


# ── tests ───────────────────────────────────────────────────────────────────────

def test_build_trace_contract():
    from core.agent_trace import build_trace
    arts = [{"agent": "recovery", "duration_ms": 1200, "tool_calls": [
        {"tool": "garmin__get_garmin_sleep", "args": {"date": "2026-06-16"},
         "label": "garmin__get_garmin_sleep",
         "result": json.dumps({"sleep_hours": 7.2, "score": 85}), "duration_ms": 120, "error": None},
        {"tool": "routes__plan_route", "args": {}, "label": "routes__plan_route",
         "result": json.dumps({"waypoints": [[1, 2], [3, 4]], "distance_km": 10}), "duration_ms": 80, "error": None},
    ]}]
    tr = build_trace(user_input="should I train?", run_id="abc12345", specialist_artifacts=arts,
                     answer="Recovered: 7.2h, score 85.\n<!--charts: sleep score trend-->",
                     total_ms=1300, error=None)
    need = {"run_id", "ts", "user_input", "plan", "tool_calls", "timing",
            "error", "actions", "agents", "route_data", "chart_hints", "answer"}
    assert need <= set(tr), f"missing keys: {need - set(tr)}"
    assert tr["timing"] == {"total_ms": 1300}
    assert isinstance(tr["tool_calls"][0]["result"], str), "tool_calls[].result must be a JSON string"
    assert tr["route_data"] == {"tool": "plan_route",
                                "data": {"waypoints": [[1, 2], [3, 4]], "distance_km": 10}}
    assert tr["chart_hints"] == ["sleep score trend"]
    assert "<!--charts" not in tr["answer"]
    assert tr["agents"][0]["agent"] == "recovery" and tr["agents"][0]["duration_ms"] == 1200
    assert tr["plan"]["steps"][0]["tool"] == "garmin__get_garmin_sleep"
    print("PASS  build_trace contract (route_data/chart_hints/agents/plan/timing)")


def test_build_trace_sub_artifacts():
    from core.agent_trace import build_trace
    arts = [{"agent": "recovery", "duration_ms": 100,
             "tool_calls": [{"tool": "garmin__get_garmin_sleep", "result": json.dumps({"x": 1})}],
             "sub_artifacts": [{"agent": "context", "duration_ms": 50,
                                "tool_calls": [{"tool": "weather__get_weather_forecast",
                                                "result": json.dumps({"temp": 15})}]}]}]
    tr = build_trace(user_input="q", run_id="r", specialist_artifacts=arts, answer="a", total_ms=200)
    names = [a["agent"] for a in tr["agents"]]
    assert names == ["recovery", "context"], names
    tools = [c["tool"] for c in tr["tool_calls"]]
    assert "garmin__get_garmin_sleep" in tools and "weather__get_weather_forecast" in tools, tools
    print("PASS  build_trace flattens peer sub_artifacts (mesh peers become agent rows)")


def test_peers_and_depth():
    import agents._base_executor as be
    p = be._peers_for("recovery", 1)
    assert "context" in p and "recovery" not in p, p
    assert be._peers_for("recovery", 2) == [], "depth cap should block re-delegation"
    os.environ["AGENT_MESH"] = "0"
    assert be._peers_for("recovery", 1) == [], "AGENT_MESH=0 should disable peers"
    os.environ["AGENT_MESH"] = "1"
    print("PASS  _peers_for: full mesh at depth 1, capped at depth 2, env toggle")


def test_a2a_two_hop():
    import agents._base_executor as be
    import core.orchestrator_agent as oa
    from agents.prompts import specialist_prompt
    be.get_chat_model = lambda: _ScriptedModel()
    oa.get_chat_model = lambda: _ScriptedModel()
    be.scoped_host = lambda names, **kw: _FakeHost(names)

    rec = be.SpecialistExecutor("recovery", ["garmin"], specialist_prompt("recovery"))
    answer, trace, status = asyncio.run(_run_servers(
        [(rec, "recovery", 9101), (oa.OrchestratorExecutor(), "orchestrator", 9100)],
        "Should I train today based on my recovery?"))
    assert answer and "FINAL ANSWER" in answer, f"bad answer: {answer!r}"
    assert any(a.get("agent") == "recovery" for a in trace.get("agents", [])), trace.get("agents")
    assert any(c.get("tool") == "garmin__get_garmin_sleep" for c in trace.get("tool_calls", [])), trace.get("tool_calls")
    assert trace.get("answer") == answer
    assert any("recovery" in m for m in status), status
    print("PASS  A2A two-hop (adapter -> orchestrator -> recovery -> recorder -> trace)")


def test_mesh_peer_delegation():
    import agents._base_executor as be
    import core.orchestrator_agent as oa
    from agents.prompts import specialist_prompt
    be.get_chat_model = lambda: _MeshModel()
    oa.get_chat_model = lambda: _MeshModel()
    be.scoped_host = lambda names, **kw: _FakeHost(names)

    rec = be.SpecialistExecutor("recovery", ["garmin"], specialist_prompt("recovery"))
    ctx = be.SpecialistExecutor("context", ["weather", "calendar"], specialist_prompt("context"))
    answer, trace, status = asyncio.run(_run_servers(
        [(rec, "recovery", 9101), (ctx, "context", 9103), (oa.OrchestratorExecutor(), "orchestrator", 9100)],
        "Plan a run for tomorrow given my recovery and the weather."))
    agent_names = [a.get("agent") for a in trace.get("agents", [])]
    tool_names = [c.get("tool") for c in trace.get("tool_calls", [])]
    assert "recovery" in agent_names and "context" in agent_names, f"mesh agents missing: {agent_names}"
    assert any(t and t.startswith("weather__") for t in tool_names), f"peer MCP call missing: {tool_names}"
    assert answer and "FINAL ANSWER" in answer
    # The orchestrator's own progress reaches the adapter; the nested
    # recovery→context consultation is captured in the trace agent pipeline (asserted above).
    assert any("recovery" in m.lower() for m in status), status
    print("PASS  mesh peer delegation (recovery consults context; peer call in trace)")


if __name__ == "__main__":
    test_build_trace_contract()
    test_build_trace_sub_artifacts()
    test_peers_and_depth()
    test_a2a_two_hop()
    test_mesh_peer_delegation()
    print("\nALL AGENT-LAYER TESTS PASSED")
