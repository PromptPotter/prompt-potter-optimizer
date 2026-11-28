#!/usr/bin/env bash
# verb.sh — THE door every `python -m promptpotter` verb on the box takes, by hand or from update.sh
# (which sources it, so `as_unit` outlives a checkout that drops this file): run as the unit runs,
# under its data root, its hardening and its box key, handed as the same systemd credential (never a
# copy), so a verb reading an account's keys opens them. A login shell holds none of the three, so a
# verb run there would judge a tree the service never reads.
#
#   ./verb.sh trail ada@example.com
#   ./verb.sh post-bulletin "The next deploy wipes run data." --pops-up
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "$HERE/deploy.config" ]] && source "$HERE/deploy.config"
APP_NAME="${APP_NAME:-myapp}"
SERVICE_NAME="${SERVICE_NAME:-$APP_NAME}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/$APP_NAME/your-repo}"
# The data root the unit carries as `Environment=PROMPTPOTTER_HOME` (install-service.sh); unset = the
# tree still lives under $INSTALL_DIR.
DATA_DIR="${DATA_DIR:-}"
# The box key the unit holds as its credential (install-service.sh): a landing seals under it.
BOX_KEY="${BOX_KEY:-/etc/$SERVICE_NAME/box.key}"
# unit_hardening — the units' block, which `as_unit` runs every verb under.
source "$HERE/hardening.sh"

as_unit() {
  local line props=()
  while IFS= read -r line; do
    [[ -z "$line" || "$line" == \#* ]] || props+=(-p "$line")
  done < <(unit_hardening "${DATA_DIR:-$INSTALL_DIR}")
  sudo systemd-run --quiet --wait --pipe --collect --uid="$(id -un)" "${props[@]}" \
    -p WorkingDirectory="$INSTALL_DIR" \
    -E PROMPTPOTTER_HOME="${DATA_DIR:-$INSTALL_DIR/.promptpotter}" \
    -p LoadCredential=box.key:"$BOX_KEY" \
    /bin/sh -c 'BOX_KEY_FILE="$CREDENTIALS_DIRECTORY/box.key" exec "$0" -m promptpotter "$@"' \
    "$INSTALL_DIR/.venv/bin/python" "$@"
}

# Run, not sourced: the verb its arguments name.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  set -euo pipefail
  as_unit "$@"
fi
