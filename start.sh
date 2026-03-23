#!/bin/bash
cd "$(dirname "$0")"

# Kill any process using port 5000
lsof -ti:5000 | xargs kill -9 2>/dev/null

# Wait a moment for port to be released
sleep 1

# Start the app
python3 app.py
