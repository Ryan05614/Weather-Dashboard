#!/bin/zsh
set -e

APP_DIR="$HOME/weather-dashboard-qt"
PY312="$(brew --prefix python@3.12)/bin/python3.12"
VENV_PY="$APP_DIR/.venv/bin/python"

cd "$APP_DIR" || { echo "Folder not found: $APP_DIR"; exit 1; }

# Create venv + install deps if needed
if [ ! -x "$VENV_PY" ]; then
  echo "Setting up environment..."
  "$PY312" -m venv .venv
  "$VENV_PY" -m pip install -U pip "PySide6<6.10" requests
fi

# Launch
exec "$VENV_PY" weather_dashboard.py
