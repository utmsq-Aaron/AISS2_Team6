# Serving FitDash from a Mac mini (public web access)

Goal: run FitDash on a Mac mini so anyone can open it in a browser, via a public
HTTPS URL — without opening router ports or touching the macOS firewall.

## How it fits together

```
  the internet ──HTTPS──▶ Cloudflare Tunnel ──▶ 127.0.0.1:3000  (Node BFF)
                                                   │  serves the built React SPA
                                                   │  proxies /api  (same-origin, SSE-safe)
                                                   ▼
                                              127.0.0.1:8000  FastAPI
                                                   ▼
                          agents :9000–9005  ·  MCP servers :8101–8107  ·  MLflow :5001
```

Only **one** local port (the BFF, `127.0.0.1:3000`) is fronted. Everything else stays
on localhost. The tunnel makes an **outbound** connection to Cloudflare, so there is
nothing to forward and no inbound firewall rule to add.

`serve.sh` builds the SPA and starts the whole backend + BFF in one command.

---

## 1. One-time prep on the Mac mini

- **Python env + deps:** the `aiss` conda env with `pip install -r requirements.txt`
  (same as dev). Note its python path, e.g. `/opt/miniconda3/envs/aiss/bin/python3`.
- **Node 18+** (`brew install node`).
- **`.env`** filled in (LLM keys, Strava/Garmin/Google client creds, etc.). For a
  public deployment also set:
  - `AUTH_SECRET=<random string>` — signs the login Bearer tokens. **Set this** so
    tokens aren't signed with the dev default. Generate one: `openssl rand -hex 32`.
- **Connect Google once, from the mini itself.** The OAuth redirect is
  `http://localhost:8000/api/settings/google/callback`, which only resolves on the
  Mac mini. So open the app **on the mini** (`http://localhost:3000`) → Settings →
  Connect Google. Remote users then share that one connection (you chose
  identity-only, shared data). Don't try to connect Google from a remote browser —
  the localhost redirect won't reach the mini.
- Same idea for **Strava/Garmin**: do those connects on the mini once. Tokens live in
  `.tokens/` (gitignored) and are reused.

## 2. Build + run

```bash
cd /path/to/AISS2_Team6
./serve.sh                       # builds web/dist, starts everything, BFF on 127.0.0.1:3000
```

Verify locally on the mini: open `http://localhost:3000`, log in with a name
(Marvin/Max/Lorenz/Aaron/Simon), ask a question.

Useful flags:
- `SKIP_BUILD=1 ./serve.sh` — reuse an existing `web/dist` (fast restarts).
- `HOST=0.0.0.0 ./serve.sh` — also reachable directly on the LAN at
  `http://<mac-ip>:3000` (only do this on a trusted network).
- `DO_LOCK=true APP_PIN=1234 ./serve.sh` — enable the BFF's shared PIN gate. ⚠ needs a
  frontend PIN screen first (see Security) — don't enable it on a public URL yet.

## 3. Make it public with Tailscale Funnel (recommended)

Tailscale Funnel gives you a **stable, free, public HTTPS URL** under `*.ts.net` — no
domain to buy, no router/firewall changes, certificates handled for you.

Install + sign in once:
```bash
brew install tailscale          # or the macOS app from tailscale.com/download
sudo tailscale up               # log in (free Personal plan is enough)
```

