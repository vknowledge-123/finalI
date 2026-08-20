#!/usr/bin/env bash
set -euo pipefail

# Installs a weekday 09:00 IST systemd timer that restarts the AshuChart
# services and warms the Dhan sector cache for user 1.

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo: sudo bash install_daily_restart_timer.sh" >&2
  exit 1
fi

APP_DIR="${APP_DIR:-/opt/finalI}"
APP_USER="${APP_USER:-ashuchart}"
USER_ID="${USER_ID:-1}"
RESTART_TIME="${RESTART_TIME:-09:00:00}"
RESTART_SCRIPT="/usr/local/bin/restart-ashuchart.sh"
DAILY_SCRIPT="/usr/local/bin/ashuchart-daily-restart.sh"
LOG_FILE="/var/log/ashuchart-daily-restart.log"

if command -v timedatectl >/dev/null 2>&1; then
  timedatectl set-timezone Asia/Kolkata
fi

if [ ! -x "$RESTART_SCRIPT" ]; then
  cat > "$RESTART_SCRIPT" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
systemctl restart ashuchart-api.service
systemctl restart ashuchart-alert.service
systemctl restart ashuchart-execution.service
systemctl restart ashuchart-market-feed.service
systemctl restart ashuchart-reconciliation.service
EOF
  chmod 0755 "$RESTART_SCRIPT"
fi

cat > "$DAILY_SCRIPT" <<EOF
#!/usr/bin/env bash
set -euo pipefail

LOG_FILE="$LOG_FILE"
USER_ID="$USER_ID"

{
  echo "==== \$(date --iso-8601=seconds) daily AshuChart restart started ===="
  systemctl daemon-reload
  "$RESTART_SCRIPT"
  sleep 20
  curl -fsS -X POST "http://127.0.0.1:8005/api/sectors/cache-dhan" \\
    -H "Content-Type: application/json" \\
    -d "{\\"user_id\\":\${USER_ID}}" || true
  echo
  echo "==== \$(date --iso-8601=seconds) daily AshuChart restart finished ===="
} >> "\$LOG_FILE" 2>&1
EOF
chmod 0755 "$DAILY_SCRIPT"
touch "$LOG_FILE"
chown "$APP_USER:$APP_USER" "$LOG_FILE" 2>/dev/null || true

cat > /etc/systemd/system/ashuchart-daily-restart.service <<EOF
[Unit]
Description=Daily AshuChart service restart and sector cache warmup
Wants=network-online.target
After=network-online.target redis-server.service

[Service]
Type=oneshot
WorkingDirectory=$APP_DIR
ExecStart=$DAILY_SCRIPT
EOF

cat > /etc/systemd/system/ashuchart-daily-restart.timer <<EOF
[Unit]
Description=Run AshuChart daily restart at $RESTART_TIME IST on weekdays

[Timer]
OnCalendar=Mon..Fri *-*-* $RESTART_TIME
Persistent=true
Unit=ashuchart-daily-restart.service

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now ashuchart-daily-restart.timer
systemctl list-timers ashuchart-daily-restart.timer --no-pager

echo "Installed ashuchart-daily-restart.timer for $RESTART_TIME Asia/Kolkata."
echo "Log file: $LOG_FILE"
