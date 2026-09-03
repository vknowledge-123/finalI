#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-/etc/ashuchart.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Environment file not found: $ENV_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

if [[ -z "${REDIS_URL:-}" ]]; then
  echo "REDIS_URL is missing in $ENV_FILE" >&2
  exit 1
fi

redis-cli -u "$REDIS_URL" DEL auth:admin auth:totp:admin >/dev/null

redis-cli -u "$REDIS_URL" --scan --pattern 'auth:session:*' | while IFS= read -r key; do
  [[ -n "$key" ]] && redis-cli -u "$REDIS_URL" DEL "$key" >/dev/null
done

redis-cli -u "$REDIS_URL" --scan --pattern 'auth:login_attempts:*' | while IFS= read -r key; do
  [[ -n "$key" ]] && redis-cli -u "$REDIS_URL" DEL "$key" >/dev/null
done

echo "Admin login, TOTP, sessions, and login attempts reset."
