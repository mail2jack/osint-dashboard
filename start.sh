#!/bin/bash

# Kill any existing Flask processes
echo "Stopping existing Flask processes..."
pkill -9 -f "python.*app.py" 2>/dev/null
sleep 1

# Also kill anything on port 5000
lsof -ti :5000 | xargs kill -9 2>/dev/null
sleep 1

# Start the app
echo "Starting app..."
cd "$(dirname "$0")"
nohup python3 app.py > flask.log 2>&1 &
sleep 4

# Show status
if lsof -i :5000 > /dev/null 2>&1; then
    echo "✓ App started on http://localhost:5000"
    tail -3 flask.log
else
    echo "✗ Failed to start app"
    cat flask.log
fi
