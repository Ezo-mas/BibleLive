# BibleLive Security Guide

BibleLive supports optional token-based authentication for operator endpoints.
When enabled, all operator actions (approve, clear, clear queue, settings,
manual display) and reference ingestion from the audio pipeline require
authentication via:

- WebSocket: first message `{"action": "authenticate", "token": "YOUR_TOKEN"}`
  (the deprecated `?token=` query parameter still works)
- HTTP: `Authorization: Bearer YOUR_TOKEN` header

The audio pipeline sends the token with every reference and health report. It reads
`BIBLELIVE_OPERATOR_TOKEN` from the environment or the project `.env` (the same
place the server reads it), or takes `--server-token`.

The pipeline authenticates as the operator: whatever machine runs it holds full
operator rights (approve, clear, settings, display). That is acceptable when the
pipeline runs on the same machine as the server. There is no separate
ingest-only token today — if the pipeline ever moves to another machine, add
one rather than copying the operator token there.

Whether or not auth is enabled, `POST /api/reference` only accepts a canonical
book and a chapter or verse that exists in the loaded Bible text, and ignores
any submitted `text`: what reaches the display is always the database's verse
text.

## Quick Start

1. Generate a secure token:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

2. Add to `.env`:

```bash
BIBLELIVE_REQUIRE_AUTH=true
BIBLELIVE_OPERATOR_TOKEN=your-generated-token-here
```

3. Start the server:

```bash
./start.sh
```

4. Access the operator console at `http://localhost:8000/operator` and enter
   your token when prompted.

## Security Architecture

### Public endpoints (no authentication required)

- `GET /` — root (redirects to the display page)
- `GET /display` — display page
- `GET /healthz` — health check
- `GET /readyz` — readiness check
- `GET /api/ui-language` — the interface language the launcher chose (`lt`, `en`, or null);
  the display page reads it too, and it returns nothing else
- `GET /ws/display` — display WebSocket

### Protected endpoints (authentication required when enabled)

- `GET /operator` — operator console page (the HTML only; its WebSocket and API calls need the token)
- `POST /api/reference` — reference ingestion (from the audio pipeline)
- `POST /api/transcript` — transcript lines from the audio pipeline, used only
  for follow-along suggestions (sermon text; not stored or logged)
- `POST /api/pipeline/heartbeat` — audio pipeline health report (so nobody
  else can tell the operator that detection is healthy)
- `GET /api/pipeline/status` — pipeline health shown in the operator console
- `GET /api/state` — application state
- `POST /api/approve` — approve pending reference
- `POST /api/clear` — clear display
- `POST /api/clear-pending` — clear pending queue
- `GET /api/books` — canonical book list for the operator's passage search
- `GET /api/lookup` — verse text lookup by coordinates for the operator's passage search
- `GET /api/language` — current spoken language
- `POST /api/language` — switch spoken language
- `GET /ws/operator` — operator WebSocket (token in the first message)

## Other Devices on the Network (Windows Launcher)

By default the Windows launcher binds the server to `127.0.0.1` with no token, so
only this PC can reach it. With **Allow other devices on this network** turned on,
the launcher starts the server on all interfaces (`BIBLELIVE_HOST=0.0.0.0`) with
`BIBLELIVE_REQUIRE_AUTH=true`, and gives the audio pipeline it starts the same token
(`BIBLELIVE_OPERATOR_TOKEN`), so its reference posts stay authorized. The launcher's
own connections still use `127.0.0.1`.

- **Token.** The launcher generates a random 12-character token the first time the
  option is turned on, and keeps it in `%APPDATA%\BibleLive\launcher.json` as
  `network_token`. To get a new one, delete `network_token` from that file. Anyone
  with the token has full operator rights (approve, clear, settings, manual display),
  so do not post it or put it in a link you share.
- **Addresses.** Once the server is ready, the launcher shows the addresses for other
  devices: `http://<lan-ip>:<port>/display` (audience screen) and
  `http://<lan-ip>:<port>/operator` (operator console). The LAN IP can change when the
  router hands out a new address, so use the one the launcher shows now. The address is
  the one this PC uses for its default route, so with a VPN on it can be the VPN's address,
  which other devices cannot reach. Turn the VPN off for the service, or use the Wi-Fi
  adapter's IPv4 address from `ipconfig`.
