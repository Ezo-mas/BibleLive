# BibleLive Deployment Guide

This guide covers deploying BibleLive in production environments.

## Quick Start (Production)

```bash
# 1. Clone and enter repository
git clone <your-repo-url> biblelive
cd biblelive

# 2. Set up environment
cp .env.example .env
nano .env  # Configure production settings (see below)

# 3. Install Python dependencies
python3 -m venv venv
source venv/bin/activate
# -c holds every package to the tested versions (see backend/constraints.txt)
pip install -e ./backend -c backend/constraints.txt

# 4. Import Bible database
PYTHONPATH="$PWD/backend" python -m core.import_bible --translation kjv --source data/kjv2.json

# 5. Build frontend
cd frontend
npm install
npm run build
cd ..

# 6. Test the deployment
PYTHONPATH="$PWD/backend" python -m core.server
# Visit http://localhost:8000/readyz

# 7. Set up systemd service (see below)
```

---

## Configuration

### Required Environment Variables

Edit `.env` file:

```bash
# Server
BIBLELIVE_HOST=127.0.0.1  # Use 127.0.0.1 if behind reverse proxy
BIBLELIVE_PORT=8000
BIBLELIVE_RELOAD=false    # MUST be false in production

# Authentication (CRITICAL for production)
BIBLELIVE_REQUIRE_AUTH=true
BIBLELIVE_OPERATOR_TOKEN=<generate-secure-token>

# Database
BIBLELIVE_DB_PATH=bible_data/biblelive.sqlite
BIBLELIVE_DB_READONLY=true

# Logging
BIBLELIVE_LOG_LEVEL=INFO

# Spoken-language switch in the operator console (optional). Only needed when
# the audio pipeline's control API is not on its default address, or when the
# pipeline runs with --control-token. Keep the pipeline's control port on
# loopback; the server forwards to it so browsers never have to.
# BIBLELIVE_AUDIO_CONTROL_URL=http://127.0.0.1:8001
# BIBLELIVE_AUDIO_CONTROL_TOKEN=<same value as the pipeline's --control-token>

# Bible version switching (optional). Translations are the *.sqlite files next to
# BIBLELIVE_DB_PATH. By default a spoken-language switch also selects that
# language's translation; set false to leave the version to the operator.
# BIBLELIVE_AUTO_BIBLE_VERSION=true
# BIBLELIVE_LANGUAGE_BIBLE_VERSIONS={"en":"kjv","lt":"ltb"}
# Follow-along next-verse suggestions for the operator (on by default).
# BIBLELIVE_FOLLOW_ALONG=true
```

### Generate Secure Token

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Copy the output to `BIBLELIVE_OPERATOR_TOKEN` in your `.env` file.

---

## Authentication

BibleLive supports simple token-based authentication for operator controls.

### How It Works

- **Display endpoints** (`/display`, `/ws/display`): Always public (read-only)
- **Operator page** (`/operator`): the HTML is served without a token. Its WebSocket (`/ws/operator`) and API calls need the token when `REQUIRE_AUTH=true`
- **Operator API** (`/api/reference`, `/api/transcript`, `/api/approve`, `/api/clear`, `/api/clear-pending`, and the other operator endpoints): Protected when `REQUIRE_AUTH=true`

### Frontend Integration

When authentication is enabled, the operator frontend needs to include the token:

**HTTP API Requests:**
```javascript
fetch('/api/approve', {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${token}`,
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({ reference_id: '...' })
})
```

**WebSocket Connection:**
```javascript
const ws = new WebSocket('ws://localhost:8000/ws/operator')
// The token is the first message, not part of the URL (URLs get logged).
ws.onopen = () => ws.send(JSON.stringify({ action: 'authenticate', token }))
```

The server closes the socket with code 1008 if the token is wrong or does not
arrive within 10 seconds. `?token=` in the URL still works but is deprecated.

### Token Storage

The operator should prompt for the token on first use and store it:
- In `localStorage` for web deployments
- In secure storage for desktop apps

**Security Note:** Tokens are transmitted in headers/query params. Always use HTTPS/WSS in production.

---

## Other Devices on the Network (Windows PC)

On a Windows PC running the launcher, another device on the same Wi-Fi (a laptop,
phone or tablet) can open the display and operator pages when **Allow other devices on
this network** is turned on. What the option changes, the token and the firewall rule
are described in
[SECURITY.md](SECURITY.md#other-devices-on-the-network-windows-launcher). Setup:

1. Make the Wi-Fi a **Private** network in Windows (Settings, Network & internet, the
   network's name, Network profile type, Private). The firewall rule covers Private
   networks only.
2. Turn on **Allow other devices on this network** in the launcher and let the server
   start. The launcher window then shows the display and operator addresses and the
   operator token, each with a **Copy** button.
3. If the window shows **Allow through Windows Firewall**, press it and accept the Windows
   administrator prompt (once). The installer does not add the rule; the launcher adds it
   on request, and the uninstaller removes it (a silent uninstall leaves it; see SECURITY.md).
4. On the other device, open `http://<lan-ip>:8000/display` (port 8000 unless you chose
   another port in the launcher) for the audience page, which needs no token. For the
   operator console, open `http://<lan-ip>:8000/operator` and enter the token shown in the
   launcher (the same value as `network_token` in `%APPDATA%\BibleLive\launcher.json`).
   The launcher can also show both addresses as QR codes (press **Show QR codes**, which
   opens a small window of its own): a phone scans the display code, or the operator code
   to sign in without typing the token. Show the operator code only on your own screen,
   never on the projector or the stream.
5. Before the service, check that a phone on the same Wi-Fi opens the display page. Many
   routers keep devices from reaching each other; on such a network this setup cannot work.

If a VPN is on, the launcher can show the VPN's address, which other devices cannot reach.
Turn the VPN off for the service, or use the Wi-Fi adapter's IPv4 address from `ipconfig`.

---

## Reverse Proxy Setup

BibleLive should be deployed behind a reverse proxy with TLS termination.

### Nginx Configuration

Create `/etc/nginx/sites-available/biblelive`:

```nginx
# HTTP redirect to HTTPS
server {
    listen 80;
    server_name biblelive.yourdomain.com;
    return 301 https://$server_name$request_uri;
}

# HTTPS server
server {
    listen 443 ssl http2;
    server_name biblelive.yourdomain.com;

    # TLS configuration
    ssl_certificate /etc/letsencrypt/live/biblelive.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/biblelive.yourdomain.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    # Logging with token redaction
    # Use custom log format that excludes query strings
    log_format biblelive_secure '$remote_addr - $remote_user [$time_local] '
                                '"$request_method $uri $server_protocol" '
                                '$status $body_bytes_sent '
                                '"$http_referer" "$http_user_agent"';
    access_log /var/log/nginx/biblelive-access.log biblelive_secure;
    error_log /var/log/nginx/biblelive-error.log;

    # Proxy settings
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # WebSocket support
    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSocket timeouts
        proxy_read_timeout 86400;
        proxy_send_timeout 86400;
    }

    # Static files (optional caching)
    location /static/ {
        proxy_pass http://127.0.0.1:8000;
        expires 7d;
        add_header Cache-Control "public, immutable";
    }

    # Health checks (no auth required)
    location /healthz {
        proxy_pass http://127.0.0.1:8000;
        access_log off;
    }
}
```

Enable the site:
```bash
sudo ln -s /etc/nginx/sites-available/biblelive /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### Caddy Configuration (Alternative)

**Note:** The `rate_limit` directive requires a third-party plugin not included in standard Caddy builds. Remove it or install the plugin separately.

Caddy automatically handles HTTPS with Let's Encrypt:

```caddyfile
biblelive.yourdomain.com {
    reverse_proxy localhost:8000 {
        # WebSocket support is automatic
    }
    
    # Optional: rate limiting (requires plugin)
    # rate_limit {
    #     zone api {
    #         key {remote_host}
    #         events 100
    #         window 1m
    #     }
    # }
}
```

---

## Audio Pipeline Service

The BibleLive server receives references from the audio pipeline. Deploy the audio service separately:

```ini
[Unit]
Description=BibleLive Audio Pipeline
After=network.target pulseaudio.service
Requires=biblelive.service

[Service]
Type=simple
User=biblelive
Group=biblelive
WorkingDirectory=/opt/biblelive
EnvironmentFile=/opt/biblelive/.env
Environment=PYTHONPATH=/opt/biblelive/backend
ExecStart=/opt/biblelive/venv/bin/python -m audio.cli
Restart=always
RestartSec=10

