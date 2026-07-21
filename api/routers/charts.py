"""LLM-generated charts for a chat turn — returns Plotly figure JSON specs —
plus the chart "Explain" endpoint (plain-language reading of a rendered chart's
data, so the dashboard needs no chat round trip)."""

import json
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.chart_service import generate_figures
from core.llm import completion_params, get_llm_client

router = APIRouter()


class ChartRequest(BaseModel):
    trace: Dict[str, Any]


@router.post("/charts")
def charts(req: ChartRequest):
    """Generate (and cache by run_id) Plotly figures illustrating a chat answer."""
    return {"figures": generate_figures(req.trace)}


class ExplainRequest(BaseModel):
    title: str                            # what the chart/section shows
    data_summary: Dict[str, Any]          # compact summary of the rendered data


_EXPLAIN_PROMPT = (
    "You are the buddy-coach of a personal sports-analytics app. The athlete is "
    "looking at a chart and tapped 'Explain'. Explain what THEIR data shows in 2-3 "
    "plain, friendly sentences — no jargon (say 'fitness/fatigue/form', not "
    "'CTL/ATL/TSB'), no headers, no bullet lists, no invented numbers: use ONLY the "
    "values in the summary below, and say what they practically mean for training. "
    "Answer in English.\n\nChart: {title}\nData summary:\n{data}"
)


@router.post("/charts/explain")
def explain(req: ExplainRequest):
    """2-3 plain-language sentences about the data a chart currently shows."""
    data = json.dumps(req.data_summary, ensure_ascii=False)
    if len(data) > 4000:
        data = data[:4000] + "…"
    try:
        client, model = get_llm_client()
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user",
                       "content": _EXPLAIN_PROMPT.format(title=req.title[:200], data=data)}],
            **completion_params(model, 300),
        )
        text = (resp.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001 — surface a clean, actionable error
        raise HTTPException(status_code=502,
                            detail=f"explain unavailable: {type(exc).__name__}") from exc
    if not text:
        raise HTTPException(status_code=502, detail="explain unavailable: empty answer")
    return {"explanation": text}
