#!/usr/bin/env bash
# start_frlg_online.command — launcher for "FRLG_Companion_App - Online.py" on macOS
# Mirrors your working start_app.command style.

set -euo pipefail

cd "$(dirname "$0")"

APP="FRLG_Companion_App - Online.py"
if [[ ! -f "$APP" ]]; then
  if [[ -f "FRLG_Companion_App.py" ]]; then
    APP="FRLG_Companion_App.py"
  elif [[ -f "app.py" ]]; then
    APP="app.py"
  else
    echo "❌ Could not find: 'FRLG_Companion_App - Online.py', 'FRLG_Companion_App.py', or 'app.py' in: $(pwd)"
    read -n1 -p "Press any key to close..."; echo
    exit 1
  fi
fi

# Pick a system python3 to create the venv
PY_SYS="${PYTHON_BIN:-python3}"
if ! command -v "$PY_SYS" >/dev/null 2>&1; then
  for cand in "/opt/homebrew/bin/python3" "/usr/local/bin/python3" "/usr/bin/python3"; do
    if [[ -x "$cand" ]]; then PY_SYS="$cand"; break; fi
  done
fi
if ! command -v "$PY_SYS" >/dev/null 2>&1; then
  echo "❌ python3 not found. Install Python 3 and retry."
  read -n1 -p "Press any key to close..."; echo
  exit 1
fi

# Create / resolve venv
if [[ ! -d ".venv" ]]; then
  "$PY_SYS" -m venv .venv
fi
VENV_PY="$(pwd)/.venv/bin/python3"
[[ -x "$VENV_PY" ]] || VENV_PY="$(pwd)/.venv/bin/python"
if [[ ! -x "$VENV_PY" ]]; then
  echo "❌ Could not find python inside .venv/bin. Venv creation failed."
  read -n1 -p "Press any key to close..."; echo
  exit 1
fi

# Strip possible Windows line endings on the app file
if command -v sed >/dev/null 2>&1; then sed -i '' $'s/\r$//' "$APP" 2>/dev/null || true; fi

# Upgrade pip tooling
"$VENV_PY" -m pip install --upgrade pip setuptools wheel >/dev/null

# Install deps
if [[ -f "requirements.txt" ]]; then
  echo "📦 Installing requirements.txt ..."
  "$VENV_PY" -m pip install -r requirements.txt
else
  echo "📦 Installing Streamlit ..."
  "$VENV_PY" -m pip install streamlit
fi

# Pick a free port
PORT="${PORT:-}"
if [[ -z "$PORT" ]]; then
  for try in 8501 8502 8503 8510 8520; do
    if ! lsof -i :"$try" >/dev/null 2>&1; then PORT="$try"; break; fi
  done
  PORT="${PORT:-8501}"
fi

# Kill quarantine bits so Finder double-click actually works from Downloads
xattr -dr com.apple.quarantine . >/dev/null 2>&1 || true

echo "🚀 Launching $APP on http://localhost:${PORT}"
( sleep 1; open "http://localhost:${PORT}" ) >/dev/null 2>&1 &

# Run Streamlit using the venv python (don’t rely on PATH)
exec "$VENV_PY" -m streamlit run "$APP" --server.port "$PORT" --server.headless false --browser.gatherUsageStats false
