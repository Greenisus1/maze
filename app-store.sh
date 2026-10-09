#!/bin/bash
# pi-app-store: 1
# pi-app-store-category: games-3d
set -eu
cd -- "$(dirname -- "$0")"
case "${1:-}" in
 install) command -v python3 >/dev/null || { echo 'Python3 is needed.'; exit 1; }; python3 -c 'from pathlib import Path; compile(Path("maze.py").read_bytes(), "maze.py", "exec")' ;;
 run) shift; exec python3 maze.py "$@" ;;
 *) echo 'Use: bash app-store.sh install OR bash app-store.sh run'; exit 1 ;;
esac
