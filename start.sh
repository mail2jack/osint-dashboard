#!/bin/bash
#
# Iveras OSINT + SpiderFoot Startup Script
# =======================================
# Combined launcher for Iveras CMS and SpiderFoot OSINT tool.
#
# Features:
# - Interactive port selection
# - Old instance cleanup
# - Auto-configuration of SpiderFoot URL
# - Status checking
#
# Usage:
#   ./start.sh              Interactive mode (asks for ports)
#   ./start.sh start        Start both services
#   ./start.sh stop         Stop both services
#   ./start.sh restart      Restart both services
#   ./start.sh status       Check status of both services
#   ./start.sh app <port>   Start app only on specific port
#   ./start.sh sf <port>    Start SpiderFoot only on specific port
#

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$SCRIPT_DIR"
SF_DIR="/Users/gast/Documents/spiderfoot"

# Configuration files
APP_CONFIG="$SCRIPT_DIR/.app_port"
SF_CONFIG="$SCRIPT_DIR/.sf_port"

# Default ports
DEFAULT_APP_PORT=5000
DEFAULT_SF_PORT=5001

# Log files
APP_LOG="$SCRIPT_DIR/app.log"
SF_LOG="$SCRIPT_DIR/spiderfoot.log"

# PID files
APP_PID_FILE="$SCRIPT_DIR/app.pid"
SF_PID_FILE="$SCRIPT_DIR/spiderfoot.pid"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

#######################################
# Utility Functions
#######################################

get_app_port() {
    if [ -f "$APP_CONFIG" ]; then
        cat "$APP_CONFIG"
    else
        echo "$DEFAULT_APP_PORT"
    fi
}

get_sf_port() {
    if [ -f "$SF_CONFIG" ]; then
        cat "$SF_CONFIG"
    else
        echo "$DEFAULT_SF_PORT"
    fi
}

save_app_port() {
    echo "$1" > "$APP_CONFIG"
}

save_sf_port() {
    echo "$1" > "$SF_CONFIG"
}

is_port_in_use() {
    local port=$1
    if lsof -i :$port > /dev/null 2>&1; then
        return 0  # Port is in use
    else
        return 1  # Port is free
    fi
}

get_pid_for_port() {
    local port=$1
    lsof -ti :$port 2>/dev/null
}

#######################################
# Print Functions
#######################################

print_header() {
    echo ""
    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}  Iveras OSINT + SpiderFoot Launcher${NC}"
    echo -e "${CYAN}========================================${NC}"
    echo ""
}

print_status() {
    local service=$1
    local status=$2
    if [ "$status" = "running" ]; then
        echo -e "  ${GREEN}✓${NC} $service"
    else
        echo -e "  ${RED}✗${NC} $service"
    fi
}

print_info() {
    echo -e "  ${BLUE}ℹ${NC} $1"
}

print_warning() {
    echo -e "  ${YELLOW}⚠${NC} $1"
}

print_error() {
    echo -e "  ${RED}✗${NC} $1"
}

print_success() {
    echo -e "  ${GREEN}✓${NC} $1"
}

#######################################
# Kill Functions
#######################################

kill_on_port() {
    local port=$1
    local service=$2
    local pids=$(get_pid_for_port $port 2>/dev/null)
    
    if [ -n "$pids" ]; then
        echo "  Killing old $service on port $port (PID: $pids)"
        echo "$pids" | xargs kill -9 2>/dev/null
        sleep 1
        print_success "Old $service stopped"
        return 0
    fi
    return 1
}

kill_by_name() {
    local name=$1
    local pids=$(pgrep -f "$name" 2>/dev/null)
    
    if [ -n "$pids" ]; then
        echo "  Killing $name processes: $pids"
        echo "$pids" | xargs kill -9 2>/dev/null
        sleep 1
        return 0
    fi
    return 1
}