# Audio access
SupplementaryGroups=audio

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable biblelive-audio.service
sudo systemctl start biblelive-audio.service
sudo systemctl status biblelive-audio.service
```

**Note:** The audio pipeline user needs access to audio devices. Add to the `audio` group or configure PulseAudio/PipeWire permissions accordingly.

---

## Systemd Service (Server)

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
Environment="PATH=/opt/biblelive/venv/bin"
EnvironmentFile=/opt/biblelive/.env
Environment=PYTHONPATH=/opt/biblelive/backend
ExecStart=/opt/biblelive/venv/bin/python -m core.server

# Security hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
# Writable: data/ holds server_settings.json (auto-approve persistence), a
# server_settings.damaged-*.json copy if that file is ever damaged, and the
# idle-screen picture (idle_screen.png/.jpg/.webp), uploaded from the operator console.
# bible_data/ stays read-only under strict (the DB opens read-only); do not
# add it here.
ReadWritePaths=/opt/biblelive/data

# Restart policy
Restart=on-failure
RestartSec=5s

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=biblelive

[Install]
WantedBy=multi-user.target
```

### Create Service User

```bash
sudo useradd -r -s /bin/false -d /opt/biblelive biblelive
sudo mkdir -p /opt/biblelive
sudo chown biblelive:biblelive /opt/biblelive
```

### Deploy Application

```bash
# Copy application files (excluding .env)
sudo cp -r /path/to/biblelive/* /opt/biblelive/
sudo chown -R biblelive:biblelive /opt/biblelive

# Create production environment file
sudo tee /opt/biblelive/.env > /dev/null << 'EOF'
BIBLELIVE_DB_PATH=/opt/biblelive/bible_data/biblelive.sqlite
BIBLELIVE_PORT=8000
BIBLELIVE_HOST=127.0.0.1
BIBLELIVE_REQUIRE_AUTH=true
BIBLELIVE_OPERATOR_TOKEN=your-secure-token-here
EOF

# Set secure permissions: root owns the secrets, the service group can read
# them, and the service itself cannot rewrite them.
sudo chown root:biblelive /opt/biblelive/.env
sudo chmod 0640 /opt/biblelive/.env

# Set up Python environment
sudo -u biblelive python3 -m venv /opt/biblelive/venv
sudo -u biblelive /opt/biblelive/venv/bin/pip install -e /opt/biblelive/backend \
    -c /opt/biblelive/backend/constraints.txt

# Import Bible data
sudo -u biblelive env PYTHONPATH=/opt/biblelive/backend \
  /opt/biblelive/venv/bin/python -m core.import_bible \
    --translation kjv --source /opt/biblelive/data/kjv2.json

# Build frontend
cd /opt/biblelive/frontend
sudo -u biblelive npm install
sudo -u biblelive npm run build
```

> After every `git pull`, re-run `npm run build` and restart the service:
> `static/frontend/` is generated and gitignored, so the served bundle goes
> stale otherwise. `/readyz` only checks the files exist; `start.sh` /
> `start.ps1` warn when `frontend/src` is newer than the bundle.

### Enable and Start Service

```bash
sudo systemctl daemon-reload
sudo systemctl enable biblelive
sudo systemctl start biblelive
sudo systemctl status biblelive
```

### View Logs

```bash
# Follow logs
sudo journalctl -u biblelive -f

# Last 100 lines
sudo journalctl -u biblelive -n 100

# Errors only
sudo journalctl -u biblelive -p err
```

---

## Health Checks

BibleLive provides two health endpoints:

### `/healthz` - Liveness Check

Returns 200 if the server process is running.

```bash
curl http://localhost:8000/healthz
# {"status":"healthy"}
```

Use for:
- Container orchestration liveness probes
- Simple uptime monitoring

### `/readyz` - Readiness Check

Returns 200 if the server is ready to accept requests (database file exists, application initialized).

```bash
curl http://localhost:8000/readyz
# {"status":"ready","checks":{"server":"ok","database":"ok","frontend":"ok"}}
```

