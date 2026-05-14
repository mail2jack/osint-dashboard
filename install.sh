#!/bin/bash
#
# Iveras OSINT Dashboard - Installation Script (v3.0)
# Production-ready: SpiderFoot, Nginx, PostgreSQL, SSL, systemd
#
# Usage:
#   wget https://raw.githubusercontent.com/mail2jack/osint-dashboard/main/install.sh
#   chmod +x install.sh
#   sudo ./install.sh
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Configuration
REPO_URL="https://github.com/mail2jack/osint-dashboard.git"
BRANCH="master"
APP_DIR="/opt/osint-dashboard"
SF_DIR="/opt/spiderfoot"
SERVICE_NAME="osint-dashboard"
SF_SERVICE_NAME="spiderfoot"

# Print functions
print_step() { echo -e "${YELLOW}[STEP]${NC} $1"; }
print_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }
print_info() { echo -e "${BLUE}[INFO]${NC} $1"; }

# Copy system lxml into a venv (needed on Python 3.14+ where pip can't build from source)
copy_system_lxml() {
    local venv_dir="$1"
    local sys_site py_ver sf_site
    sys_site=$(python3 -c "import site; print(site.getsitepackages()[0])" 2>/dev/null)
    sf_site=$(find "$venv_dir/lib" -name site-packages -type d 2>/dev/null | head -1)
    if [ -n "$sys_site" ] && [ -n "$sf_site" ]; then
        for pkg in "$sys_site"/lxml*; do
            [ -e "$pkg" ] && cp -r "$pkg" "$sf_site/" 2>/dev/null
        done
        print_success "System lxml gekopieerd naar $venv_dir"
    fi
}

# Header
echo -e "\n${CYAN}========================================${NC}"
echo -e "${CYAN}  Iveras OSINT Dashboard Installation${NC}"
echo -e "${CYAN}  Version 3.0 - Production Ready${NC}"
echo -e "${CYAN}========================================${NC}\n"

# Check root
if [[ $EUID -ne 0 ]]; then
    print_error "This script must be run as root"
    exit 1
fi

# ============================================================================
# STEP 1: Update System
# ============================================================================
print_step "Updating system packages..."
apt update -qq
apt upgrade -y -qq
print_success "System updated"

# ============================================================================
# STEP 1b: Ensure swap (prevents OOM during lxml compilation)
# ============================================================================
if ! swapon --show | grep -q .; then
    print_step "Creating 2GB swap file..."
    fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    print_success "Swap file created"
fi

# ============================================================================
# STEP 2: Install System Dependencies
# ============================================================================
print_step "Installing system dependencies..."
apt install -y \
    curl \
    wget \
    git \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    build-essential \
    libxml2-dev \
    libxslt1-dev \
    python3-lxml \
    postgresql \
    postgresql-contrib \
    nginx \
    ufw \
    fail2ban \
    software-properties-common \
    certbot \
    python3-certbot-nginx
print_success "System dependencies installed"

# ============================================================================
# STEP 3: Create App User
# ============================================================================
print_step "Creating application user..."
if id -u osint &>/dev/null; then
    print_info "User 'osint' already exists"
else
    useradd -m -s /bin/bash osint
    print_success "User 'osint' created"
fi

# ============================================================================
# STEP 4: Clone Repository
# ============================================================================
print_step "Cloning repository to $APP_DIR..."

if [[ -d "$APP_DIR" ]]; then
    print_info "Directory exists - removing old installation..."
    systemctl stop $SERVICE_NAME 2>/dev/null || true
    cd / && rm -rf $APP_DIR
fi

git clone -b $BRANCH $REPO_URL "$APP_DIR"
chown -R osint:osint "$APP_DIR"
print_success "Repository cloned"

# ============================================================================
# STEP 5: Setup Python Virtual Environment (Iveras)
# ============================================================================
print_step "Setting up Iveras Python virtual environment..."

cd "$APP_DIR"