cleanup_old_instances() {
    print_header
    echo " Cleaning Up Old Instances"
    echo "----------------------------------------"
    
    local cleaned=0
    local app_port=$(get_app_port)
    local sf_port=$(get_sf_port)
    
    # Kill old app instances
    if kill_on_port $app_port "Iveras app"; then
        cleaned=1
    fi
    kill_by_name "python.*app.py" "Iveras app"
    
    # Kill old SpiderFoot instances
    if kill_on_port $sf_port "SpiderFoot"; then
        cleaned=1
    fi
    kill_by_name "sf.py" "SpiderFoot"
    
    # Also check for any Python processes on these ports
    for port in $app_port $sf_port; do
        if is_port_in_use $port; then
            pids=$(get_pid_for_port $port)
            if [ -n "$pids" ]; then
                echo "  Force killing on port $port: $pids"
                echo "$pids" | xargs kill -9 2>/dev/null
                cleaned=1
            fi
        fi
    done
    
    if [ $cleaned -eq 1 ]; then
        sleep 1
        print_success "Cleanup complete"
    else
        print_info "No old instances found"
    fi
    
    echo ""
}

#######################################
# Ask Functions
#######################################

ask_ports() {
    print_header
    echo " Port Configuration"
    echo "----------------------------------------"
    echo ""
    
    # Ask for app port
    local current_app_port=$(get_app_port)
    read -p "Iveras App Port [$current_app_port]: " app_port
    app_port="${app_port:-$current_app_port}"
    
    # Validate app port
    while ! [[ "$app_port" =~ ^[0-9]+$ ]] || [ "$app_port" -lt 1024 ] || [ "$app_port" -gt 65535 ]; do
        print_error "Invalid port. Please enter a number between 1024 and 65535."
        read -p "Iveras App Port: " app_port
    done
    
    # Ask for SpiderFoot port
    local current_sf_port=$(get_sf_port)
    read -p "SpiderFoot Port [$current_sf_port]: " sf_port
    sf_port="${sf_port:-$current_sf_port}"
    
    # Validate SpiderFoot port
    while ! [[ "$sf_port" =~ ^[0-9]+$ ]] || [ "$sf_port" -lt 1024 ] || [ "$sf_port" -gt 65535 ]; do
        print_error "Invalid port. Please enter a number between 1024 and 65535."
        read -p "SpiderFoot Port: " sf_port
    done
    
    # Check for conflicts
    if [ "$app_port" = "$sf_port" ]; then
        print_error "Ports cannot be the same!"
        ask_ports
        return
    fi
    
    # Save ports
    save_app_port "$app_port"
    save_sf_port "$sf_port"
    
    echo ""
    echo " Ports saved:"
    echo "  Iveras:    $app_port"
    echo "  SpiderFoot: $sf_port"
    echo ""
}

#######################################
# Start Functions
#######################################

start_spiderfoot() {
    local port=$(get_sf_port)
    
    # Check if SpiderFoot directory exists
    if [ ! -d "$SF_DIR" ]; then
        print_warning "SpiderFoot not found at $SF_DIR"
        read -p "Should I clone SpiderFoot? (Y/n): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Nn]$ ]]; then
            return 1
        fi
        print_info "Cloning SpiderFoot..."
        cd /Users/gast/Documents
        git clone https://github.com/smicallef/spiderfoot.git 2>/dev/null
        cd "$SF_DIR" && pip3 install -r requirements.txt 2>/dev/null
        print_success "SpiderFoot installed"
    fi
    
    # Check if already running
    if is_port_in_use $port; then
        print_warning "SpiderFoot already running on port $port"
        return 0
    fi
    
    print_info "Starting SpiderFoot on port $port..."
    cd "$SF_DIR"
    python3 ./sf.py -l 127.0.0.1:$port >> "$SF_LOG" 2>&1 &
    echo $! > "$SF_PID_FILE"
    save_sf_port "$port"
    
    sleep 2
    
    # Verify it started
    if is_port_in_use $port; then
        print_success "SpiderFoot started on http://localhost:$port"
        return 0
    else
        print_error "SpiderFoot failed to start. Check $SF_LOG"
        return 1
    fi
}

