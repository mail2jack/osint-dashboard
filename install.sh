#!/bin/bash
#
# Iveras OSINT Dashboard - Full Stack Installation Script
# Version: 1.0.0
# Author: Iveras OSINT Team
#
# Usage:
#   wget https://raw.githubusercontent.com/mail2jack/osint-dashboard/main/install.sh
#   chmod +x install.sh
#   ./install.sh
#

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
REPO_URL="https://github.com/mail2jack/osint-dashboard.git"
BRANCH="master"
APP_DIR="/opt/osint-dashboard"
SERVICE_NAME="osint-dashboard"
DOMAIN=""
PORT=5000

# Print functions
print_header() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}========================================${NC}\n"
}

print_step() {
    echo -e "${YELLOW}[STEP]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

# Check if running as root
check_root() {
    if [[ $EUID -ne 0 ]]; then
        print_error "This script must be run as root (use sudo)"
        exit 1
    fi
}

# Get system info
get_system_info() {
    print_step "Detecting system..."
    
    if [[ -f /etc/os-release ]]; then
        source /etc/os-release
        OS=$ID
        VER=$VERSION_ID
    else
        OS="unknown"
        VER="unknown"
    fi
    
    print_success "Detected: $OS $VER"
}

# Update system
update_system() {
    print_step "Updating system packages..."
    apt update -qq
    apt upgrade -y -qq
    print_success "System updated"
}

# Install dependencies
install_dependencies() {
    print_step "Installing system dependencies..."
    
    # Common dependencies
    apt install -y \
        curl \
        wget \
        git \
        ufw \
        fail2ban \
        python3 \
        python3-pip \
        python3-venv \
        postgresql \
        postgresql-contrib \
        nginx \
        certbot \
        python3-certbot-nginx
    
    print_success "Dependencies installed"
}

# Create app user
create_app_user() {
    print_step "Creating application user..."
    
    if id -u osint &>/dev/null; then
        print_warning "User 'osint' already exists"
    else
        useradd -m -s /bin/bash osint
        print_success "User 'osint' created"
    fi
}

# Clone repository
clone_repo() {
    print_step "Cloning repository..."
    
    if [[ -d "$APP_DIR" ]]; then
        print_warning "Directory $APP_DIR already exists"
        read -p "Update existing installation? (y/n): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            cd "$APP_DIR"
            git pull origin $BRANCH
            print_success "Repository updated"
        fi
    else
        git clone -b $BRANCH $REPO_URL "$APP_DIR"
        chown -R osint:osint "$APP_DIR"
        print_success "Repository cloned to $APP_DIR"
    fi
}

# Setup virtual environment
setup_venv() {
    print_step "Setting up Python virtual environment..."
    
    cd "$APP_DIR"
    
    # Create venv if not exists
    if [[ ! -d "venv" ]]; then
        python3 -m venv venv
    fi
    
    # Activate venv and install dependencies
    source venv/bin/activate
    pip install --upgrade pip -q
    pip install -r requirements.txt -q
    
    chown -R osint:osint "$APP_DIR"
    deactivate
    
    print_success "Virtual environment ready"
}

# Setup PostgreSQL
setup_postgresql() {
    print_step "Setting up PostgreSQL..."
    
    # Start PostgreSQL
    systemctl enable postgresql
    systemctl start postgresql
    
    # Create database and user
    sudo -u postgres psql << EOF
-- Create database user if not exists
DO 
\$do\$
BEGIN
   IF NOT EXISTS (
      SELECT FROM pg_catalog.pg_roles
      WHERE  rolname = 'osint') THEN
      CREATE USER osint WITH PASSWORD 'osint_secure_password_change_me';
   END IF;
END
\$do\$;

-- Create database
SELECT 'CREATE DATABASE osint_db'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'osint_db')\gexec

-- Grant privileges
GRANT ALL PRIVILEGES ON DATABASE osint_db TO osint;
ALTER DATABASE osint_db OWNER TO osint;
EOF
    
    print_success "PostgreSQL configured"
    
    # Return the database URL
    echo "postgresql://osint:osint_secure_password_change_me@localhost/osint_db"
}

# Create .env file
create_env_file() {
    print_step "Creating environment configuration..."
    
    # Ask for domain
    read -p "Enter your domain name (or press Enter for localhost): " DOMAIN
    read -p "Enter Brave API Key (optional, press Enter to skip): " BRAVE_API_KEY
    read -p "Enter secret key (press Enter for random): " SECRET_KEY
    
    # Generate random secret if empty
    if [[ -z "$SECRET_KEY" ]]; then
        SECRET_KEY=$(openssl rand -hex 32)
    fi
    
    cat > "$APP_DIR/.env" << EOF
# Flask Configuration
FLASK_APP=app.py
FLASK_ENV=production
SECRET_KEY=$SECRET_KEY

# Database
DATABASE_URL=postgresql://osint:osint_secure_password_change_me@localhost/osint_db

# OSINT Settings
BRAVE_API_KEY=$BRAVE_API_KEY

# Server
PORT=$PORT
EOF
    
    chown osint:osint "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
    
    print_success "Environment file created"
}