if [[ -d "venv" ]]; then
    rm -rf venv
fi

python3 -m venv venv
copy_system_lxml "$APP_DIR/venv"
source venv/bin/activate

pip install --upgrade pip
pip install --upgrade setuptools wheel

print_step "Installing Python packages from requirements.txt..."
pip install -r requirements.txt

if ! "$APP_DIR/venv/bin/gunicorn" --version &>/dev/null; then
    print_error "Gunicorn installation failed!"
    exit 1
fi

chown -R osint:osint "$APP_DIR"
deactivate

print_success "Iveras virtual environment ready with all packages"

# ============================================================================
# STEP 6: Install SpiderFoot
# ============================================================================
print_step "Installing SpiderFoot..."

if [[ -d "$SF_DIR" ]]; then
    print_info "Directory exists - removing old SpiderFoot..."
    systemctl stop $SF_SERVICE_NAME 2>/dev/null || true
    rm -rf $SF_DIR
fi

git clone https://github.com/smicallef/spiderfoot.git "$SF_DIR"
chown -R osint:osint "$SF_DIR"

# Create SpiderFoot venv (separate to avoid dependency conflicts)
python3 -m venv "$SF_DIR/venv"
copy_system_lxml "$SF_DIR/venv"

# Remove lxml from requirements.txt — we already injected system lxml and
# SpiderFoot pins lxml<5 which blocks the pre-installed 6.x wheel from PyPI.
# Removing the pin lets pip skip the source build entirely.
sed -i '/^lxml/d' "$SF_DIR/requirements.txt"

source "$SF_DIR/venv/bin/activate"
pip install --upgrade pip
pip install -r "$SF_DIR/requirements.txt"
deactivate

# Create SpiderFoot passwd file for digest auth
SF_PASSWORD=$(openssl rand -base64 12 | tr -d '=+/')
mkdir -p /home/osint/.spiderfoot
cat > /home/osint/.spiderfoot/passwd << PASSEOF
admin:$SF_PASSWORD
PASSEOF
chown -R osint:osint /home/osint/.spiderfoot
chmod 600 /home/osint/.spiderfoot/passwd

print_success "SpiderFoot installed and configured"

# ============================================================================
# STEP 7: Setup PostgreSQL
# ============================================================================
print_step "Setting up PostgreSQL..."

systemctl enable postgresql
systemctl start postgresql

DB_PASSWORD=$(openssl rand -base64 18 | tr -d '=+/')

# Create database and user
sudo -u postgres psql << EOF
DO \$\$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'osint') THEN
      CREATE USER osint WITH PASSWORD '$DB_PASSWORD';
   ELSE
      ALTER USER osint WITH PASSWORD '$DB_PASSWORD';
   END IF;
END
\$\$;

SELECT 'CREATE DATABASE osint_db'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'osint_db')\gexec

GRANT ALL PRIVILEGES ON DATABASE osint_db TO osint;
ALTER DATABASE osint_db OWNER TO osint;
\c osint_db
GRANT ALL ON SCHEMA public TO osint;
EOF

print_success "PostgreSQL configured"

# ============================================================================
# STEP 8: Create Environment File
# ============================================================================
print_step "Creating environment configuration..."

SECRET_KEY=$(openssl rand -hex 32)
CMS_ENCRYPTION_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")

cat > "$APP_DIR/.env" << EOF
# Flask Configuration
FLASK_APP=app.py
SECRET_KEY=$SECRET_KEY

# Database (PostgreSQL)
DATABASE_URL=postgresql://osint:$DB_PASSWORD@localhost:5432/osint_db

# Server
PORT=5000

# CMS Encryption
CMS_ENCRYPTION_KEY=$CMS_ENCRYPTION_KEY

# SpiderFoot
SPIDERFOOT_URL=http://127.0.0.1:5001
SPIDERFOOT_USERNAME=admin
SPIDERFOOT_PASSWORD=$SF_PASSWORD