start_iveras() {
    local port=$(get_app_port)
    
    # Check if already running
    if is_port_in_use $port; then
        print_warning "Iveras already running on port $port"
        return 0
    fi
    
    print_info "Starting Iveras on port $port..."
    cd "$APP_DIR"
    nohup python3 app.py > "$APP_LOG" 2>&1 &
    echo $! > "$APP_PID_FILE"
    save_app_port "$port"
    
    sleep 3
    
    # Verify it started
    if is_port_in_use $port; then
        print_success "Iveras started on http://localhost:$port"
        return 0
    else
        print_error "Iveras failed to start. Check $APP_LOG"
        return 1
    fi
}

update_spiderfoot_config() {
    local sf_port=$(get_sf_port)
    print_info "Updating Iveras SpiderFoot configuration..."
    
    cd "$APP_DIR"
    python3 << EOF
import os
import sys
sys.path.insert(0, '$APP_DIR')
os.environ.setdefault('FLASK_APP', 'app.py')

from cms.models import Setting, db
from flask import Flask

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///iveras.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'dev-key'

db.init_app(app)

with app.app_context():
    Setting.set('spiderfoot_url', 'http://localhost:$sf_port',
               description='SpiderFoot server URL', category='spiderfoot')
    print('SpiderFoot URL updated to http://localhost:$sf_port')
EOF
    
    print_success "Iveras SpiderFoot config updated"
}

start_all() {
    print_header
    echo " Starting Services"
    echo "----------------------------------------"
    
    # Cleanup old instances
    cleanup_old_instances
    
    # Ask for ports if not configured
    if [ ! -f "$APP_CONFIG" ] || [ ! -f "$SF_CONFIG" ]; then
        ask_ports
    else
        local app_port=$(get_app_port)
        local sf_port=$(get_sf_port)
        echo " Using configured ports:"
        echo "  Iveras:    $app_port"
        echo "  SpiderFoot: $sf_port"
        echo ""
        
        read -p "Use these ports? (Y/n): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Nn]$ ]]; then
            ask_ports
        fi
    fi
    
    echo ""
    echo " Starting services..."
    echo ""
    
    # Start SpiderFoot first
    start_spiderfoot
    
    # Start Iveras
    start_iveras
    
    # Update Iveras config with SpiderFoot URL
    update_spiderfoot_config
    
    # Final status
    sleep 1
    echo ""
    show_status
}

#######################################
# Stop Functions
#######################################

stop_spiderfoot() {
    local port=$(get_sf_port)
    
    if kill_on_port $port "SpiderFoot"; then
        return 0
    fi
    
    # Try PID file
    if [ -f "$SF_PID_FILE" ]; then
        local pid=$(cat "$SF_PID_FILE")
        if kill -0 $pid 2>/dev/null; then
            kill $pid 2>/dev/null
            rm -f "$SF_PID_FILE"
            print_success "SpiderFoot stopped"
            return 0
        fi
    fi
    
    # Try process name
    kill_by_name "sf.py" "SpiderFoot"
    return 0
}

stop_iveras() {
    local port=$(get_app_port)
    
    if kill_on_port $port "Iveras"; then
        return 0
    fi
    
    # Try PID file
    if [ -f "$APP_PID_FILE" ]; then
        local pid=$(cat "$APP_PID_FILE")
        if kill -0 $pid 2>/dev/null; then
            kill $pid 2>/dev/null
            rm -f "$APP_PID_FILE"
            print_success "Iveras stopped"
            return 0
        fi
    fi
    
    # Try process name
    kill_by_name "python.*app.py" "Iveras"
    return 0
}

stop_all() {
    print_header
    echo " Stopping Services"
    echo "----------------------------------------"
    echo ""
    
    stop_iveras
    stop_spiderfoot
    
    echo ""
    print_success "All services stopped"
    echo ""
}

#######################################
# Status Functions
#######################################

