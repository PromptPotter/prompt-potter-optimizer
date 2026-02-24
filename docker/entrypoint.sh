#!/bin/bash
set -e

# PromptPotter Optimizer - Entrypoint Script
# Starts both JupyterLab and FastAPI server

echo "Starting PromptPotter Optimizer..."

# Start FastAPI server in background
echo "Starting FastAPI server on port ${API_PORT:-8001}..."
uvicorn api.main:app --host ${API_HOST:-0.0.0.0} --port ${API_PORT:-8001} &
FASTAPI_PID=$!

# Wait for FastAPI to be ready
sleep 2

# Start JupyterLab in foreground
echo "Starting JupyterLab on port 8888..."
jupyter lab \
    --ip=0.0.0.0 \
    --port=8888 \
    --no-browser \
    --allow-root \
    --ServerApp.token="${JUPYTER_TOKEN:-}" \
    --ServerApp.password="" \
    --ServerApp.allow_origin="*" \
    --ServerApp.allow_remote_access=True \
    --ServerApp.terminado_settings='{"shell_command": ["/bin/bash"]}' &
JUPYTER_PID=$!

echo ""
echo "============================================"
echo "PromptPotter Optimizer is running!"
echo "============================================"
echo "JupyterLab:  http://localhost:8888"
echo "FastAPI:     http://localhost:${API_PORT:-8001}"
echo "Swagger UI:  http://localhost:${API_PORT:-8001}/docs"
echo "============================================"
echo ""

# Handle shutdown gracefully
trap "echo 'Shutting down...'; kill $FASTAPI_PID $JUPYTER_PID 2>/dev/null; exit 0" SIGTERM SIGINT

# Wait for both processes
wait $FASTAPI_PID $JUPYTER_PID
