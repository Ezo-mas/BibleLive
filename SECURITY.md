# BibleLive Security Guide

BibleLive supports optional token-based authentication for operator endpoints.
When enabled, all operator actions (approve, clear, clear queue, settings,
manual display) require authentication via:

- WebSocket: query parameter `?token=YOUR_TOKEN`
- HTTP: `Authorization: Bearer YOUR_TOKEN` header

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
- `POST /api/reference` — reference ingestion (from the audio pipeline)
- `GET /api/books` — canonical book list (read-only canon data)
- `GET /api/lookup` — verse text lookup by coordinates (read-only)
- `GET /ws/display` — display WebSocket

### Protected endpoints (authentication required when enabled)

- `GET /operator` — operator console page
- `GET /api/state` — application state
- `POST /api/approve` — approve pending reference
- `POST /api/clear` — clear display
- `POST /api/clear-pending` — clear pending queue
- `GET /ws/operator` — operator WebSocket (requires `?token=...`)

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

Create `/etc/systemd/system/biblelive.service`:

```ini
[Unit]
Description=BibleLive Display System
After=network.target

[Service]
Type=simple
User=biblelive
Group=biblelive
WorkingDirectory=/opt/biblelive
EnvironmentFile=/etc/biblelive/biblelive.env
Environment=PYTHONPATH=/opt/biblelive/backend
ExecStart=/opt/biblelive/venv/bin/python -m core.server

# Security hardening
PrivateTmp=yes
NoNewPrivileges=yes
ReadWritePaths=/opt/biblelive/bible_data
ReadOnlyPaths=/opt/biblelive

Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Create secure environment file:**

```bash
sudo mkdir -p /etc/biblelive
sudo nano /etc/biblelive/biblelive.env
```

Add your configuration (`BIBLELIVE_REQUIRE_AUTH`, `BIBLELIVE_OPERATOR_TOKEN`,
`BIBLELIVE_DB_PATH`, `BIBLELIVE_DB_READONLY`, `BIBLELIVE_HOST`, `BIBLELIVE_PORT`)
with `0600` permissions:

```bash
sudo chown biblelive:biblelive /etc/biblelive/biblelive.env
sudo chmod 0600 /etc/biblelive/biblelive.env
sudo systemctl daemon-reload
```

### Nginx Configuration with Log Redaction

**Important:** WebSocket tokens appear in URLs. This configuration redacts
query strings from logs:

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
- [ ] Token stored securely with `0600` permissions
- [ ] TLS enabled (HTTPS/WSS)
- [ ] Server bound to `127.0.0.1`
- [ ] Database read-only mode enabled
- [ ] Log query strings redacted
- [ ] Rate limiting configured for `/api/reference`
- [ ] Firewall rules in place

---

See [DEPLOYMENT.md](DEPLOYMENT.md) for complete deployment instructions.
