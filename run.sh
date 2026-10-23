#!/usr/bin/env bash
set -e

# Change directory to the script's home
cd "$(dirname "$0")"

PORT=5001
URL="http://localhost:${PORT}"

echo "=========================================================="
echo "  🚀 Starting Video Inspiration Engine on ${URL}"
echo "=========================================================="

# Check if server is already running
if curl -s "http://localhost:${PORT}" > /dev/null 2>&1; then
    echo "Server is already running at ${URL}!"
    if command -v xdg-open > /dev/null 2>&1; then
        echo "Opening default browser at ${URL}..."
        xdg-open "${URL}" > /dev/null 2>&1 &
    fi
    exit 0
fi
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment in .venv..."
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    sha256sum requirements.txt 2>/dev/null | cut -d' ' -f1 > .venv/installed_requirements.sha || true
else
    # Auto-sync dependencies if requirements.txt changes
    CURRENT_REQ_SHA=$(sha256sum requirements.txt 2>/dev/null | cut -d' ' -f1 || echo "")
    PREV_REQ_SHA=$(cat .venv/installed_requirements.sha 2>/dev/null || echo "")
    if [ -n "$CURRENT_REQ_SHA" ] && [ "$CURRENT_REQ_SHA" != "$PREV_REQ_SHA" ]; then
        echo "Detected updates in requirements.txt. Installing new dependencies..."
        .venv/bin/pip install -q -r requirements.txt
        echo "$CURRENT_REQ_SHA" > .venv/installed_requirements.sha
    fi
fi

# Ensure database is initialized
.venv/bin/python database.py > /dev/null 2>&1 || true

# Cleanup trap on Ctrl+C or kill
cleanup() {
    echo ""
    echo "Shutting down Video Inspiration server (PID: $SERVER_PID)..."
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
    echo "Goodbye!"
    exit 0
}
trap cleanup SIGINT SIGTERM EXIT

# Start Flask server
.venv/bin/python app.py &
SERVER_PID=$!

# Wait for server to come online
echo "Waiting for web server to start..."
for i in {1..20}; do
    if curl -s "http://localhost:${PORT}" > /dev/null 2>&1; then
        echo "Server is live at ${URL}!"
        break
    fi
    sleep 0.5
done

# Try opening the browser
if command -v xdg-open > /dev/null 2>&1; then
    echo "Opening default browser at ${URL}..."
    xdg-open "${URL}" > /dev/null 2>&1 &
fi

# Wait for server process
wait "$SERVER_PID"
