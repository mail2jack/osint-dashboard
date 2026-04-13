#!/bin/bash
#
# Iveras OSINT Dashboard - Installation Script (v2.0)
# Fixed version with all troubleshooting included
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
NC='\033[0m'

# Configuration
REPO_URL="https://github.com/mail2jack/osint-dashboard.git"
BRANCH="master"
APP_DIR="/opt/osint-dashboard"
SERVICE_NAME="osint-dashboard"

# Print functions
print_step() { echo -e "${YELLOW}[STEP]${NC} $1"; }
print_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }
print_info() { echo -e "${BLUE}[INFO]${NC} $1"; }

# Header
echo -e "\n${BLUE}========================================${NC}"
echo -e "${BLUE}  Iveras OSINT Dashboard Installation${NC}"
echo -e "${BLUE}  Version 2.0 - Fixed & Improved${NC}"
echo -e "${BLUE}========================================${NC}\n"

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
    postgresql \
    postgresql-contrib \
    nginx \
    ufw \
    software-properties-common
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
    rm -rf $APP_DIR
fi

git clone -b $BRANCH $REPO_URL "$APP_DIR"
chown -R osint:osint "$APP_DIR"
print_success "Repository cloned"

# ============================================================================
# STEP 5: Setup Python Virtual Environment
# ============================================================================
print_step "Setting up Python virtual environment..."

cd "$APP_DIR"

# Remove old venv if exists
if [[ -d "venv" ]]; then
    rm -rf venv
fi

# Create fresh venv
python3 -m venv venv
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip
pip install --upgrade setuptools wheel

# Install Flask and extensions
print_step "Installing Flask and extensions..."
pip install flask flask-sqlalchemy flask-login flask-migrate flask-wtf flask-cors flask-bcrypt werkzeug

# Install HTTP clients
print_step "Installing HTTP clients..."
pip install requests httpx urllib3

# Install data processing
print_step "Installing data processing packages..."
pip install beautifulsoup4 lxml Pillow bleach markdown python-dateutil

# Install database packages
print_step "Installing database packages..."
pip install psycopg2-binary cryptography

# Install utilities
print_step "Installing utilities..."
pip install python-dotenv dnspython email-validator reportlab

# Install Gunicorn (REQUIRED for systemd)
print_step "Installing Gunicorn..."
pip install gunicorn

# Verify gunicorn is installed
if ! "$APP_DIR/venv/bin/gunicorn" --version &>/dev/null; then
    print_error "Gunicorn installation failed!"
    exit 1
fi

chown -R osint:osint "$APP_DIR"
deactivate

print_success "Virtual environment ready with all packages"

# ============================================================================
# STEP 6: Setup PostgreSQL
# ============================================================================
print_step "Setting up PostgreSQL..."

systemctl enable postgresql
systemctl start postgresql

# Create database and user
sudo -u postgres psql << 'EOF'
-- Create user if not exists
DO 
$do$
BEGIN
   IF NOT EXISTS (
      SELECT FROM pg_catalog.pg_roles
      WHERE  rolname = 'osint') THEN
      CREATE USER osint WITH PASSWORD 'ChangeThisPassword123!';
   END IF;
END
$do$;

-- Create database
SELECT 'CREATE DATABASE osint_db'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'osint_db')\gexec

-- Grant privileges
GRANT ALL PRIVILEGES ON DATABASE osint_db TO osint;
ALTER DATABASE osint_db OWNER TO osint;
EOF

print_success "PostgreSQL configured"

# ============================================================================
# STEP 7: Create Environment File
# ============================================================================
print_step "Creating environment configuration..."

SECRET_KEY=$(openssl rand -hex 32)

cat > "$APP_DIR/.env" << EOF
# Flask Configuration
FLASK_APP=app.py
SECRET_KEY=$SECRET_KEY

# Database
DATABASE_URL=postgresql://osint:ChangeThisPassword123!@localhost:5432/osint_db

# Server
PORT=5000
EOF

chown osint:osint "$APP_DIR/.env"
chmod 600 "$APP_DIR/.env"
print_success "Environment file created"

# ============================================================================
# STEP 8: Configure Nginx
# ============================================================================
print_step "Configuring Nginx..."