One-time admin-console setup (https://login.tailscale.com/admin):
1. **DNS → enable MagicDNS** and **enable HTTPS certificates**.
2. **Access controls (ACLs)** → grant this node the Funnel attribute. Add to the policy:
   ```jsonc
   "nodeAttrs": [
     { "target": ["autogroup:member"], "attr": ["funnel"] }
   ]
   ```
   (or scope `target` to just the mini's tag/host.)

Then publish the BFF:
```bash
tailscale funnel 3000
```
It prints your public URL — a **stable** hostname like
`https://macmini.<your-tailnet>.ts.net` that does **not** change across restarts.
Share that. The SPA's same-origin `/api` calls (incl. the chat SSE stream) work
unchanged because Funnel forwards everything to `127.0.0.1:3000`.

Rename for a cleaner URL (optional): change the **machine name** (admin console → the
device → rename) and/or the **tailnet name** (Settings → General) so it reads e.g.
`https://fitdash.marvin.ts.net`. It's always under `.ts.net` — a truly custom domain
is the only thing that costs money.

Make it survive reboots — run Funnel as a background service:
```bash
sudo tailscale funnel --bg 3000     # persists; `tailscale funnel status` to check, `--https=off`… to stop
```

> **Private instead of public?** If only your five teammates need it, skip Funnel and
> use plain Tailscale: each teammate installs Tailscale and joins your tailnet, then
> reaches the mini at `http://macmini.<tailnet>.ts.net:3000` (or `:443` via `tailscale
> serve`). Same stable hostname, **zero public exposure** — the safest option, and it
> sidesteps the login weakness below entirely.

> **Alternative — Cloudflare Tunnel.** `brew install cloudflared` then
> `cloudflared tunnel --url http://127.0.0.1:3000` for a throwaway
> `*.trycloudflare.com` URL (changes each run), or a named tunnel on your own domain
> for a fixed hostname. Good if you already use Cloudflare; otherwise Funnel is simpler.

## 4. Keep it running (autostart + no sleep)

**Don't let the mini sleep** (it would drop the tunnel and stop the agents):
```bash
sudo pmset -a sleep 0 disksleep 0      # never sleep the machine/disk
# (display can still sleep: `sudo pmset -a displaysleep 10`)
```

**Autostart on boot/crash** via the included launchd job:
1. Build once: `./serve.sh` (or `SKIP_BUILD=0` once) so `web/dist` exists.
2. Edit `deploy/com.fitdash.serve.plist`, replacing:
   - `__REPO__` → absolute repo path (e.g. `/Users/you/.../AISS2_Team6`)
   - `__PY__` → your conda python (e.g. `/opt/miniconda3/envs/aiss/bin/python3`)
   - `__CONDA_BIN__` → its directory (e.g. `/opt/miniconda3/envs/aiss/bin`)
3. Install it:
   ```bash
   cp deploy/com.fitdash.serve.plist ~/Library/LaunchAgents/
   launchctl load -w ~/Library/LaunchAgents/com.fitdash.serve.plist
   ```
   Logs: `/tmp/fitdash.serve.out` / `.err`. Stop: `launchctl unload -w ~/Library/LaunchAgents/com.fitdash.serve.plist`.

Run the tunnel as a service too so the public URL survives reboots:
`sudo tailscale funnel --bg 3000` (Tailscale), or `sudo cloudflared service install`
(Cloudflare).

## 5. Security — read before sharing the URL

This is a **prototype auth**: logging in is just typing one of five known names — no
password. Anyone with the public URL can sign in as any teammate. That's fine inside a
trusted group; it is **not** safe for a truly open audience. Mitigations:

- **Set `AUTH_SECRET`** (step 1) so tokens use a real signing key.
- **Prefer private over public.** The strongest, simplest option: skip Funnel and keep
  it **private on Tailscale** (teammates join your tailnet). Only they can reach it; the
  weak login never faces the open internet.
- **Shared PIN gate (needs a small fix first).** The BFF can require a shared PIN before
  serving anything (`DO_LOCK=true APP_PIN=<pin>`), but the React app currently has **no
  screen to enter that PIN**, so turning it on today locks everyone out. Ask to have the
  PIN entry screen wired into the SPA before relying on this for a public Funnel URL.
- Don't expose MLflow (`:5001`), FastAPI (`:8000`), or the agent/MCP ports — `serve.sh`
  keeps them on localhost; only front the BFF.

## Troubleshooting

- **Blank page / "SPA not built":** run `./serve.sh` without `SKIP_BUILD`, or
  `cd web && npm run build`.
- **Chat hangs:** the orchestrator (`:9000`) or a specialist isn't up — check
  `/tmp/agent_*.log`. The BFF proxies SSE with no timeout, so a hang is upstream.
- **Google "redirect_uri_mismatch" when a remote user clicks Connect:** expected —
  connect Google **on the mini** only (step 1).
- **Everything dies when you close Terminal:** you didn't install the launchd job —
  see step 4 (or run under `tmux`/`caffeinate -s`).
