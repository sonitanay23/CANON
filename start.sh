#!/bin/bash
# One command to set up and run Canon locally: ./start.sh
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "First run: setting up Python environment..."
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q -r requirements.txt
if [ "$1" = "check" ]; then
  python -m canon.check_key
  exit
fi
PORT=${PORT:-5050}
echo ""
echo "Canon is starting. Open http://localhost:$PORT in your browser."
echo "Press Ctrl+C here to stop it."
echo ""
PORT=$PORT python app.py
