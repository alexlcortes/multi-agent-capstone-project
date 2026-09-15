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

exec npx n8n start
