"""LLM-generated charts for a chat turn — returns Plotly figure JSON specs."""

from typing import Any, Dict

from fastapi import APIRouter
from pydantic import BaseModel

from api.chart_service import generate_figures

router = APIRouter()


class ChartRequest(BaseModel):
    trace: Dict[str, Any]


@router.post("/charts")
def charts(req: ChartRequest):
    """Generate (and cache by run_id) Plotly figures illustrating a chat answer."""
    return {"figures": generate_figures(req.trace)}
