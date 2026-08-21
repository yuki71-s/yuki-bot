#!/bin/bash
# ============================================
# Yuki Bot — One-Command Deployment Script
# For: Hostinger KVM 1 (Ubuntu 22.04 + AaPanel)
# ============================================

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}  Yuki Bot Deployment Script${NC}"
echo -e "${BLUE}  VPS: Hostinger KVM 1 (Ubuntu 22.04)${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""

# ── Step 1: Update System ──
echo -e "${YELLOW}[1/10] Updating system packages...${NC}"
apt update && apt upgrade -y
echo -e "${GREEN}✓ System updated${NC}"

# ── Step 2: Install Dependencies ──
echo -e "${YELLOW}[2/10] Installing Python 3.11 + Git + dependencies...${NC}"
apt install python3.11 python3.11-venv python3-pip git curl ufw -y
echo -e "${GREEN}✓ Dependencies installed${NC}"

# ── Step 3: Clone Repositories ──
echo -e "${YELLOW}[3/10] Cloning yuki-bot repositories...${NC}"
mkdir -p /opt/yuki-bot
cd /opt/yuki-bot

# Clone bot
if [ ! -d ".git" ]; then
    git clone https://github.com/yuki71-s/yuki-bot.git .
else
    git pull origin master
fi

# Clone AI server
if [ ! -d "yuki-ai-server" ]; then
    git clone https://github.com/yuki71-s/yuki-ai-server.git yuki-ai-server
else
    cd yuki-ai-server && git pull origin master && cd ..
fi
echo -e "${GREEN}✓ Repositories cloned${NC}"

# ── Step 4: Setup Virtual Environments ──
echo -e "${YELLOW}[4/10] Setting up virtual environments...${NC}"

# Bot venv
python3.11 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate

# AI Server venv
cd yuki-ai-server
python3.11 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate
cd /opt/yuki-bot
echo -e "${GREEN}✓ Virtual environments ready${NC}"

# ── Step 5: Setup Environment Variables ──
echo -e "${YELLOW}[5/10] Setting up environment variables...${NC}"

# Bot .env
if [ ! -f ".env" ]; then
    cat > .env << 'EOF'
# Yuki Bot Configuration
BOT_TOKEN=YOUR_BOT_TOKEN_HERE
AI_SERVER_URL=http://127.0.0.1:8001
SHEET_ID=YOUR_GOOGLE_SHEET_ID_HERE
SERVICE_ACCOUNT_JSON=YOUR_SERVICE_ACCOUNT_JSON_HERE
ALLOWED_USERS=8575279550
EOF
    echo -e "${YELLOW}  ⚠️  Edit /opt/yuki-bot/.env with your API keys!${NC}"
else
    echo -e "${GREEN}  ✓ Bot .env already exists${NC}"
fi

# AI Server .env
if [ ! -f "yuki-ai-server/.env" ]; then
    cat > yuki-ai-server/.env << 'EOF'
# Yuki AI Server Configuration
GEMINI_API_KEY=YOUR_GEMINI_API_KEY_HERE
OPENROUTER_API_KEY=YOUR_OPENROUTER_API_KEY_HERE
TINYFISH_API_KEY=YOUR_TINYFISH_API_KEY_HERE
TAVILY_API_KEY=YOUR_TAVILY_API_KEY_HERE
PORT=8001
EOF
    echo -e "${YELLOW}  ⚠️  Edit /opt/yuki-bot/yuki-ai-server/.env with your API keys!${NC}"
else
    echo -e "${GREEN}  ✓ AI Server .env already exists${NC}"
fi

# ── Step 6: Config Nginx ──
echo -e "${YELLOW}[6/10] Configuring Nginx reverse proxy...${NC}"

# Backup existing config
if [ -f "/etc/nginx/conf.d/yuki.conf" ]; then
    cp /etc/nginx/conf.d/yuki.conf /etc/nginx/conf.d/yuki.conf.backup
fi

cat > /etc/nginx/conf.d/yuki.conf << 'EOF'
server {
    listen 80;
    server_name _;

    # Yuki Bot (Webhook endpoint)
    location /webhook {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
        proxy_connect_timeout 10s;
    }

    # Yuki AI Server
    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
        proxy_connect_timeout 10s;
    }

    # Health check
    location /health {
        proxy_pass http://127.0.0.1:8001/health;
    }
}
EOF

# Test nginx config
if nginx -t 2>&1; then
    systemctl reload nginx
    echo -e "${GREEN}✓ Nginx configured${NC}"
else
    echo -e "${RED}✗ Nginx config error!${NC}"
    exit 1
fi

# ── Step 7: Setup Systemd Services ──
echo -e "${YELLOW}[7/10] Setting up systemd services...${NC}"

# Yuki Bot service
cat > /etc/systemd/system/yuki-bot.service << 'EOF'
[Unit]
Description=Yuki Telegram Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/yuki-bot
ExecStart=/opt/yuki-bot/venv/bin/python app.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

# Yuki AI Server service
cat > /etc/systemd/system/yuki-ai-server.service << 'EOF'
[Unit]
Description=Yuki AI Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/yuki-bot/yuki-ai-server
ExecStart=/opt/yuki-bot/yuki-ai-server/venv/bin/uvicorn app:app --host 0.0.0.0 --port 8001
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd
systemctl daemon-reload
systemctl enable yuki-bot yuki-ai-server
echo -e "${GREEN}✓ Systemd services configured${NC}"

# ── Step 8: Setup Firewall ──
echo -e "${YELLOW}[8/10] Configuring firewall...${NC}"
ufw allow 22/tcp    # SSH
ufw allow 80/tcp    # HTTP
ufw allow 443/tcp   # HTTPS
ufw allow 8888/tcp  # AaPanel
ufw --force enable
echo -e "${GREEN}✓ Firewall configured${NC}"

# ── Step 9: Start Services ──
echo -e "${YELLOW}[9/10] Starting services...${NC}"
systemctl start yuki-bot yuki-ai-server
echo -e "${GREEN}✓ Services started${NC}"

# ── Step 10: Health Check ──
echo -e "${YELLOW}[10/10] Running health check...${NC}"
sleep 5

# Check services
BOT_STATUS=$(systemctl is-active yuki-bot)
AI_STATUS=$(systemctl is-active yuki-ai-server)

echo ""
echo -e "${BLUE}============================================${NC}"
echo -e "${GREEN}  Deployment Complete!${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""
echo -e "  ${BLUE}Services Status:${NC}"
echo -e "  - yuki-bot:       ${BOT_STATUS}"
echo -e "  - yuki-ai-server: ${AI_STATUS}"
echo ""
echo -e "  ${BLUE}Endpoints:${NC}"
echo -e "  - Bot Webhook: http://YOUR_VPS_IP/webhook"
echo -e "  - AI Server:   http://YOUR_VPS_IP/health"
echo ""
echo -e "  ${YELLOW}⚠️  IMPORTANT: Edit .env files with your API keys!${NC}"
echo -e "  - /opt/yuki-bot/.env"
echo -e "  - /opt/yuki-bot/yuki-ai-server/.env"
echo ""
echo -e "  ${BLUE}After editing .env, restart services:${NC}"
echo -e "  systemctl restart yuki-bot yuki-ai-server"
echo ""
echo -e "  ${BLUE}View logs:${NC}"
echo -e "  journalctl -u yuki-bot -f"
echo -e "  journalctl -u yuki-ai-server -f"
echo ""
echo -e "${BLUE}============================================${NC}"