- **QR codes.** The launcher's **Show QR codes** button opens a window with a code for each page. The
  operator code carries the token in its `#token=` fragment, so a phone that scans it
  signs in. Anyone who scans it gets full operator rights: show it only on your own
  screen, never on the projector or the stream.
- **Operator token handoff.** The operator page accepts the token in the URL fragment,
  `/operator#token=<token>`. The browser does not send a fragment to the server, so the
  server never logs it. On load the page takes the fragment out of the address bar and its
  history entry, and uses the token as if it had been typed into the token prompt: it is
  saved in the browser at once, and removed again if the server refuses it. The browser's
  own saved history can still record the address the launcher opened, token included, so
  clear that history on a PC that other people use. The launcher opens the PC's own
  operator page this way. Other devices type the token into the prompt. Do not share a
  link that contains `#token=`.
- **Display pages are view-only and need no token.** `/display`, `/ws/display` and
  `GET /api/ui-language` stay public, so any device that can reach the PC sees what the
  audience screen shows. The Bible lookup endpoints `/api/books` and `/api/lookup` need the
  token like the rest of the operator API (the display never calls them). Operator actions
  and the operator WebSocket still need the token.
- **Windows Firewall.** The installer does not add a rule. With the option on and the
  server ready, the launcher looks for an inbound rule named `BibleLive`. If there is none,
  its network panel shows **Allow through Windows Firewall**. Pressing it runs one elevated
  command (one Windows administrator prompt, the UAC dialog) that adds allow rules for the
  bundled `python.exe` and `pythonw.exe`, for the **Private** network profile only. Public
  networks (cafés, hotels, most guest Wi-Fi) stay blocked even with the option on. To make a
  network Private: Settings, Network & internet, the network's name, Network profile type,
  Private. The launcher asks only when that button is pressed, never at startup. If the
  prompt is declined, the panel says so and nothing changes. Uninstalling removes the rule if
  it exists (also with an administrator prompt; a declined prompt is logged and the
  uninstall continues). A silent uninstall cannot show that prompt, so it leaves the rule
  and logs that; remove it from an administrator command prompt with
  `netsh advfirewall firewall delete rule name=BibleLive`.
- **Windows' own firewall dialog.** The first time `python.exe` listens for connections,
  Windows may show *Windows Defender Firewall has blocked some features of this app*. If
  **Cancel** is pressed there, Windows creates block rules for that program. A block rule
  overrides the allow rule above, so the pages stay unreachable even though the panel shows
  the rule. To fix it, open Windows Defender Firewall, choose *Allow an app or feature
  through Windows Defender Firewall*, and tick **Private** for `python.exe` and `pythonw.exe`
  in the BibleLive `python` folder (*Allow another app* if they are not listed). If the
  pages still do not open, open *Advanced settings*, *Inbound Rules*, and delete or disable
  the rules that block those two programs.
