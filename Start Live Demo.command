#!/bin/zsh
# Double-click to start the DotReply live demo. Close this window to stop it.
cd "$(dirname "$0")"
set -a; . ~/study-assistant/.env; set +a
pkill -f "live/server.py" 2>/dev/null
(sleep 2; open "http://127.0.0.1:8770") &
python3 -I live/server.py
