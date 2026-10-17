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
- `GET /api/books` — canonical book list (read-only canon data)
- `GET /api/lookup` — verse text lookup by coordinates (read-only)
- `GET /ws/display` — display WebSocket

### Protected endpoints (authentication required when enabled)

- `GET /operator` — operator console page
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
- `GET /api/language` — current spoken language
- `POST /api/language` — switch spoken language
- `GET /ws/operator` — operator WebSocket (token in the first message)

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
