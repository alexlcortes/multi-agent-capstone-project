#!/usr/bin/env bash
# Loads secrets from .env and injects them into n8n via CREDENTIALS_OVERWRITE_DATA,
# so API keys never have to be typed into the n8n UI/credential store directly.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "Missing .env — copy .env.example to .env and fill in your real key first." >&2
  exit 1
fi
set -a
source .env
set +a

if [ -z "${OPENAI_API_KEY:-}" ]; then
  echo "OPENAI_API_KEY is not set in .env" >&2
  exit 1
fi

export CREDENTIALS_OVERWRITE_DATA
CREDENTIALS_OVERWRITE_DATA=$(node -e 'console.log(JSON.stringify({openAiApi:{apiKey:process.env.OPENAI_API_KEY}}))')
export N8N_USER_FOLDER="$(pwd)/.n8n"

# Reliability layer (Step 8 of the capstone guide): Code nodes append structured
# run-record events to ./logs/runs.jsonl (relative to this dir, which is n8n's cwd
# since we cd here above). Code nodes can require('fs') but cannot read $env or
# process.env (n8n blocks both by default), so the log path is a relative literal
# in each node's code, not read from an env var.
mkdir -p "$(pwd)/logs"
export NODE_FUNCTION_ALLOW_BUILTIN="fs"

exec npx n8n start
