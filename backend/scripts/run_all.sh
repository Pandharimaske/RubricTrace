#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

echo "Starting RubricTrace development environment..."
echo "This will start both the backend API and React frontend"
echo ""

# Start backend in background
echo "Starting backend API on http://127.0.0.1:8000..."
./backend/scripts/run_api.sh &
BACKEND_PID=$!

# Wait a moment for backend to start
sleep 3

# Start frontend
echo "Starting React frontend on http://localhost:5173..."
./backend/scripts/run_ui.sh &
FRONTEND_PID=$!

echo ""
echo "========================================"
echo "RubricTrace is now running!"
echo "Backend: http://127.0.0.1:8000"
echo "Frontend: http://localhost:5173"
echo "API Docs: http://127.0.0.1:8000/docs"
echo "========================================"
echo ""
echo "Press Ctrl+C to stop both services"

# Handle cleanup on exit
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; echo 'Stopping services...'; exit" INT TERM

# Wait for both processes
wait
