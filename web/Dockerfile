# The web front door: builds the React SPA, then serves it through the Node BFF
# (server/index.js), which also proxies /api to the FastAPI container and hosts
# the optional shared PIN gate.
#
# Build context is the REPO ROOT (see docker-compose.yml), because this needs
# both web/ and server/.
#
#   docker build -f web/Dockerfile -t training-copilot-web .

# ── Stage 1: build the SPA ────────────────────────────────────────────────────
FROM node:20-slim AS build
WORKDIR /build

# Dependencies first, so a source-only change doesn't re-run npm ci.
COPY web/package.json web/package-lock.json* ./
RUN npm ci

COPY web/ ./
# The build logs "could not read FASTAPI_PORT from core/config.py (spawnSync
# python3 ENOENT)". That is expected and harmless: vite.config.ts shells out to
# python3 to read the port registry, and this image has no Python. The port only
# configures the DEV proxy — a production build contains no proxy at all, and the
# config falls back to :8000 either way. At runtime the BFF gets the real target
# from API_TARGET, so nothing here depends on that lookup succeeding.
RUN npm run build

# ── Stage 2: the BFF that serves it ───────────────────────────────────────────
FROM node:20-slim
WORKDIR /app

COPY server/package.json server/package-lock.json* ./
RUN npm ci --omit=dev

COPY server/index.js ./
COPY --from=build /build/dist ./web-dist

# server/index.js resolves WEB_DIST relative to itself by default (../web/dist),
# which is not the layout inside this image — point it at the copied build.
ENV WEB_DIST=/app/web-dist \
    API_TARGET=http://api:8000 \
    HOST=0.0.0.0 \
    PORT=3000

EXPOSE 3000
CMD ["node", "index.js"]
