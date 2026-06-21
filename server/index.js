// FitDash BFF — serves the built React SPA and proxies /api (incl. SSE) to the
// Python FastAPI. Hosts the optional shared PIN gate (DO_LOCK + APP_PIN) — the
// single secret in front of the whole app for a public deployment.
//
//   API_TARGET   FastAPI base URL              (default http://127.0.0.1:8000)
//   PORT / HOST  BFF listen addr               (default 127.0.0.1:3000)
//   WEB_DIST     built SPA dir                 (default ../web/dist)
//   DO_LOCK      "true" to enable the PIN gate (default off)
//   APP_PIN      required passphrase when locked (use a long one, not 4 digits)
//   AUTH_SECRET  HMAC key signing the gate cookie — SET THIS in production so
//                sessions survive restarts and can't be forged (else a random
//                per-process key is used and everyone re-enters the PIN on restart)
//
// Hardening (so a public PIN gate is actually safe):
//   • the session cookie is HMAC-SIGNED (cookie-parser secret) — it cannot be
//     forged by setting a constant value; it also carries a server-checked expiry.
//   • /bff/login is RATE-LIMITED with per-IP lockout + a small delay, so the PIN
//     can't be brute-forced.
//   • the PIN is compared in CONSTANT TIME.
//   • behind a tunnel we trust X-Forwarded-* for the client IP and https flag.

import crypto from "node:crypto";
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

// HMAC key that signs the gate cookie. Set AUTH_SECRET in production; otherwise a
// random per-process key (sessions then reset on restart, which is safe but means
// everyone re-enters the PIN after a redeploy).
const SECRET = process.env.AUTH_SECRET || crypto.randomBytes(32).toString("hex");
if (PIN_ENABLED && !process.env.AUTH_SECRET) {
  console.warn("[bff] PIN gate ON but AUTH_SECRET unset — using a random key (sessions reset on restart).");
}

const SESSION_MS = 7 * 24 * 60 * 60 * 1000; // gate cookie lifetime

// Brute-force controls for /bff/login (in-memory, per client IP).
const MAX_FAILS = 5; // failures allowed before a lockout
const FAIL_WINDOW_MS = 15 * 60 * 1000; // window the failures are counted over
const LOCKOUT_MS = 15 * 60 * 1000; // base lockout once MAX_FAILS is hit (doubles per repeat)
const FAIL_DELAY_MS = 300; // small per-failure delay to slow scripted guessing
const _gateFails = new Map(); // ip -> { count, first, lockUntil, strikes }

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const clientIp = (req) => req.ip || req.socket?.remoteAddress || "unknown";
const isHttps = (req) =>
  req.secure || String(req.headers["x-forwarded-proto"] || "").split(",")[0].trim() === "https";

function constantTimeEqual(a, b) {
  const ab = Buffer.from(String(a));
  const bb = Buffer.from(String(b));
  if (ab.length !== bb.length) return false; // length leak is unavoidable & harmless here
  return crypto.timingSafeEqual(ab, bb);
}

function setGateCookie(req, res) {
  // Value carries a server-checked expiry; cookie-parser HMAC-signs it (signed:true),
  // so it can't be forged without SECRET.
  const exp = Date.now() + SESSION_MS;
  res.cookie("fd_gate", String(exp), {
    httpOnly: true,
    sameSite: "lax",
    secure: isHttps(req), // Secure over the tunnel's https; not required for localhost http
    signed: true,
    maxAge: SESSION_MS,
  });
}

function gateOpen(req) {
  const v = req.signedCookies?.fd_gate; // false if signature is invalid/forged
  if (!v) return false;
  const exp = Number(v);
  return Number.isFinite(exp) && exp > Date.now();
}

const app = express();
app.set("trust proxy", true); // behind the tunnel: trust X-Forwarded-For / -Proto
app.use(cookieParser(SECRET));

// ── Optional shared PIN gate ────────────────────────────────────────────────────
if (PIN_ENABLED) {
  app.use(express.json());

  app.post("/bff/login", async (req, res) => {
    const ip = clientIp(req);
    const now = Date.now();
    let rec = _gateFails.get(ip);
    if (rec && now - rec.first > FAIL_WINDOW_MS && (!rec.lockUntil || rec.lockUntil < now)) {
      rec = undefined; // window elapsed and not locked → reset
    }
    if (rec?.lockUntil && rec.lockUntil > now) {
      const retry = Math.ceil((rec.lockUntil - now) / 1000);
      res.set("Retry-After", String(retry));
      return res.status(429).json({ ok: false, error: "Too many attempts.", retryAfter: retry });
    }

    if (typeof req.body?.pin === "string" && constantTimeEqual(req.body.pin, PIN)) {
      _gateFails.delete(ip); // success clears the record
      setGateCookie(req, res);
      return res.json({ ok: true });
    }

    // Failure: count, maybe lock, slow down.
    rec = rec || { count: 0, first: now, lockUntil: 0, strikes: 0 };
    rec.count += 1;
    if (rec.count >= MAX_FAILS) {
      rec.strikes += 1;
      rec.lockUntil = now + LOCKOUT_MS * 2 ** (rec.strikes - 1); // exponential backoff
      rec.count = 0;
      rec.first = now;
    }
    _gateFails.set(ip, rec);
    await sleep(FAIL_DELAY_MS);
    if (rec.lockUntil > now) {
      const retry = Math.ceil((rec.lockUntil - now) / 1000);
      res.set("Retry-After", String(retry));
      return res.status(429).json({ ok: false, error: "Too many attempts.", retryAfter: retry });
    }
    return res.status(401).json({ ok: false, error: "Incorrect PIN" });
  });

  app.get("/bff/status", (req, res) => res.json({ locked: true, authed: gateOpen(req) }));

  app.use((req, res, next) => {
    if (req.path === "/bff/login" || req.path === "/bff/status") return next();
    if (gateOpen(req)) return next();
    if (req.path.startsWith("/api")) return res.status(401).json({ error: "locked" });
    // Let the SPA shell load; the frontend's PinGate renders the PIN screen.
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
