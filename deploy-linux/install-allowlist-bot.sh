#!/usr/bin/env bash
# Installs the allowlist admin bot as a systemd service (ADR-0004 operator-admin
# channel). The bot long-polls Telegram OUTBOUND-ONLY and edits the local sign-in
# allowlist — it opens no inbound port. Idempotent: re-running refreshes + restarts.
set -euo pipefail

# --- config + knobs -----------------------------------------------------
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "$HERE/deploy.config" ]] && source "$HERE/deploy.config"
APP_NAME="${APP_NAME:-myapp}"
SERVICE_NAME="${SERVICE_NAME:-$APP_NAME}"
BOT_SERVICE_NAME="${BOT_SERVICE_NAME:-$SERVICE_NAME-allowlist-bot}"
ADMIN_BOT_MODULE="${ADMIN_BOT_MODULE:-myapp.presentation.admin_bot}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/$APP_NAME/your-repo}"
RUN_USER="${RUN_USER:-$USER}"
# ------------------------------------------------------------------------

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mxx \033[0m %s\n' "$*" >&2; exit 1; }

[[ -d "$INSTALL_DIR/.venv" ]] || die "no .venv at $INSTALL_DIR — run bootstrap.sh first"
[[ -f "$INSTALL_DIR/.env" ]]  || die "no .env at $INSTALL_DIR — run bootstrap.sh first"

grep -q '^ADMIN_BOT_TELEGRAM_TOKEN=' "$INSTALL_DIR/.env" \
    || die "ADMIN_BOT_TELEGRAM_TOKEN not in .env — see docs/operations/secure-hosting.md"
grep -q '^ADMIN_BOT_CHAT_ID=' "$INSTALL_DIR/.env" \
    || die "ADMIN_BOT_CHAT_ID not in .env — see docs/operations/secure-hosting.md"

UNIT_FILE=/etc/systemd/system/$BOT_SERVICE_NAME.service
say "writing $UNIT_FILE (user=$RUN_USER)"

sudo tee "$UNIT_FILE" >/dev/null <<EOF
[Unit]
Description=$APP_NAME allowlist admin bot (outbound-only operator-admin channel)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$INSTALL_DIR/.env
ExecStart=$INSTALL_DIR/.venv/bin/python -m $ADMIN_BOT_MODULE
Restart=on-failure
RestartSec=5s
# Hardening — the bot only needs to read .env + write the identity dir.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=$INSTALL_DIR
[Install]
WantedBy=multi-user.target
EOF

say "reloading systemd, enabling + starting $BOT_SERVICE_NAME.service"
sudo systemctl daemon-reload
sudo systemctl enable "$BOT_SERVICE_NAME.service"
sudo systemctl restart "$BOT_SERVICE_NAME.service"

sleep 2
if systemctl is-active --quiet "$BOT_SERVICE_NAME.service"; then
    say "bot is running (outbound-only; no new listening port by design)"
else
    die "bot failed to start — see: journalctl -u $BOT_SERVICE_NAME -e"
fi

printf '\n\033[1;32mallowlist admin bot installed.\033[0m\n\n'
cat <<EOF
useful commands:
  systemctl status $BOT_SERVICE_NAME        # is it running?
  journalctl -u $BOT_SERVICE_NAME -f        # live logs
  sudo systemctl restart $BOT_SERVICE_NAME  # after rotating the token

now message your bot:
  /allow you@example.com
  /list
EOF
