#!/usr/bin/env bash
# Installs the admin bot as a systemd service (the operator-admin channel, docs/decisions.md § 0004).
# The bot long-polls Telegram OUTBOUND-ONLY and edits the local sign-in blocklist
# — it opens no inbound port. Idempotent: re-running refreshes + restarts.
set -euo pipefail

# --- config + knobs -----------------------------------------------------
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "$HERE/deploy.config" ]] && source "$HERE/deploy.config"
source "$HERE/hardening.sh"
APP_NAME="${APP_NAME:-myapp}"
SERVICE_NAME="${SERVICE_NAME:-$APP_NAME}"
BOT_SERVICE_NAME="${BOT_SERVICE_NAME:-$SERVICE_NAME-admin-bot}"
ADMIN_BOT_MODULE="${ADMIN_BOT_MODULE:-myapp.presentation.admin_bot}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/$APP_NAME/your-repo}"
RUN_USER="${RUN_USER:-$USER}"
# The data root, which the app's unit already carries (install-service.sh). The bot reads AND writes
# the same identity tree, and `config/paths.py::user_data_root` falls back to the CHECKOUT's
# `.promptpotter/` when this is unset — so a bot without it answers `/spend` with "No accounts on
# this install" over a populated box, and lands a `/block` in a tree the app never reads, replying
# success. Unset = the tree still lives under $INSTALL_DIR, which is where it began.
DATA_DIR="${DATA_DIR:-}"
# Both follow the root: the cwd because it is what the fallback above resolves from, and the write
# surface because the identity dir the bot edits moves with the root. Keeping ReadWritePaths on the
# checkout would grant write to the CODE and withhold it from the one directory the bot exists to
# edit — wrong in both directions at once.
WORK_DIR="${DATA_DIR:-$INSTALL_DIR}"
WRITE_PATH="${DATA_DIR:-$INSTALL_DIR}"
# Where systemd reads the environment from — NOT necessarily where you edit it. systemd loads
# EnvironmentFile as root before dropping privileges, and on an SELinux box a file under $HOME is
# labelled `user_home_t`, which root is denied. The unit then fails with "unavailable resources"
# and the journal line "Failed to load environment files: Permission denied". Point this at a
# path under /etc (label `etc_t`) there.
ENV_FILE="${ENV_FILE:-$INSTALL_DIR/.env}"
# THE BOT'S OWN env file, for what only the bot reads: the passphrase that authorizes inbound
# commands, and the briefing feed's URL and secret. Defaults to the app's, so leaving it unset
# changes nothing. The token and chat id stay in the app's file alone, because the API sends on the
# same bot; the unit loads the app's file first and this one over it (docs/decisions.md § 0004).
BOT_ENV_FILE="${BOT_ENV_FILE:-$ENV_FILE}"
# ------------------------------------------------------------------------

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!! \033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx \033[0m %s\n' "$*" >&2; exit 1; }

[[ -d "$INSTALL_DIR/.venv" ]] || die "no .venv at $INSTALL_DIR — run bootstrap.sh first"
[[ -f "$ENV_FILE" ]] || die "no env file at $ENV_FILE — run bootstrap.sh first, or set ENV_FILE in deploy.config"
[[ -f "$BOT_ENV_FILE" ]] || die "no env file at $BOT_ENV_FILE — set BOT_ENV_FILE in deploy.config, or unset it"

# Read as ROOT, because that is who systemd reads it as. Checking it as the login user passes on a
# file the service will then be denied, which is the whole failure this preflight exists to catch.
# `=.` and not `=`: a key present but empty reads exactly like a key that was never set, and the
# bot's own check cannot tell the operator which of the two they did.
for key in ADMIN_BOT_TELEGRAM_TOKEN ADMIN_BOT_CHAT_ID; do
    sudo grep -q "^$key=." "$ENV_FILE" \
        || die "$key not set in the app's $ENV_FILE — see deploy-linux/README.md § Admin bot"
done

if [[ "$BOT_ENV_FILE" != "$ENV_FILE" ]]; then
    # The passphrase is the second factor on inbound commands, and only the bot reads it: a copy in
    # the API's environment turns a read of that process into command authority.
    sudo grep -q '^ADMIN_BOT_PASSPHRASE=.' "$ENV_FILE" 2>/dev/null \
        && warn "ADMIN_BOT_PASSPHRASE is STILL in the app's $ENV_FILE. Delete that line and restart $SERVICE_NAME."
    # A token in both files is two homes: a re-issued one pasted into one leaves the other refused,
    # which is how a box once went ten days with every operator notice unheard.
    for key in ADMIN_BOT_TELEGRAM_TOKEN ADMIN_BOT_CHAT_ID; do
        sudo grep -q "^$key=" "$BOT_ENV_FILE" 2>/dev/null \
            && warn "$key is also in $BOT_ENV_FILE, over the app's. Delete it there; the bot reads the app's file."
    done
fi

# ADMIN_BOT_MODULE is the one knob whose default is a PLACEHOLDER (`myapp.…`), so a deploy that
# never set it installs a unit whose ExecStart cannot resolve. systemd reports that as
# "unavailable resources" and auto-restarts forever, naming neither the module nor the file to
# fix. Resolve it here, where the answer is one line in deploy.config. The import holds the data
# root, so it is handed the unit's root, and its stderr names a refused one as itself.
PROMPTPOTTER_HOME="${DATA_DIR:-$INSTALL_DIR/.promptpotter}" "$INSTALL_DIR/.venv/bin/python" -c "import $ADMIN_BOT_MODULE" \
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
WorkingDirectory=$WORK_DIR
EnvironmentFile=$ENV_FILE
$([[ "$BOT_ENV_FILE" != "$ENV_FILE" ]] && echo "EnvironmentFile=$BOT_ENV_FILE")
${DATA_DIR:+Environment=PROMPTPOTTER_HOME=$DATA_DIR}
ExecStart=$INSTALL_DIR/.venv/bin/python -m $ADMIN_BOT_MODULE
Restart=on-failure
RestartSec=5s
$(unit_hardening "$WRITE_PATH")

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