# Create Gunicorn configuration
create_gunicorn_config() {
    print_step "Creating Gunicorn configuration..."
    
    cat > "$APP_DIR/gunicorn_config.py" << 'EOF'
# Gunicorn configuration file
import multiprocessing

# Server socket
bind = "127.0.0.1:5000"
backlog = 2048

# Worker processes
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "sync"
worker_connections = 1000
timeout = 120
keepalive = 5

# Logging
accesslog = "/var/log/osint-dashboard/access.log"
errorlog = "/var/log/osint-dashboard/error.log"
loglevel = "info"

# Process naming
proc_name = "osint-dashboard"

# Server mechanics
daemon = False
pidfile = "/var/run/osint-dashboard.pid"
umask = 0
user = "osint"
group = "osint"
tmp_upload_dir = None
EOF
    
    chown osint:osint "$APP_DIR/gunicorn_config.py"
    
    # Create log directory
    mkdir -p /var/log/osint-dashboard
    chown osint:osint /var/log/osint-dashboard
    
    print_success "Gunicorn configured"
}

# Create systemd service
create_systemd_service() {
    print_step "Creating systemd service..."
    
    cat > /etc/systemd/system/$SERVICE_NAME.service << EOF
[Unit]
Description=Iveras OSINT Dashboard
After=network.target postgresql.service

[Service]
Type=notify
User=osint
Group=osint
WorkingDirectory=$APP_DIR
Environment="PATH=$APP_DIR/venv/bin"
ExecStart=$APP_DIR/venv/bin/gunicorn -c $APP_DIR/gunicorn_config.py app:app
ExecReload=/bin/kill -s HUP \$MAINPID
KillMode=mixed
TimeoutStopSec=5
PrivateTmp=true
Restart=on-failure
RestartSec=10s

[Install]
WantedBy=multi-user.target
EOF
    
    systemctl daemon-reload
    systemctl enable $SERVICE_NAME
    
    print_success "Systemd service created"
}

# Setup Nginx
setup_nginx() {
    print_step "Setting up Nginx..."
    
    if [[ -n "$DOMAIN" && "$DOMAIN" != "localhost" ]]; then
        # Create Nginx config with SSL
        cat > /etc/nginx/sites-available/$SERVICE_NAME << EOF
server {
    listen 80;
    server_name $DOMAIN;
    
    return 301 https://\$server_name\$request_uri;
}

server {
    listen 443 ssl http2;
    server_name $DOMAIN;
    
    # SSL Configuration
    ssl_certificate /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;
    
    # Proxy to Gunicorn
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_redirect off;
        
        # WebSocket support
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }
    
    # Static files
    location /static {
        alias $APP_DIR/static;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }
    
    # Upload limit
    client_max_body_size 50M;
}
EOF
        
        # Enable site
        ln -sf /etc/nginx/sites-available/$SERVICE_NAME /etc/nginx/sites-enabled/
        
        # Get SSL certificate
        certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "admin@$DOMAIN"
        
        print_success "Nginx configured with SSL for $DOMAIN"
    else
        # Simple config without SSL
        cat > /etc/nginx/sites-available/$SERVICE_NAME << EOF
server {
    listen 80;
    server_name localhost;
    
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_redirect off;
    }
    
    location /static {
        alias $APP_DIR/static;
        expires 30d;
    }
    
    client_max_body_size 50M;
}
EOF
        
        ln -sf /etc/nginx/sites-available/$SERVICE_NAME /etc/nginx/sites-enabled/
        rm -f /etc/nginx/sites-enabled/default
        
        print_success "Nginx configured (HTTP only)"
    fi
    
    # Test and reload Nginx
    nginx -t && systemctl reload nginx
}

# Configure firewall
setup_firewall() {
    print_step "Configuring firewall..."
    
    # Allow SSH, HTTP, HTTPS
    ufw allow ssh
    ufw allow http
    ufw allow https
    
    # Enable firewall
    echo "y" | ufw enable
    
    print_success "Firewall configured"
}

# Start services
start_services() {
    print_step "Starting services..."
    
    systemctl restart $SERVICE_NAME
    systemctl status $SERVICE_NAME --no-pager || true
    
    print_success "Services started"
}

# Print final instructions
print_final_instructions() {
    print_header "Installation Complete!"
    
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  Iveras OSINT Dashboard Installed${NC}"
    echo -e "${GREEN}========================================${NC}\n"
    
    echo -e "Installation Directory: ${BLUE}$APP_DIR${NC}"
    echo -e "Service Name: ${BLUE}$SERVICE_NAME${NC}"
    
    if [[ -n "$DOMAIN" && "$DOMAIN" != "localhost" ]]; then
        echo -e "URL: ${GREEN}https://$DOMAIN${NC}"
    else
        echo -e "URL: ${GREEN}http://localhost:5000${NC}"
    fi
    
    echo ""
    echo -e "Default Login:"
    echo -e "  Username: ${YELLOW}admin${NC}"
    echo -e "  Password: ${YELLOW}changeme123${NC}"
    echo ""
    
    echo -e "${YELLOW}IMPORTANT:${NC} Change the admin password immediately!"
    echo ""
    
    echo "Useful commands:"
    echo -e "  ${BLUE}sudo systemctl status $SERVICE_NAME${NC}  - Check status"
    echo -e "  ${BLUE}sudo systemctl restart $SERVICE_NAME${NC} - Restart"
    echo -e "  ${BLUE}sudo journalctl -u $SERVICE_NAME -f${NC}  - View logs"
    echo ""
    
    echo "Database credentials (change these!):"
    echo -e "  ${BLUE}sudo -u postgres psql -d osint_db${NC}"
    echo ""
}

# Main installation
main() {
    print_header "Iveras OSINT Dashboard Installation"
    
    check_root
    get_system_info
    update_system
    install_dependencies
    create_app_user
    clone_repo
    setup_venv
    setup_postgresql
    create_env_file
    create_gunicorn_config
    create_systemd_service
    setup_nginx
    setup_firewall
    start_services
    print_final_instructions
}

# Run main
main "$@"
