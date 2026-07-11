"""Coach Agent — A2A server :9006.

A LangGraph ReAct agent over the athlete + strava + garmin MCP servers, plus the
local fitness-literature RAG search (the same tool the fitness agent uses, so
workout choices can carry citations). It owns the structured race goal, the
personal timeline, the deterministically computed zones and the training plan —
fetching real numbers from strava/garmin and letting the athlete server do all
arithmetic (Riegel prognosis, zone bands, ramp-capped week volumes). Run standalone:

    python -m agents.coach_agent
"""

import functools

from agents._base_executor import SpecialistExecutor, run_agent_server
from agents._rag_executor import _make_search_tool
from agents.prompts import specialist_prompt
from core.config import AGENT_MCP_SCOPE

_DESC = ("Structured race goal, personal timeline (injuries/illnesses/races), "
         "deterministic HR+pace zones and the guardrail-validated multi-week "
         "training plan — personalised from the athlete's real Strava/Garmin data.")

if __name__ == "__main__":
    executor = SpecialistExecutor(
        "coach",
        AGENT_MCP_SCOPE["coach"],
        functools.partial(specialist_prompt, "coach"),
        extra_tools=lambda recorder: [_make_search_tool(recorder)],
    )
    run_agent_server(
        "coach", executor, description=_DESC,
        skill_id="coach", skill_name="Training plan coaching",
        skill_desc=_DESC, tags=["coach", "plan", "zones", "goal", "periodization"],
    )