Returns 503 if not ready:
```json
{
  "detail": {
    "status": "not_ready",
    "checks": {
      "server": "ok",
      "database": "error",
      "frontend": "not_built"
    }
  }
}
```

Use for:
- Container orchestration readiness probes
- Load balancer health checks
- Startup validation

---

## Security Checklist

- [ ] `BIBLELIVE_REQUIRE_AUTH=true` in production
- [ ] Strong random `BIBLELIVE_OPERATOR_TOKEN` generated and set
- [ ] `BIBLELIVE_RELOAD=false` in production
- [ ] TLS enabled on reverse proxy (HTTPS/WSS)
- [ ] Firewall allows only reverse proxy to access port 8000
- [ ] Windows PC: "Allow other devices on this network" off unless other devices need the pages, and the network is Private
- [ ] Service runs as non-root user with minimal permissions
- [ ] Database file has appropriate permissions (readable/writable only by service user)
- [ ] `.env` file owned by root, readable by the service group (`chown root:biblelive`, `chmod 640 .env`)
- [ ] Operator tokens not committed to version control
- [ ] CORS configured restrictively (no wildcard `*` in production)
- [ ] Regular security updates applied to OS and dependencies
- [ ] Logs monitored for unauthorized access attempts

---

## Troubleshooting

### Server Won't Start

Check logs:
```bash
sudo journalctl -u biblelive -n 50
```

Common issues:
- Port 8000 already in use: `sudo lsof -i :8000`
- Port 8001 already in use (audio pipeline's language-control server, loopback
  only): `sudo lsof -i :8001`, or run the pipeline with `--control-port PORT`
  / `--no-control`
- Database not found: Run import command
- Frontend not built: `cd frontend && npm run build`
- Missing dependencies: `pip install -e ./backend -c backend/constraints.txt`

### WebSocket Connection Fails

- Check reverse proxy WebSocket configuration
- Verify firewall allows WebSocket upgrades
- Test direct connection: `wscat -c ws://localhost:8000/ws/display`
- Check for authentication issues (operator connections)

### Authentication Errors

- Verify `BIBLELIVE_OPERATOR_TOKEN` is set in `.env`
- Check token in frontend localStorage/code
- Test API with curl:
  ```bash
  curl -H "Authorization: Bearer YOUR_TOKEN" \
       -X POST http://localhost:8000/api/approve \
       -H "Content-Type: application/json" \
       -d '{"reference_id":"..."}'
  ```

### Other Devices Cannot Open the Pages (Windows PC)

- The network is **Public**: set it to Private (Settings, Network & internet, the network,
  Network profile type). The firewall rule does not cover Public networks.
- **Allow other devices on this network** is off: the server listens only on `127.0.0.1`.
- The firewall rule is missing: the launcher's network panel shows **Allow through Windows
  Firewall**. Press it and accept the administrator prompt. To check from a command prompt,
  `netsh advfirewall firewall show rule name=BibleLive` prints "No rules match" when it is
  missing.
- Windows blocked the program: if the firewall dialog that appears when `python.exe` first
  listens was cancelled, Windows keeps block rules for `python.exe` and `pythonw.exe`, and
  they override the launcher's rule. Allow both in Windows Defender Firewall (Allow an app or
  feature through Windows Defender Firewall, Private), or delete their block rules under
  Advanced settings, Inbound Rules. SECURITY.md has the steps.
- Client isolation: a phone on the same Wi-Fi cannot open the page either. Use a network
  without isolation (see SECURITY.md).
- The address changed: the router may have given the PC a new LAN IP. Use the address the
  launcher shows now.
- The launcher shows a VPN's address (or none): other devices cannot reach it. Turn the VPN
  off for the service, or use the Wi-Fi adapter's IPv4 address from `ipconfig`.
- The operator page refuses the token: check `network_token` in
  `%APPDATA%\BibleLive\launcher.json`, then restart the launcher. It passes the token to the
  server when it starts.

---

## Support and Resources

- **Documentation**: See `README.md` for usage and `DATABASE_SETUP.md` for
  database import; `SECURITY.md` covers tokens and endpoint protection
- **Configuration**: `.env.example` for all options
- **API Documentation**: Visit `http://localhost:8000/docs` when server is running
- **Health Endpoints**: `/healthz` and `/readyz`