- **Wi-Fi client isolation.** Some routers keep devices on the same Wi-Fi from reaching
  each other (guest networks, some church and hotel networks, mesh systems with "AP
  isolation" or "client isolation"). Then no other device can open the address, whatever
  the PC is set to. Check before relying on it: from a phone on the same Wi-Fi, open the
  `/display` address the launcher shows.
- **Plain HTTP.** The traffic is not encrypted on the local network, so the token and
  operator commands can be read by anyone who can capture the network traffic. Use the
  option only on a password-protected Wi-Fi you control, never on an open network.
- Turn the option off when no other device needs the pages.

## Production Configuration

### Environment Variables

**Required for production:**

```bash
# Authentication
BIBLELIVE_REQUIRE_AUTH=true
BIBLELIVE_OPERATOR_TOKEN=<generate-with-secrets.token_urlsafe(32)>

# Database
BIBLELIVE_DB_PATH=bible_data/biblelive.sqlite
BIBLELIVE_DB_READONLY=true

# Server
BIBLELIVE_HOST=127.0.0.1  # Bind to localhost, use reverse proxy
BIBLELIVE_PORT=8000
BIBLELIVE_RELOAD=false
BIBLELIVE_LOG_LEVEL=INFO

# CORS (only if serving frontend from a different origin)
BIBLELIVE_CORS_ORIGINS=  # Leave empty for same-origin only
```

The full annotated list lives in `.env.example`; deployment walkthroughs
(systemd units, nginx, health checks) live in `DEPLOYMENT.md`.

### Systemd Service

The canonical server unit lives in `DEPLOYMENT.md` ("Systemd Service
(Server)"); use that file so the two docs cannot drift apart. In
particular: `EnvironmentFile=/opt/biblelive/.env`,
`ExecStart=/opt/biblelive/venv/bin/python -m core.server`, and hardening with
`ReadWritePaths=/opt/biblelive/data` (the settings file) while `bible_data/`
stays read-only.

**Create secure environment file (`/opt/biblelive/.env`, as the unit reads):**

```bash
sudo nano /opt/biblelive/.env
```

Add your configuration (`BIBLELIVE_REQUIRE_AUTH`, `BIBLELIVE_OPERATOR_TOKEN`,
`BIBLELIVE_DB_PATH`, `BIBLELIVE_DB_READONLY`, `BIBLELIVE_HOST`, `BIBLELIVE_PORT`)
with `0640` permissions (root-owned, group-readable):

```bash
sudo chown root:biblelive /opt/biblelive/.env
sudo chmod 0640 /opt/biblelive/.env
sudo systemctl daemon-reload
```

### Nginx Configuration with Log Redaction

The operator console sends its token inside the WebSocket, not in the URL, and
the server redacts any `token=` query parameter from its own logs. A client
still using the deprecated `?token=` form puts the token in the URL a proxy
sees, so this configuration keeps query strings out of the proxy's logs too:

```nginx
# Redact query strings from logs
log_format biblelive_safe '$remote_addr - $remote_user [$time_local] '
                          '"$request_method $uri $server_protocol" '
                          '$status $body_bytes_sent';

server {
    listen 443 ssl http2;
    server_name biblelive.example.com;

    ssl_certificate /etc/letsencrypt/live/biblelive.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/biblelive.example.com/privkey.pem;

    access_log /var/log/nginx/biblelive-access.log biblelive_safe;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "Upgrade";
        proxy_read_timeout 86400;
    }
}
```

## Changes that need a security review

A change to any of these needs a security review before it is called done:

- **Authentication and tokens:** `backend/core/server/auth.py`, the WebSocket
  `authenticate` handshake (`core/server/app.py`, `connections.py`), and token
  handling in the clients (`frontend/src/shared/operatorToken.ts`,
  `useWebSocket.ts`, the `#token=` link, `audio/sender.py`).
- **Which endpoints are public or protected:** the lists under "Security
  Architecture" above, and any new route in `core/server/app.py`.
- **Who can reach the server:** `HOST`, `REQUIRE_AUTH` and `CORS_ORIGINS` in
  `core/config.py`; the launcher's network mode, bind address, firewall rule
  and token (`backend/launcher/network.py`, `process.py`, `settings.py`); the
  pipeline's language-control server (`audio/control.py`,
  `core/server/language_control.py`, bound to `127.0.0.1`).
- **Log redaction:** `core/server/log_redaction.py`.

How to run it: in Claude Code, the main session runs the built-in
`/security-review` skill on the branch itself. A subagent without the Skill
tool, or another coding tool, cannot run it. It says in its report that the
security review is still needed, so the user (or the main session) runs it.
Report the findings, or "security review not run". Never drop the step
silently.

## Security Checklist

- [ ] `BIBLELIVE_REQUIRE_AUTH=true` set
- [ ] Strong `BIBLELIVE_OPERATOR_TOKEN` generated (32+ bytes)
- [ ] Token stored in a root-owned file readable by the service group (`0640`)
- [ ] TLS enabled (HTTPS/WSS)
- [ ] Server bound to `127.0.0.1`
- [ ] Database read-only mode enabled
- [ ] Log query strings redacted
- [ ] Rate limiting configured for `/api/reference`
- [ ] Firewall rules in place

---

See [DEPLOYMENT.md](DEPLOYMENT.md) for complete deployment instructions.
