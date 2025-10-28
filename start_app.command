#!/bin/bash
cd "$(dirname "$0")"
if [ -d ".venv" ]; then
  source .venv/bin/activate
else
  python3 -m venv .venv
  source .venv/bin/activate
fi
python3 -m pip install --upgrade pip >/dev/null 2>&1
python3 -m pip install streamlit >/dev/null 2>&1
exec streamlit run app.py
