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
  - `AUTH_SECRET=<random string>` — signs the login Bearer tokens **and** the PIN-gate
    cookie. **Set this** (`openssl rand -hex 32`) so they don't use the dev default.
  - `ADMIN_EMAIL=kit.aiss2026@gmail.com` — the account with full Settings + the email
    sender (this is the default; override if needed).
- **Login is email + OTP.** There are no preset accounts. A visitor enters their email,
  receives a 6-digit code (emailed from the admin Gmail), and enters it; the first time
  is registration. Accounts are stored in `data/accounts.json` (gitignored). **Settings**
  is open to everyone for connecting their own **Strava / Garmin / Google Calendar**; the
  rest (LLM keys, Telegram, restart, the email Google token) is **admin-only** (`ADMIN_EMAIL`).
- **Connect the admin email sender once, on the mini** (powers OTP login email). Run the
  CLI signed in as **kit.aiss2026@gmail.com**:
  ```bash
  python auth/google_oauth.py        # writes .tokens/google_mail.json (gmail.send)
  ```
  **Enable the Gmail API** (and Calendar API) in the Cloud project (console → APIs &
  Services → Library), and keep the redirect URI
  `http://localhost:8000/api/settings/google/callback` registered.
  - **Two separate tokens, deliberately.** This CLI token (`google_mail.json`, `gmail.send`)
    is the email sender. The **calendar** integration is a *different* token
    (`google.json`, calendar scopes only) connected in-app from Settings — by the admin or
    any user. Keeping them apart means a user reconnecting Calendar can't break login email.
    (On an existing deployment whose old `google.json` still had `gmail.send`, it's copied to
    `google_mail.json` automatically on first send.)
  - Don't connect Google from a remote browser — the localhost redirect only resolves on
    the mini.
- **First-run bootstrap (chicken-and-egg).** OTP email needs `google_mail.json` present, but
  the in-app connect needs you logged in (which needs an email). Break the loop: run
  `python auth/google_oauth.py` **before** first login (recommended), or start once with
  `OTP_DEV_ECHO=1` so codes print to the server log (`/tmp/fitdash_api.log`) — log in as the
  admin, then restart **without** `OTP_DEV_ECHO`. Never leave it on for a public URL.
- **Strava / Garmin / Calendar**: any logged-in user can connect these from Settings; tokens
  live in `.tokens/` (gitignored) and are shared across users.

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
- `DO_LOCK=true APP_PIN='a-long-passphrase' AUTH_SECRET=<random> ./serve.sh` — enable the
  shared PIN gate in front of the whole app (see Security). Use a long passphrase, not a
  4-digit PIN, and set `AUTH_SECRET` so sessions survive restarts.

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

Then publish the BFF — either as part of the launcher or standalone:
```bash
FUNNEL=1 ./serve.sh      # serve.sh starts the app AND the Funnel in one go
# …or, if the app is already running:
tailscale funnel 3000
```
With `FUNNEL=1`, `serve.sh` runs `tailscale funnel --bg 3000` for you (and tears it
down on Ctrl-C). It **pre-provisions** the HTTPS cert, **verifies** the public URL
serves a trusted cert (a real `curl` handshake), and prints your public URL — a
**stable** hostname like `https://macmini.<your-tailnet>.ts.net` that does **not**
change across restarts. The SPA's same-origin `/api` calls (incl. the chat SSE stream)
work unchanged because Funnel forwards everything to `127.0.0.1:3000`.

> **Share the bare hostname only — no port.** The valid Let's Encrypt cert covers the
> `*.ts.net` **name on 443**. Funnel maps public `https://<name>/` → local `:3000`, so
> the link to give people is exactly `https://<name>/` with **no `:3000`**. A link that
> still has `:3000` (or an IP address) hits plain HTTP / a name the cert doesn't cover,
> which is what makes a browser show "your connection is not private / proceed anyway".
> The launcher prints the correct URL in a box labelled *SHARE THIS URL*.

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

Login is **email + OTP**: a real per-user identity (a code emailed to an address the
person controls); users can connect their own Strava/Garmin/Calendar in Settings, while
the privileged Settings cards stay `ADMIN_EMAIL`-only. That's the primary auth.
Registration is **open** — anyone who can receive an OTP can create an account — so on a
public URL you still want the **shared PIN gate** in front as a coarse first wall (it
limits who can even reach the login screen). The gate is hardened and safe to expose:

**Turn it on** (the only secure way to run a public URL):
```bash
DO_LOCK=true APP_PIN='choose-a-long-passphrase' AUTH_SECRET="$(openssl rand -hex 32)" ./serve.sh
```
(or set those three in `deploy/com.fitdash.serve.plist` for the autostart service).

What the gate does:
- A visitor must enter the PIN before the SPA loads any data or `/api` responds. After
  success they get a **signed, HMAC-protected session cookie** (keyed by `AUTH_SECRET`) —
  it can't be forged by setting a constant value, and it carries a server-checked expiry.
- `/bff/login` is **rate-limited with per-IP lockout** (5 tries, then a 15-min lockout
  that doubles on repeat) plus a per-attempt delay, so the PIN can't be brute-forced. The
  PIN is compared in constant time.
- Behind the tunnel the real client IP (`X-Forwarded-For`) drives the limiter and the
  cookie is marked `Secure` over HTTPS.

Use a **long passphrase**, not a 4-digit PIN — that's the one thing the rate-limit can't
fix. Share it with your five users out-of-band.

Other notes:
- **Even stronger: keep it private.** If only your teammates need it, skip Funnel and use
  plain Tailscale — no public login surface at all. The PIN gate is for when you genuinely
  want a public URL.
- Don't expose MLflow (`:5001`), FastAPI (`:8000`), or the agent/MCP ports — `serve.sh`
  keeps them on localhost; only the BFF is fronted.

## Troubleshooting

- **Blank page / "SPA not built":** run `./serve.sh` without `SKIP_BUILD`, or
  `cd web && npm run build`.
- **Chat hangs:** the orchestrator (`:9000`) or a specialist isn't up — check
  `/tmp/agent_*.log`. The BFF proxies SSE with no timeout, so a hang is upstream.
- **Google "redirect_uri_mismatch" when a remote user clicks Connect:** expected —
  connect Google **on the mini** only (step 1).
- **Browser says "not secure / proceed anyway" on the `.ts.net` URL:** the cert is fine
  (Tailscale issues a real Let's Encrypt cert for the bare name on 443) — the link being
  used has a **`:3000`** on it or is an **IP**, both of which the cert doesn't cover.
  Share the exact `https://<name>/` the launcher prints (no port). If the *bare* name
  truly warns, the cert hasn't provisioned: admin console → **DNS → enable MagicDNS +
  "HTTPS Certificates"**, then re-run with `FUNNEL=1` (it pre-provisions and verifies).
  A one-off first-visit hiccup can also be the ~30s issuance window — reload after a
  moment. And a *client*-side clock that's wrong will reject any cert as out-of-date.
- **Everything dies when you close Terminal:** you didn't install the launchd job —
  see step 4 (or run under `tmux`/`caffeinate -s`).
