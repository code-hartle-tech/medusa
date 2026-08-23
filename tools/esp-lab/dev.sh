#!/usr/bin/env bash
# Start the Medusa Wi-Fi Lab: backend (4300) + Vite client (5250).
set -e
cd "$(dirname "$0")"

[ -d server/node_modules ] || (cd server && npm install)
[ -d client/node_modules ] || (cd client && npm install)

( cd server && node server.js ) &
BACK=$!
trap 'kill "$BACK" 2>/dev/null' EXIT INT TERM

cd client && npm run dev
