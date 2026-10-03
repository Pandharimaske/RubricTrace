#!/usr/bin/env bash
set -euo pipefail

server_pid=""
if ! curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
	ollama serve &
	server_pid=$!
fi

if [[ -n "$server_pid" ]]; then
	trap 'kill "$server_pid" 2>/dev/null || true' EXIT
fi

ollama pull "${RUBRICTRACE_VLM_MODEL:-qwen2.5vl:3b}"
ollama pull "${RUBRICTRACE_LLM_MODEL:-qwen2.5:3b}"