# Remove old configs
rm -f /etc/nginx/sites-enabled/*
rm -f /etc/nginx/sites-available/*

# Create Nginx config
cat > /etc/nginx/sites-available/default << 'EOF'
server {
    listen 80 default_server;
    server_name _;

    client_max_body_size 50M;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_redirect off;
        
        # WebSocket support
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    location /static {
        alias /opt/osint-dashboard/static;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }
}
EOF

ln -sf /etc/nginx/sites-available/default /etc/nginx/sites-enabled/default

# Test and start nginx
nginx -t && systemctl restart nginx
print_success "Nginx configured"

# ============================================================================
# STEP 9: Configure Firewall
# ============================================================================
print_step "Configuring firewall..."

# Allow SSH, HTTP, HTTPS
ufw allow ssh
ufw allow http
ufw allow https

# Enable firewall
echo "y" | ufw enable || true
print_success "Firewall configured"

# ============================================================================
# STEP 10: Create Systemd Service
# ============================================================================
print_step "Creating systemd service..."

cat > /etc/systemd/system/$SERVICE_NAME.service << 'EOF'
[Unit]
Description=Iveras OSINT Dashboard
After=network.target postgresql.service

[Service]
Type=simple
User=osint
WorkingDirectory=/opt/osint-dashboard
Environment="PATH=/opt/osint-dashboard/venv/bin"
Environment="FLASK_APP=app.py"
ExecStart=/opt/osint-dashboard/venv/bin/gunicorn --workers 2 --bind 0.0.0.0:5000 --timeout 120 "app:app"
Restart=always
RestartSec=10s

[Install]
WantedBy=multi-user.target
EOF

chmod 644 /etc/systemd/system/$SERVICE_NAME.service
systemctl daemon-reload
systemctl enable $SERVICE_NAME

# ============================================================================
# STEP 11: Start Services
# ============================================================================
print_step "Starting services..."

systemctl start $SERVICE_NAME
sleep 2

# Check status
if systemctl is-active --quiet $SERVICE_NAME; then
    print_success "Service started successfully"
else
    print_error "Service failed to start!"
    print_info "Check logs with: journalctl -u $SERVICE_NAME -n 50"
fi

systemctl status $SERVICE_NAME --no-pager || true

# ============================================================================
# STEP 12: Get Server IP Addresses
# ============================================================================
echo -e "\n${BLUE}========================================${NC}"
echo -e "${BLUE}  Installation Complete!${NC}"
echo -e "${BLUE}========================================${NC}\n"

print_info "Server IP Addresses:"
echo ""
hostname -I | tr ' ' '\n' | while read ip; do
    echo -e "  ${GREEN}http://$ip:5000${NC}"
done
echo ""

# Also show localhost
echo -e "  ${GREEN}http://localhost:5000${NC}"
echo ""

# ============================================================================
# FINAL INFORMATION
# ============================================================================
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Iveras OSINT Dashboard Installed${NC}"
echo -e "${GREEN}========================================${NC}\n"

echo -e "Installation Directory: ${BLUE}$APP_DIR${NC}"
echo ""
echo -e "Default Login:"
echo -e "  Username: ${YELLOW}admin${NC}"
echo -e "  Password: ${YELLOW}changeme123${NC}"
echo ""
echo -e "${RED}IMPORTANT:${NC} Change these credentials immediately!"
echo ""

echo -e "Useful Commands:"
echo -e "  ${BLUE}sudo systemctl status $SERVICE_NAME${NC}    - Check status"
echo -e "  ${BLUE}sudo systemctl restart $SERVICE_NAME${NC}  - Restart service"
echo -e "  ${BLUE}sudo journalctl -u $SERVICE_NAME -f${NC}     - View live logs"
echo -e "  ${BLUE}sudo systemctl stop $SERVICE_NAME${NC}    - Stop service"
echo ""
echo -e "Database:"
echo -e "  Host: ${BLUE}localhost${NC}"
echo -e "  Database: ${BLUE}osint_db${NC}"
echo -e "  User: ${BLUE}osint${NC}"
echo -e "  Password: ${RED}ChangeThisPassword123!${NC} (change this!)"
echo ""

echo -e "${BLUE}========================================${NC}\n"
