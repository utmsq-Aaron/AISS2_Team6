// FitDash BFF — serves the built React SPA and proxies /api (incl. SSE) to the
// Python FastAPI. Hosts the optional PIN gate (DO_LOCK + APP_PIN), mirroring the
// Streamlit gate which is OFF by default.
//
//   API_TARGET  FastAPI base URL          (default http://127.0.0.1:8000)
//   PORT        BFF listen port           (default 3000)
//   WEB_DIST    built SPA dir             (default ../web/dist)
//   DO_LOCK     "true" to enable PIN gate (default off)
//   APP_PIN     required PIN when locked

import cookieParser from "cookie-parser";
import express from "express";
import { createProxyMiddleware } from "http-proxy-middleware";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const API_TARGET = process.env.API_TARGET || "http://127.0.0.1:8000";
const PORT = Number(process.env.PORT || 3000);
// Bind localhost by default so the raw port is never exposed on the network — a
// tunnel (Cloudflare/Tailscale) reaches it at 127.0.0.1. Set HOST=0.0.0.0 to also
// serve directly on the LAN (http://<machine-ip>:PORT).
const HOST = process.env.HOST || "127.0.0.1";
const WEB_DIST = process.env.WEB_DIST || path.resolve(__dirname, "../web/dist");

const LOCK = String(process.env.DO_LOCK || "false").toLowerCase() === "true";
const PIN = process.env.APP_PIN || "";
const PIN_ENABLED = LOCK && !!PIN;

const app = express();
app.use(cookieParser());

// ── Optional PIN gate ─────────────────────────────────────────────────────────
if (PIN_ENABLED) {
  app.use(express.json());
  app.post("/bff/login", (req, res) => {
    if (req.body?.pin === PIN) {
      res.cookie("fd_auth", "1", { httpOnly: true, sameSite: "lax", maxAge: 7 * 864e5 });
      return res.json({ ok: true });
    }
    res.status(401).json({ ok: false, error: "Incorrect PIN" });
  });
  app.get("/bff/status", (req, res) => res.json({ locked: true, authed: req.cookies?.fd_auth === "1" }));

  app.use((req, res, next) => {
    if (req.path === "/bff/login" || req.path === "/bff/status") return next();
    if (req.cookies?.fd_auth === "1") return next();
    if (req.path.startsWith("/api")) return res.status(401).json({ error: "locked" });
    // For the SPA, let it load; the frontend renders its own PIN screen via /bff/status.
    next();
  });
} else {
  app.get("/bff/status", (_req, res) => res.json({ locked: false, authed: true }));
}

// ── API proxy (Server-Sent Events friendly) ───────────────────────────────────
// pathFilter (not app.use("/api", …)) so the full "/api/…" path is preserved and
// forwarded to FastAPI unchanged — Express would otherwise strip the mount prefix.
app.use(
  createProxyMiddleware({
    target: API_TARGET,
    changeOrigin: true,
    pathFilter: "/api",
    proxyTimeout: 0, // don't time out long SSE streams
    on: {
      proxyReq: (proxyReq) => proxyReq.setHeader("Accept-Encoding", "identity"),
    },
  }),
);

// ── Static SPA ─────────────────────────────────────────────────────────────────
if (fs.existsSync(WEB_DIST)) {
  app.use(express.static(WEB_DIST));
  app.get("*", (_req, res) => res.sendFile(path.join(WEB_DIST, "index.html")));
} else {
  app.get("*", (_req, res) =>
    res
      .status(503)
      .send(`SPA not built. Run \`npm run build\` in web/ (looked in ${WEB_DIST}).`),
  );
}

app.listen(PORT, HOST, () => {
  console.log(`FitDash BFF → ${HOST}:${PORT}  ·  API ${API_TARGET}  ·  PIN ${PIN_ENABLED ? "ON" : "off"}`);
});
