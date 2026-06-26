#!/bin/bash
# Local dev: runs the FastAPI backend (:8000) and the Vite dev server (:5173)
# concurrently. The Vite dev server proxies /api -> :8000 (see vite.config.ts).
# Adapted from the qc-selfservice-v3 dev.sh house pattern.

set +e
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; CYAN='\033[0;36m'; RED='\033[0;31m'; NC='\033[0m'

cleanup() {
  echo -e "\n${YELLOW}Shutting down...${NC}"
  [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null
  [ -n "$FRONTEND_PID" ] && kill "$FRONTEND_PID" 2>/dev/null
  exit 0
}
trap cleanup SIGINT SIGTERM

echo -e "${BLUE}== Regression Validation Platform - dev ==${NC}"

# Ports are offset by +10 from the Vite/uvicorn defaults to avoid clashing with
# other local projects (backend 8010, frontend 5183). Keep in sync with
# vite.config.ts (server.port + /api proxy target).
BACKEND_PORT=8010
FRONTEND_PORT=5183

# Free the ports we use.
for port in $BACKEND_PORT $FRONTEND_PORT; do
  pid=$(lsof -ti:$port 2>/dev/null)
  [ -n "$pid" ] && { echo -e "${YELLOW}Freeing port $port (pid $pid)${NC}"; kill -9 "$pid" 2>/dev/null; }
done

# Python venv.
if [ ! -d "backend/venv" ]; then
  echo -e "${CYAN}Creating backend venv...${NC}"
  python3 -m venv backend/venv
fi
PY="backend/venv/bin/python"
if ! "$PY" -c "import fastapi" 2>/dev/null; then
  echo -e "${CYAN}Installing backend dependencies...${NC}"
  "$PY" -m pip install --upgrade pip -q
  "$PY" -m pip install -r backend/requirements.txt
fi

# Frontend deps.
if [ ! -d "node_modules" ]; then
  echo -e "${CYAN}Installing frontend dependencies...${NC}"
  npm install
fi

# Start backend (uvicorn, reload).
echo -e "${GREEN}-> backend  http://localhost:${BACKEND_PORT}  (docs at /docs)${NC}"
( cd backend && exec venv/bin/uvicorn app.main:app --reload --port "$BACKEND_PORT" ) > backend.log 2>&1 &
BACKEND_PID=$!
sleep 3

# Start frontend (vite). --port keeps it in sync with vite.config.ts.
echo -e "${GREEN}-> frontend http://localhost:${FRONTEND_PORT}${NC}"
npm run dev -- --port "$FRONTEND_PORT" > frontend.log 2>&1 &
FRONTEND_PID=$!

echo -e "\n${CYAN}Logs: tail -f backend.log frontend.log${NC}"
echo -e "${YELLOW}Ctrl+C to stop.${NC}\n"
wait $BACKEND_PID $FRONTEND_PID