# API Keys (fill in your own keys)
OVERHEID_API_KEY=
BRAVE_API_KEY=
TWOCHAT_API_KEY=
TWOCHAT_WHATSAPP_NUMBER=
HIBP_API_KEY=
EOF

chown osint:osint "$APP_DIR/.env"
chmod 600 "$APP_DIR/.env"
print_success "Environment file created"

# ============================================================================
# STEP 9: Configure Nginx
# ============================================================================
print_step "Configuring Nginx..."

DOMAINS=()
print_info "Enter your domain name(s) if you want SSL (space-separated, or leave blank for IP-only):"
read -p "Domain names: " -a DOMAINS

rm -f /etc/nginx/sites-enabled/*
rm -f /etc/nginx/sites-available/*

if [[ ${#DOMAINS[@]} -gt 0 && -n "${DOMAINS[0]}" ]]; then
    SERVER_NAMES="${DOMAINS[*]}"
else
    SERVER_NAMES="_"
fi

cat > /etc/nginx/sites-available/default << NGINXEOF
server {
    listen 80 default_server;
    server_name ${SERVER_NAMES};

    client_max_body_size 50M;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_redirect off;

        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";

        proxy_next_upstream error timeout invalid_header http_500 http_502 http_503;
    }

    location /spiderfoot/ {
        proxy_pass http://127.0.0.1:5001/;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300s;
        proxy_connect_timeout 10s;
    }

    location /static {
        alias /opt/osint-dashboard/static;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }
}
NGINXEOF

ln -sf /etc/nginx/sites-available/default /etc/nginx/sites-enabled/default
nginx -t && systemctl restart nginx
print_success "Nginx configured"

# ============================================================================
# STEP 9b: SSL via Let's Encrypt (if domain provided)
# ============================================================================
if [[ ${#DOMAINS[@]} -gt 0 && -n "${DOMAINS[0]}" ]]; then
    print_step "Setting up SSL certificate for ${DOMAINS[*]}..."
    CERTBOT_ARGS=""
    for d in "${DOMAINS[@]}"; do
        CERTBOT_ARGS="$CERTBOT_ARGS -d $d"
    done
    FIRST_DOMAIN="${DOMAINS[0]}"
    certbot --nginx $CERTBOT_ARGS --non-interactive --agree-tos --email "admin@$FIRST_DOMAIN" || {
        print_warning "SSL setup failed. You can run it later:"
        print_info "  sudo certbot --nginx $CERTBOT_ARGS"
    }
    print_success "SSL configured"
fi

# ============================================================================
# STEP 10: Configure Firewall
# ============================================================================
print_step "Configuring firewall..."

ufw allow ssh
ufw allow http
ufw allow https

echo "y" | ufw enable || true
print_success "Firewall configured"

# ============================================================================
# STEP 11: Configure Fail2ban
# ============================================================================
print_step "Configuring fail2ban..."

# Enable SSH jail
if [ -f /etc/fail2ban/jail.conf ]; then
    cat > /etc/fail2ban/jail.local << 'FAIL2EOF'
[DEFAULT]
bantime = 3600
findtime = 600
maxretry = 5

[sshd]
enabled = true

[nginx-http-auth]
enabled = true

[nginx-botsearch]
enabled = true
logpath = /var/log/nginx/error.log
maxretry = 2
findtime = 600
bantime = 86400
FAIL2EOF
    systemctl enable fail2ban
    systemctl restart fail2ban
    print_success "Fail2ban configured"
fi

# ============================================================================
# STEP 12: Create Systemd Services
# ============================================================================
print_step "Creating systemd services..."

# Iveras service
cat > /etc/systemd/system/$SERVICE_NAME.service << 'SERVICEEOF'
[Unit]
Description=Iveras OSINT Dashboard
After=network.target postgresql.service spiderfoot.service
Wants=spiderfoot.service

[Service]
Type=simple
User=osint
Group=osint
WorkingDirectory=/opt/osint-dashboard
Environment="PATH=/opt/osint-dashboard/venv/bin"
ExecStart=/opt/osint-dashboard/venv/bin/gunicorn --workers 2 --bind 0.0.0.0:5000 --timeout 120 "app:app"
Restart=always
RestartSec=10s

[Install]
WantedBy=multi-user.target
SERVICEEOF

# SpiderFoot service
cat > /etc/systemd/system/$SF_SERVICE_NAME.service << 'SFEOF'
[Unit]
Description=SpiderFoot OSINT Automation Tool
Documentation=https://www.spiderfoot.net/documentation/
After=network.target

[Service]
Type=simple
User=osint
Group=osint
WorkingDirectory=/opt/spiderfoot
Environment="PATH=/opt/spiderfoot/venv/bin"
ExecStart=/opt/spiderfoot/venv/bin/python3 /opt/spiderfoot/sf.py -l 127.0.0.1:5001 --passwd /home/osint/.spiderfoot/passwd
Restart=always
RestartSec=10s

# Security
NoNewPrivileges=true
ProtectHome=true
ProtectSystem=full
PrivateTmp=true

[Install]
WantedBy=multi-user.target
SFEOF

chmod 644 /etc/systemd/system/$SERVICE_NAME.service
chmod 644 /etc/systemd/system/$SF_SERVICE_NAME.service
systemctl daemon-reload
systemctl enable $SERVICE_NAME
systemctl enable $SF_SERVICE_NAME
print_success "Systemd services created"

# ============================================================================
# STEP 13: Store SpiderFoot Settings in Database
# ============================================================================
print_step "Storing SpiderFoot settings in database..."

cd "$APP_DIR"
sudo -u osint ./venv/bin/python3 << PYEOF
import os
import sys
os.environ.setdefault('FLASK_APP', 'app.py')
os.environ.setdefault('SECRET_KEY', '$SECRET_KEY')
os.environ.setdefault('CMS_ENCRYPTION_KEY', '$CMS_ENCRYPTION_KEY')

from app import app
from cms.models import Setting, db

with app.app_context():
    db.create_all()
    Setting.set('spiderfoot_url', 'http://127.0.0.1:5001',
               description='SpiderFoot server URL', category='spiderfoot')
    Setting.set('spiderfoot_username', 'admin',
               description='SpiderFoot username', category='spiderfoot')
    Setting.set('spiderfoot_password', '$SF_PASSWORD',
               description='SpiderFoot password', category='spiderfoot')
    print('SpiderFoot settings stored in database')
PYEOF
print_success "SpiderFoot settings configured in database"

# ============================================================================
# STEP 14: Start Services
# ============================================================================
print_step "Starting services..."

# Start SpiderFoot first (Iveras depends on it)
systemctl start $SF_SERVICE_NAME
sleep 3

systemctl restart $SERVICE_NAME
sleep 2

# Check status
echo ""
if systemctl is-active --quiet $SERVICE_NAME; then
    print_success "Iveras started successfully"
else
    print_error "Iveras failed to start!"
    print_info "Check logs: journalctl -u $SERVICE_NAME -n 50"
fi

if systemctl is-active --quiet $SF_SERVICE_NAME; then
    print_success "SpiderFoot started successfully"
else
    print_error "SpiderFoot failed to start!"
    print_info "Check logs: journalctl -u $SF_SERVICE_NAME -n 50"
fi

systemctl status $SERVICE_NAME --no-pager || true
systemctl status $SF_SERVICE_NAME --no-pager || true

# ============================================================================
# STEP 15: Health Check Test
# ============================================================================
print_step "Running health check..."
sleep 2
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:5000/health 2>/dev/null || echo "failed")
if [[ "$HTTP_CODE" == "200" ]]; then
    print_success "Health check passed (HTTP $HTTP_CODE)"
else
    print_warning "Health check returned HTTP $HTTP_CODE"
    print_info "The app may still be starting up - check: curl http://localhost:5000/health"
fi

SF_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5001 2>/dev/null || echo "failed")
if [[ "$SF_CODE" == "200" || "$SF_CODE" == "401" ]]; then
    print_success "SpiderFoot reachable (HTTP $SF_CODE - auth required, which is correct)"
else
    print_warning "SpiderFoot returned HTTP $SF_CODE"
fi

# ============================================================================
# STEP 16: Get Server IP Addresses
# ============================================================================
echo -e "\n${CYAN}========================================${NC}"
echo -e "${CYAN}  Installation Complete!${NC}"
echo -e "${CYAN}========================================${NC}\n"

print_info "Server URLs:"
echo ""
hostname -I | tr ' ' '\n' | while read ip; do
    echo -e "  ${GREEN}http://$ip${NC}"
done
echo -e "  ${GREEN}http://localhost${NC}"
if [[ ${#DOMAINS[@]} -gt 0 && -n "${DOMAINS[0]}" ]]; then
    for d in "${DOMAINS[@]}"; do
        echo -e "  ${GREEN}https://$d${NC}"
    done
fi
echo ""

# ============================================================================
# FINAL INFORMATION
# ============================================================================
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Iveras OSINT Dashboard Installed${NC}"
echo -e "${GREEN}========================================${NC}\n"

echo -e "Installation Directory: ${BLUE}$APP_DIR${NC}"
echo -e "SpiderFoot Directory:   ${BLUE}$SF_DIR${NC}"
echo ""

echo -e "${YELLOW}--- SpiderFoot Credentials ---${NC}"
echo -e "  URL:       http://localhost/spiderfoot/"
echo -e "  Username:  admin"
echo -e "  Password:  ${RED}$SF_PASSWORD${NC}"
echo -e "  Passwd:    ${BLUE}/home/osint/.spiderfoot/passwd${NC}"
echo ""

echo -e "${YELLOW}--- PostgreSQL Database ---${NC}"
echo -e "  Host:     localhost"
echo -e "  Database: osint_db"
echo -e "  User:     osint"
echo -e "  Password: ${RED}$DB_PASSWORD${NC}"
echo -e "  URL:      ${BLUE}postgresql://osint:$DB_PASSWORD@localhost:5432/osint_db${NC}"
echo ""

echo -e "${YELLOW}--- Default App Login ---${NC}"
echo -e "  Username: ${BLUE}admin${NC}"
echo -e "  Password: ${RED}changeme123${NC} (change immediately after first login!)"
echo ""

echo -e "${RED}IMPORTANT:${NC}"
echo -e "  1. Change the default admin password immediately after first login"
echo -e "  2. Save the SpiderFoot password above - you'll need it for API access"
echo -e "  3. Edit ${BLUE}$APP_DIR/.env${NC} and add your API keys (OVERHEID, BRAVE, etc.)"
echo -e "  4. The .env file contains sensitive credentials - keep it secure"
echo ""

echo -e "${YELLOW}--- Useful Commands ---${NC}"
echo -e "  ${BLUE}sudo systemctl status $SERVICE_NAME${NC}      - Iveras status"
echo -e "  ${BLUE}sudo systemctl status $SF_SERVICE_NAME${NC}    - SpiderFoot status"
echo -e "  ${BLUE}sudo journalctl -u $SERVICE_NAME -f${NC}       - Iveras live logs"
echo -e "  ${BLUE}sudo journalctl -u $SF_SERVICE_NAME -f${NC}    - SpiderFoot live logs"
echo -e "  ${BLUE}sudo systemctl restart $SERVICE_NAME${NC}     - Restart Iveras"
echo -e "  ${BLUE}curl http://localhost:5000/health${NC}         - Health check"
echo ""

echo -e "${CYAN}========================================${NC}"
echo -e "${CYAN}  Happy OSINT-ing!${NC}"
echo -e "${CYAN}========================================${NC}\n"
