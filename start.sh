#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Determine bind host: use Tailscale IP if available, otherwise localhost
HOST="${BIND_HOST:-}"
if [ -z "$HOST" ]; then
    TS_IP=$(tailscale ip -4 2>/dev/null || true)
    if [ -n "$TS_IP" ]; then
        HOST="$TS_IP"
    else
        HOST="127.0.0.1"
    fi
fi

# Start FastAPI backend
echo "Starting backend on http://${HOST}:8000 ..."
cd "$SCRIPT_DIR/backend"
if [ -d venv ]; then
    source venv/bin/activate
fi
ALLOWED_ORIGINS="http://${HOST}:3000,http://127.0.0.1:3000,http://localhost:3000" \
    uvicorn app.main:app --host "$HOST" --port 8000 --reload &
BACKEND_PID=$!

# Start React frontend dev server
echo "Starting frontend on http://${HOST}:3000 ..."
cd "$SCRIPT_DIR/frontend"
HOST="$HOST" \
    DANGEROUSLY_DISABLE_HOST_CHECK=true \
    REACT_APP_BACKEND_URL="http://${HOST}:8000" \
    npm start &
FRONTEND_PID=$!

echo ""
echo "Services running:"
echo "  Backend:  http://${HOST}:8000"
echo "  Frontend: http://${HOST}:3000"
echo ""
echo "Press Ctrl+C to stop both services."

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" INT TERM
wait
