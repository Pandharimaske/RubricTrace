#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."
cd frontend

# Check if node_modules exists, if not install dependencies
if [ ! -d "node_modules" ]; then
    echo "Installing frontend dependencies..."
    npm install
fi

echo "Starting React development server..."
npm run dev
