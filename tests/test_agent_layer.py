"""Regression test for the LangGraph + A2A agent layer — runs WITHOUT the LLM gateway.

Two checks, both deterministic:
  1. build_trace() emits the exact UI/chart/route trace contract.
  2. A full in-process A2A two-hop (adapter -> orchestrator -> recovery specialist ->
     tool recorder -> DataPart -> assembled trace) using a scripted fake chat model
     and a fake MCP host — so it needs neither the KIT gateway nor live MCP servers.

Run:  python tests/test_agent_layer.py
Uses test ports 9100/9101 (overridden via *_A2A_URL env) so it won't clash with a
running dev stack.
"""

import os
import sys
import json
import asyncio
from pathlib import Path

# Isolate from a running stack + restrict the orchestrator to the recovery specialist.
os.environ.setdefault("ORCHESTRATOR_A2A_URL", "http://127.0.0.1:9100/")
os.environ.setdefault("RECOVERY_A2A_URL", "http://127.0.0.1:9101/")
os.environ.setdefault("ORCHESTRATOR_SPECIALISTS", "recovery")
sys.path.insert(0, str(Path(__file__).parent.parent))

import uvicorn
from pydantic import PrivateAttr
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatResult, ChatGeneration


# ── 1) build_trace contract ───────────────────────────────────────────────────

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


# ── 2) full in-process A2A two-hop with a fake model + fake host ───────────────

class _ScriptedModel(BaseChatModel):
    """Human -> call first bound tool; ToolMessage -> final answer."""
    _bound: list = PrivateAttr(default_factory=list)

    def bind_tools(self, tools, **kw):
        self._bound = list(tools)
        return self

    def _make(self, messages):
        last = messages[-1] if messages else None
        if isinstance(last, ToolMessage):
            return AIMessage(content=f"FINAL ANSWER ({str(last.content)[:40]})")
        if self._bound:
            t = self._bound[0]
            args = {}
            for k, sch in (getattr(t, "args", {}) or {}).items():
                if isinstance(sch, dict) and sch.get("type") == "string":
                    args[k] = "2026-06-16" if k == "date" else "Assess my recovery today."
            return AIMessage(content="", tool_calls=[{"name": t.name, "args": args, "id": "c1", "type": "tool_call"}])
        return AIMessage(content="direct answer")

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        return ChatResult(generations=[ChatGeneration(message=self._make(messages))])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kw):
        return ChatResult(generations=[ChatGeneration(message=self._make(messages))])

    @property
    def _llm_type(self):
        return "scripted"


class _FakeHost:
    async def alist_tools(self):
        return [{"type": "function", "function": {
            "name": "garmin__get_garmin_sleep", "description": "Garmin sleep for a date.",
            "parameters": {"type": "object", "properties": {"date": {"type": "string"}}, "required": []}}}]

    async def acall_tool(self, name, args):
        return json.dumps({"date": args.get("date"), "sleep_hours": 7.2, "score": 85})


def test_a2a_two_hop():
    import agents._base_executor as be
    import core.orchestrator_agent as oa
    import core.orchestrator as orch
    from a2a.server.apps import A2AStarletteApplication
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.tasks import InMemoryTaskStore
    from a2a.types import AgentCard, AgentSkill, AgentCapabilities
    from agents.prompts import specialist_prompt

    be.get_chat_model = lambda: _ScriptedModel()
    oa.get_chat_model = lambda: _ScriptedModel()
    be.scoped_host = lambda names, **kw: _FakeHost()

    def card(name, port):
        return AgentCard(name=name, description=name, url=f"http://127.0.0.1:{port}/", version="1.0.0",
                         capabilities=AgentCapabilities(streaming=True),
                         default_input_modes=["text"], default_output_modes=["text"],
                         skills=[AgentSkill(id=name, name=name, description=name, tags=[name])])

    def app(executor, name, port):
        h = DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore())
        return A2AStarletteApplication(agent_card=card(name, port), http_handler=h).build()

    rec = be.SpecialistExecutor("recovery", ["garmin"], specialist_prompt("recovery"))

    async def run():
        s_rec = uvicorn.Server(uvicorn.Config(app(rec, "recovery", 9101), host="127.0.0.1", port=9101, log_level="error"))
        s_orc = uvicorn.Server(uvicorn.Config(app(oa.OrchestratorExecutor(), "orchestrator", 9100), host="127.0.0.1", port=9100, log_level="error"))
        t1 = asyncio.create_task(s_rec.serve())
        t2 = asyncio.create_task(s_orc.serve())
        for _ in range(120):
            if s_rec.started and s_orc.started:
                break
            await asyncio.sleep(0.05)
        assert s_rec.started and s_orc.started, "servers did not start"

        status = []
        def call():
            return orch.FitDashOrchestrator().run(
                "Should I train today based on my recovery?", [], progress_cb=status.append)
        answer, trace = await asyncio.get_running_loop().run_in_executor(None, call)

        s_rec.should_exit = True
        s_orc.should_exit = True
        await asyncio.gather(t1, t2)
        return answer, trace, status

    answer, trace, status = asyncio.run(run())
    assert answer and "FINAL ANSWER" in answer, f"bad answer: {answer!r}"
    assert any(a.get("agent") == "recovery" for a in trace.get("agents", [])), trace.get("agents")
    assert any(c.get("tool") == "garmin__get_garmin_sleep" for c in trace.get("tool_calls", [])), trace.get("tool_calls")
    assert trace.get("answer") == answer
    assert any("recovery" in m for m in status), status
    print("PASS  A2A two-hop (adapter -> orchestrator -> recovery -> recorder -> trace)")


if __name__ == "__main__":
    test_build_trace_contract()
    test_a2a_two_hop()
    print("\nALL AGENT-LAYER TESTS PASSED")
