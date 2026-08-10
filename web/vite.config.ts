import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..");

// FASTAPI_PORT comes from core/config.py — the single source of truth for every
// port in the app (see scripts/export_ports.py) — read live so this can never
// drift from what the launcher scripts actually start FastAPI on. Falls back to
// the documented default if Python isn't reachable (e.g. `npm run dev` run
// standalone, outside the repo's launcher scripts) — degrades gracefully rather
// than failing the dev server, matching how the rest of the app treats missing
// tooling as "skipped, never fatal".
function fastApiPort(): number {
  try {
    const py = process.env.PY || "python3";
    const out = execFileSync(py, [path.join(repoRoot, "scripts", "export_ports.py"), "--format", "json"], {
      cwd: repoRoot,
      encoding: "utf-8",
    });
    const port = JSON.parse(out).FASTAPI_PORT;
    if (!Number.isInteger(port)) throw new Error(`unexpected FASTAPI_PORT: ${port}`);
    return port;
  } catch (err) {
    console.warn(`[vite] could not read FASTAPI_PORT from core/config.py (${err}) — falling back to 8000`);
    return 8000;
  }
}

// Dev server proxies /api to the FastAPI backend so the browser talks to a single
// origin (mirrors how the Node BFF serves things in production).
export default defineConfig({
  plugins: [react()],
  // Read .env from the REPO ROOT, not web/. The app has exactly one .env (the one
  // .env.example documents), and the VITE_* flags live in it alongside everything
  // else. Without this Vite would look in web/ and silently ignore them — which is
  // why the old launcher scripts had to export them by hand. Only VITE_-prefixed
  // variables are ever exposed to client code, so pointing at the root .env does
  // not leak API keys into the bundle.
  envDir: repoRoot,
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: `http://127.0.0.1:${fastApiPort()}`,
        changeOrigin: true,
        // SSE: keep the connection open and unbuffered
        configure: (proxy) => {
          proxy.on("proxyReq", (proxyReq) => proxyReq.setHeader("Accept-Encoding", "identity"));
        },
      },
    },
  },
});
