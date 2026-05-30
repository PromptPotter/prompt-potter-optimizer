#!/usr/bin/env bash
# Installs the allowlist admin bot as a systemd service (ADR-0004 operator-admin
# channel). The bot long-polls Telegram OUTBOUND-ONLY and edits the local sign-in
# allowlist — it opens no inbound port. Idempotent: re-running refreshes + restarts.
set -euo pipefail

# --- knobs --------------------------------------------------------------
INSTALL_DIR="${INSTALL_DIR:-$HOME/potter/prompt-potter-optimizer}"
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

UNIT_FILE=/etc/systemd/system/promptpotter-allowlist-bot.service
say "writing $UNIT_FILE (user=$RUN_USER)"

sudo tee "$UNIT_FILE" >/dev/null <<EOF
[Unit]
Description=PromptPotter allowlist admin bot (outbound-only operator-admin channel)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$INSTALL_DIR/.env
ExecStart=$INSTALL_DIR/.venv/bin/python -m promptpotter.presentation.admin_bot
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

say "reloading systemd, enabling + starting promptpotter-allowlist-bot.service"
sudo systemctl daemon-reload
sudo systemctl enable promptpotter-allowlist-bot.service
sudo systemctl restart promptpotter-allowlist-bot.service

sleep 2
if systemctl is-active --quiet promptpotter-allowlist-bot.service; then
    say "bot is running (outbound-only; no new listening port by design)"
else
    die "bot failed to start — see: journalctl -u promptpotter-allowlist-bot -e"
fi

printf '\n\033[1;32mallowlist admin bot installed.\033[0m\n\n'
cat <<'EOF'
useful commands:
  systemctl status promptpotter-allowlist-bot        # is it running?
  journalctl -u promptpotter-allowlist-bot -f        # live logs
  sudo systemctl restart promptpotter-allowlist-bot  # after rotating the token

now message your bot:
  /allow you@example.com
  /list
EOF
