# One image for every Python process in the stack — the nine MCP servers, the
# seven agents, the FastAPI seam and the Telegram bridge. They differ only in
# what they start:
#
#   MCP servers  → the SERVER env var picks the module (the default CMD below)
#   everything else → docker-compose overrides `command:` outright
#
# One image means one dependency install and one thing to rebuild, which is what
# keeps a 17-service compose file manageable.
#
#   docker build -t training-copilot .
FROM python:3.11-slim

# curl is used by the compose healthchecks and is handy for debugging a running
# container; git is needed by a few pip source installs.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CPU-only torch, installed BEFORE everything else.
#
# requirements.txt has sentence-transformers (the fitness RAG's local embedding
# model), which pulls torch. On Linux the default PyPI wheel is the CUDA build:
# ~2.5 GB, plus ~2 GB of nvidia-* dependencies — none of which this image can use,
# since the embedding model runs on CPU here. Installing the CPU wheel first means
# the requirements.txt install below sees torch as already satisfied, and the image
# stays about 4 GB smaller. (On a developer's Mac, requirements.txt still resolves
# normally to the MPS-capable build — this only affects the container.)
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch

# The rest. This layer is cached and only re-runs when requirements change, so an
# ordinary code edit rebuilds in seconds.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code. .dockerignore keeps credentials, the venv, node_modules and
# the MLflow store out — those are bind-mounted at runtime instead.
COPY . .

# Unbuffered stdout, so `docker compose logs -f` shows output as it happens
# rather than in 8 KB bursts.
ENV PYTHONUNBUFFERED=1

# Overridden per service in docker-compose (e.g. SERVER=servers.routes_mcp).
ENV SERVER=servers.weather_mcp
CMD ["sh", "-c", "python -m $SERVER"]
