"""FitDash API — FastAPI app exposing the core to the React + Node frontend.

Run from the project root (so `core`, `servers`, `.env`, `.tokens` resolve):
    uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload
"""

from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from api.auth import current_user  # noqa: E402
from api.routers import auth, chat, charts, health, settings, sync, tools  # noqa: E402
from core.tracing import setup_tracing  # noqa: E402

setup_tracing("api")  # MLflow autologging for the chart-service LLM calls

app = FastAPI(title="FitDash API", version="0.1.0")

# In production the Node BFF serves the SPA same-origin and proxies /api, so CORS
# is not strictly needed; allow localhost dev origins (Vite :5173, BFF :3000).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:3000", "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth routes are public (you log in here); everything else requires a Bearer token.
app.include_router(auth.router, prefix="/api")

_PROTECTED = [Depends(current_user)]
for r in (health.router, tools.router, chat.router, charts.router, settings.router, sync.router):
    app.include_router(r, prefix="/api", dependencies=_PROTECTED)


@app.get("/api/ping")
def ping():
    return {"ok": True, "service": "fitdash-api"}