show_status() {
    print_header
    echo " Service Status"
    echo "----------------------------------------"
    echo ""
    
    local app_port=$(get_app_port)
    local sf_port=$(get_sf_port)
    
    # Check Iveras
    echo -n "  Iveras App:   "
    if is_port_in_use $app_port; then
        local pids=$(get_pid_for_port $app_port)
        echo -e "${GREEN}RUNNING${NC} (port $app_port, PID: $pids)"
    else
        echo -e "${RED}STOPPED${NC} (port $app_port)"
    fi
    
    # Check SpiderFoot
    echo -n "  SpiderFoot:   "
    if is_port_in_use $sf_port; then
        local pids=$(get_pid_for_port $sf_port)
        echo -e "${GREEN}RUNNING${NC} (port $sf_port, PID: $pids)"
    else
        echo -e "${RED}STOPPED${NC} (port $sf_port)"
    fi
    
    echo ""
    echo " URLs:"
    if is_port_in_use $app_port; then
        echo -e "  ${BLUE}→${NC} Iveras CMS:    http://localhost:$app_port/cms"
        echo -e "  ${BLUE}→${NC} OSINT Tools:   http://localhost:$app_port"
    fi
    if is_port_in_use $sf_port; then
        echo -e "  ${BLUE}→${NC} SpiderFoot:   http://localhost:$sf_port"
    fi
    
    echo ""
}

#######################################
# Help
#######################################

show_help() {
    print_header
    echo " Usage"
    echo "----------------------------------------"
    echo ""
    echo "  ./start.sh              Interactive mode (asks for ports)"
    echo "  ./start.sh start        Start both services"
    echo "  ./start.sh stop         Stop both services"
    echo "  ./start.sh restart      Restart both services"
    echo "  ./start.sh status       Show status of both services"
    echo "  ./start.sh app          Start Iveras only (asks for port)"
    echo "  ./start.sh app <port>   Start Iveras on specific port"
    echo "  ./start.sh sf           Start SpiderFoot only (asks for port)"
    echo "  ./start.sh sf <port>    Start SpiderFoot on specific port"
    echo "  ./start.sh ports        Show configured ports"
    echo "  ./start.sh setports     Reconfigure ports"
    echo "  ./start.sh cleanup      Kill old instances only"
    echo ""
    echo " Current Ports:"
    echo "  Iveras:    $(get_app_port)"
    echo "  SpiderFoot: $(get_sf_port)"
    echo ""
}

#######################################
# Main Command Handler
#######################################

case "${1:-interactive}" in
    interactive|i)
        start_all
        ;;
    start|s)
        start_all
        ;;
    stop|k)
        stop_all
        ;;
    restart|r)
        stop_all
        sleep 2
        start_all
        ;;
    status|st)
        show_status
        ;;
    app|a)
        if [ -n "$2" ]; then
            save_app_port "$2"
        fi
        if [ -z "$2" ] && [ ! -f "$APP_CONFIG" ]; then
            print_header
            read -p "Iveras App Port [$(get_app_port)]: " port
            port="${port:-$(get_app_port)}"
            save_app_port "$port"
        fi
        cleanup_old_instances
        start_iveras
        ;;
    sf|spiderfoot|spider)
        if [ -n "$2" ]; then
            save_sf_port "$2"
        fi
        if [ -z "$2" ] && [ ! -f "$SF_CONFIG" ]; then
            print_header
            read -p "SpiderFoot Port [$(get_sf_port)]: " port
            port="${port:-$(get_sf_port)}"
            save_sf_port "$port"
        fi
        cleanup_old_instances
        start_spiderfoot
        ;;
    ports|p)
        echo "Configured ports:"
        echo "  Iveras:    $(get_app_port)"
        echo "  SpiderFoot: $(get_sf_port)"
        ;;
    setports|c)
        ask_ports
        ;;
    cleanup|c)
        cleanup_old_instances
        ;;
    help|h|--help|-h)
        show_help
        ;;
    *)
        echo "Unknown command: $1"
        echo "Run './start.sh help' for usage information"
        exit 1
        ;;
esac
