#!/usr/bin/env bash
# Installs the admin bot as a systemd service (ADR-0004 operator-admin channel).
# The bot long-polls Telegram OUTBOUND-ONLY and edits the local sign-in blocklist
# — it opens no inbound port. Idempotent: re-running refreshes + restarts.
set -euo pipefail

# --- config + knobs -----------------------------------------------------
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "$HERE/deploy.config" ]] && source "$HERE/deploy.config"
APP_NAME="${APP_NAME:-myapp}"
SERVICE_NAME="${SERVICE_NAME:-$APP_NAME}"
BOT_SERVICE_NAME="${BOT_SERVICE_NAME:-$SERVICE_NAME-admin-bot}"
ADMIN_BOT_MODULE="${ADMIN_BOT_MODULE:-myapp.presentation.admin_bot}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/$APP_NAME/your-repo}"
RUN_USER="${RUN_USER:-$USER}"
# Where systemd reads the environment from — NOT necessarily where you edit it. systemd loads
# EnvironmentFile as root before dropping privileges, and on an SELinux box a file under $HOME is
# labelled `user_home_t`, which root is denied. The unit then fails with "unavailable resources"
# and the journal line "Failed to load environment files: Permission denied". Point this at a
# path under /etc (label `etc_t`) there.
ENV_FILE="${ENV_FILE:-$INSTALL_DIR/.env}"
# ------------------------------------------------------------------------

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mxx \033[0m %s\n' "$*" >&2; exit 1; }

[[ -d "$INSTALL_DIR/.venv" ]] || die "no .venv at $INSTALL_DIR — run bootstrap.sh first"
[[ -f "$ENV_FILE" ]] || die "no env file at $ENV_FILE — run bootstrap.sh first, or set ENV_FILE in deploy.config"

# Read as ROOT, because that is who systemd reads it as. Checking it as the login user passes on a
# file the service will then be denied, which is the whole failure this preflight exists to catch.
sudo grep -q '^ADMIN_BOT_TELEGRAM_TOKEN=' "$ENV_FILE" \
    || die "ADMIN_BOT_TELEGRAM_TOKEN not in $ENV_FILE — see docs/operations/secure-hosting.md"
sudo grep -q '^ADMIN_BOT_CHAT_ID=' "$ENV_FILE" \
    || die "ADMIN_BOT_CHAT_ID not in $ENV_FILE — see docs/operations/secure-hosting.md"

# ADMIN_BOT_MODULE is the one knob whose default is a PLACEHOLDER (`myapp.…`), so a deploy that
# never set it installs a unit whose ExecStart cannot resolve. systemd reports that as
# "unavailable resources" and auto-restarts forever, naming neither the module nor the file to
# fix. Resolve it here, where the answer is one line in deploy.config.
"$INSTALL_DIR/.venv/bin/python" -c "import $ADMIN_BOT_MODULE" 2>/dev/null \
    || die "ADMIN_BOT_MODULE=$ADMIN_BOT_MODULE does not import — set it in deploy-linux/deploy.config"

UNIT_FILE=/etc/systemd/system/$BOT_SERVICE_NAME.service
say "writing $UNIT_FILE (user=$RUN_USER)"

sudo tee "$UNIT_FILE" >/dev/null <<EOF
[Unit]
Description=$APP_NAME admin bot (outbound-only operator-admin channel)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$ENV_FILE
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

printf '\n\033[1;32madmin bot installed.\033[0m\n\n'
cat <<EOF
useful commands:
  systemctl status $BOT_SERVICE_NAME        # is it running?
  journalctl -u $BOT_SERVICE_NAME -f        # live logs
  sudo systemctl restart $BOT_SERVICE_NAME  # after rotating the token

now message your bot:
  /blocked
  /block someone@example.com

upgrading from the pre-blocklist unit? the old one is a DIFFERENT service and is
still installed — retire it once, or it competes for the same getUpdates offset:
  sudo systemctl disable --now $SERVICE_NAME-allowlist-bot
  sudo rm -f /etc/systemd/system/$SERVICE_NAME-allowlist-bot.service && sudo systemctl daemon-reload
EOF
