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
- `DO_LOCK=true APP_PIN=1234 ./serve.sh` — add a shared PIN gate (see Security).

## 3. Make it public with a Cloudflare Tunnel

Install once: `brew install cloudflared`.

**Quick (throwaway URL, zero account):**
```bash
cloudflared tunnel --url http://127.0.0.1:3000
```
It prints a `https://<random>.trycloudflare.com` URL — share that. Good for a demo;
the URL changes each run and there's no uptime guarantee.

**Stable (your own subdomain, needs a free Cloudflare account + a domain on it):**
```bash
cloudflared tunnel login
cloudflared tunnel create fitdash
# map a hostname to the local BFF:
cloudflared tunnel route dns fitdash fitdash.example.com
cloudflared tunnel --hostname fitdash.example.com --url http://127.0.0.1:3000
# (or run it as a managed service: `sudo cloudflared service install` with a config.yml)
```

Cloudflare terminates TLS, so users get HTTPS for free and the SPA's same-origin
`/api` calls (including the chat SSE stream) work unchanged.

> Alternatives: **Tailscale Funnel** (`tailscale funnel 3000`) gives an HTTPS
> `*.ts.net` URL with similar simplicity; **ngrok** also works. Cloudflare Tunnel is
> the most "set-and-forget" for a fixed domain.

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

Run `cloudflared` as a service too (`sudo cloudflared service install`) so the public
URL also survives reboots.

## 5. Security — read before sharing the URL

This is a **prototype auth**: logging in is just typing one of five known names — no
password. Anyone with the public URL can sign in as any teammate. That's fine inside a
trusted group; it is **not** safe for a truly open audience. Mitigations:

- **Set `AUTH_SECRET`** (step 1) so tokens use a real signing key.
- **Add a shared PIN gate** in front of the whole app: run with `DO_LOCK=true
  APP_PIN=<pin>` (or the plist env). The BFF then requires the PIN before serving the
  SPA or proxying `/api`. Share the PIN out-of-band with your five users.
- **Or keep it private**: instead of a public tunnel, put the mini on a **Tailscale**
  network and have your teammates join it — then it's reachable only by them, no
  public exposure at all (`http://<mini>.<tailnet>.ts.net:3000`).
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
