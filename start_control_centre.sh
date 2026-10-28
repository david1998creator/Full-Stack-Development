#!/usr/bin/env bash
set -e

# Pin PYTHONPATH to src directory
export PYTHONPATH="src:${PYTHONPATH}"

echo "===================================================="
echo "Starting AgentHub - AgentOps Control Console"
echo "Open your browser at: http://127.0.0.1:8000"
echo "===================================================="

python ui/server.py
