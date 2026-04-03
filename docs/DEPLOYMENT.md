# Deployment Guide

## Local Development

### Prerequisites

- Python 3.10+
- Google Chrome (stable channel)
- An LLM provider (Ollama, LM Studio, or any cloud API)

### Setup

```bash
git clone https://github.com/ankitjha67/linkedin-lightning-applier.git
cd linkedin-lightning-applier
pip install -r requirements.txt

cp config.example.yaml config.yaml
# Edit config.yaml with your credentials and preferences
```

### Running

```bash
# Standard run
python main.py

# Custom config
python main.py -c my_config.yaml
```

The bot will:
1. Launch Chrome (visible by default)
2. Log in to LinkedIn (or wait for manual login)
3. Start the search/apply cycle
4. Show the dashboard at http://localhost:5000

Press `Ctrl+C` to stop gracefully (finishes current action, exports final CSVs).

---

## Docker Deployment

### Quick Start

```bash
# Prepare config
cp config.example.yaml config.yaml
nano config.yaml  # Fill in your details

# Set headless mode for Docker
# In config.yaml: browser.headless: true

# Build and run
docker build -f docker/Dockerfile -t lla .
docker run -d \
  --name lla \
  --shm-size=2g \
  -v $(pwd)/config.yaml:/app/config.yaml:ro \
  -v lla-data:/app/data \
  -v lla-logs:/app/logs \
  -p 5000:5000 \
  -p 8080:8080 \
  lla
```

### Docker Compose

```bash
cd docker
cp ../config.yaml .
docker-compose up -d

# View logs
docker-compose logs -f

# Stop
docker-compose down
```

### Health Monitoring

The container includes a health check that verifies:
- `state.db` exists and was modified within the last 30 minutes
- Log files are being written

```bash
# Check health
docker inspect --format='{{.State.Health.Status}}' lla

# Manual check
docker exec lla python docker/healthcheck.py
```

### Ports

| Port | Service | Auth |
|------|---------|------|
| 5000 | Real-time dashboard | None (internal network) |
| 8080 | Web app + API | Username/password |

### Volumes

| Volume | Contents |
|--------|----------|
| `/app/data` | SQLite DB, CSVs, tailored resumes |
| `/app/logs` | Daily log files |
| `/app/config.yaml` | Configuration (mount read-only) |

---

## Cloud VPS Deployment

### AWS EC2 / DigitalOcean / Railway

1. **Provision a VM** — Ubuntu 22.04, 2GB+ RAM, 20GB disk
2. **Install dependencies:**

```bash
sudo apt update && sudo apt install -y python3-pip google-chrome-stable
pip3 install -r requirements.txt
```

3. **Configure:**

```bash
cp config.example.yaml config.yaml
nano config.yaml
# Set browser.headless: true
# Set dashboard.host: "0.0.0.0"
```

4. **Run with systemd:**

```bash
sudo tee /etc/systemd/system/lla.service << 'EOF'
[Unit]
Description=LinkedIn Lightning Applier
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/linkedin-lightning-applier
ExecStart=/usr/bin/python3 main.py -c config.yaml
Restart=on-failure
RestartSec=60

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable lla
sudo systemctl start lla
sudo systemctl status lla
```

5. **Monitor:**

```bash
# View logs
journalctl -u lla -f

# Check dashboard
curl http://localhost:5000/health
```

### Security for Cloud

- **Firewall:** Only expose ports 5000/8080 if needed. Use SSH tunnel for dashboard access:
  ```bash
  ssh -L 5000:localhost:5000 user@your-server
  ```
- **Web app auth:** Set strong credentials via environment variables:
  ```bash
  export LLA_USERNAME="your-username"
  export LLA_PASSWORD_HASH=$(python3 -c "import hashlib; print(hashlib.sha256(b'your-password').hexdigest())")
  ```
- **Config encryption:** Consider encrypting `config.yaml` at rest with `gpg` or using a secrets manager.

---

## Web App Deployment

The SaaS web app can run independently of the bot (reads from the same SQLite DB):

```bash
# Standalone
python webapp/app.py

# With custom settings
LLA_USERNAME=admin \
LLA_PASSWORD_HASH=$(python3 -c "import hashlib; print(hashlib.sha256(b'strongpassword').hexdigest())") \
python webapp/app.py
```

### Behind Nginx

```nginx
server {
    listen 80;
    server_name lla.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

---

## Monitoring & Alerts Setup

### Telegram Bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot`, follow prompts to get a bot token
3. Start a chat with your bot, then get your chat ID:
   ```bash
   curl "https://api.telegram.org/bot<TOKEN>/getUpdates"
   ```
4. Add to config:
   ```yaml
   alerts:
     enabled: true
     telegram:
       enabled: true
       bot_token: "123456:ABC-DEF..."
       chat_id: "987654321"
   ```

### Discord Webhook

1. In your Discord server: Channel Settings -> Integrations -> Create Webhook
2. Copy the webhook URL
3. Add to config:
   ```yaml
   alerts:
     discord:
       enabled: true
       webhook_url: "https://discord.com/api/webhooks/..."
   ```

### Slack Webhook

1. Go to https://api.slack.com/apps -> Create App -> Incoming Webhooks
2. Add webhook to your channel, copy URL
3. Add to config:
   ```yaml
   alerts:
     slack:
       enabled: true
       webhook_url: "https://hooks.slack.com/services/..."
   ```

---

## Proxy Setup

### Residential Proxies

```yaml
proxy:
  enabled: true
  proxy_list:
    - "http://user:pass@proxy1.example.com:8080"
    - "socks5://user:pass@proxy2.example.com:1080"
  rotate_per_session: true
  sticky_session_minutes: 30
```

Or use a proxy file (one per line):

```yaml
proxy:
  proxy_file: "proxies.txt"
```

The proxy manager scores each proxy by success rate and latency, automatically routes traffic to the healthiest options, and bans proxies after repeated failures.

---

## Troubleshooting

### Chrome won't start

```bash
# Check Chrome is installed
google-chrome --version

# For headless servers, install xvfb
sudo apt install -y xvfb
Xvfb :99 -screen 0 1280x900x24 &
export DISPLAY=:99
```

### Login fails

- Check credentials in `config.yaml`
- LinkedIn may require security verification (CAPTCHA, 2FA) — complete it in the browser window
- Try setting `browser.user_data_dir` and logging in manually once

### Bot gets rate-limited

- Reduce `scheduling.max_applies_per_day` (recommended: 20-30)
- Increase delays in scheduling section
- Enable `activity_simulation` to look more human
- Consider `proxy.enabled: true` with residential proxies

### AI not answering questions

- Verify your LLM is running (`curl http://localhost:11434/v1/models` for Ollama)
- Check `ai.enabled: true` and correct provider/model settings
- Review logs for API errors: `grep "AI error" logs/lla_*.log`
