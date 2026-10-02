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

# Ports are offset by +10 from the Vite/uvicorn defaults (backend 8010, frontend
# 5183) to avoid clashing with other local projects. Keep the bases in sync with
# vite.config.ts fallbacks.
#
# By default we AUTO-SELECT the first free port pair, scanning 8010/5183,
# 8020/5193, ... so multiple git worktrees can each run ./dev.sh with no config
# and without killing each other's servers. Set RV_PORT_OFFSET to pin the offset
# explicitly instead (e.g. `RV_PORT_OFFSET=20 ./dev.sh` -> backend 8030).
port_free() { ! lsof -ti:"$1" >/dev/null 2>&1; }

if [ -n "${RV_PORT_OFFSET}" ]; then
  OFFSET="$RV_PORT_OFFSET"
else
  OFFSET=""
  for off in 0 10 20 30 40 50 60 70 80 90; do
    if port_free $((8010 + off)) && port_free $((5183 + off)); then
      OFFSET="$off"; break
    fi
  done
  if [ -z "$OFFSET" ]; then
    echo -e "${RED}No free 8010/5183 port pair found (scanned offsets 0..90).${NC}"
    exit 1
  fi
  [ "$OFFSET" != "0" ] && echo -e "${YELLOW}Ports 8010/5183 busy; using offset ${OFFSET}.${NC}"
fi
BACKEND_PORT=$((8010 + OFFSET))
FRONTEND_PORT=$((5183 + OFFSET))
export BACKEND_PORT   # read by vite.config.ts to point the /api proxy back here

# When the offset was pinned explicitly, reclaim those ports if a stale server
# is holding them. (In auto-select mode the ports are already free, so we never
# kill anything — that's what keeps parallel worktrees from murdering each other.)
if [ -n "${RV_PORT_OFFSET}" ]; then
  for port in $BACKEND_PORT $FRONTEND_PORT; do
    pid=$(lsof -ti:$port 2>/dev/null)
    [ -n "$pid" ] && { echo -e "${YELLOW}Freeing port $port (pid $pid)${NC}"; kill -9 "$pid" 2>/dev/null; }
  done
fi

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

# --- Real workspace execution (NO mocking) ---------------------------------
# Point local probes at a REAL Databricks workspace using your CLI OAuth, so
# runs execute for real instead of returning simulated/demo results. Override
# any of these inline, e.g. `DATABRICKS_CONFIG_PROFILE=other ./dev.sh`. To force
# offline/simulated mode, run with `RV_LOCAL_DEMO=1 ./dev.sh`.
if [ "${RV_LOCAL_DEMO}" != "1" ]; then
  # DATABRICKS_PROFILE is what the backend reads (config.py); DATABRICKS_CONFIG_PROFILE
  # is the SDK/CLI env - export both so they stay consistent.
  export DATABRICKS_PROFILE="${DATABRICKS_PROFILE:-myenv}"
  export DATABRICKS_CONFIG_PROFILE="${DATABRICKS_PROFILE}"
  export DATABRICKS_WAREHOUSE_ID="${DATABRICKS_WAREHOUSE_ID:-238e4114cdfd555f}"
  export MODEL_SERVING_ENDPOINT="${MODEL_SERVING_ENDPOINT:-databricks-claude-sonnet-5}"
  echo -e "${CYAN}-> live workspace: profile '${DATABRICKS_PROFILE}' (warehouse ${DATABRICKS_WAREHOUSE_ID})${NC}"
  echo -e "${YELLOW}   (set RV_LOCAL_DEMO=1 to force offline/simulated mode)${NC}"
else
  echo -e "${YELLOW}-> RV_LOCAL_DEMO=1: probes run in simulated/offline mode${NC}"
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
