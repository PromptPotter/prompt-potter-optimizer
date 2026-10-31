#!/usr/bin/env bash
# update.sh — the only command you run after a change. Idempotent: pulls latest,
# refreshes deps, rebuilds the webapp, and restarts the app (+ its backend, if
# configured). Force-matches origin, so it never stalls on a diverged box —
# tracked files mirror the remote; .env / runtime data are gitignored and survive.
#
#   ./update.sh                    # the tracked branch's tip
#   ./update.sh v0.8.17            # a tag, branch or commit on origin — a release deploy names its tag
#   ./update.sh v0.8.17 --reset    # and, where the landing code cannot load some run data, drop ALL
#                                  # run data: every campaign, cycle and draft of every account
#
# Exits 0 landed and healthy; 1 not landed (the old code runs again) or a service down after a
# landing; 3 a roll-back that failed, with the commit the checkout is really on printed.
set -euo pipefail
REF=""
RESET=""
for arg in "$@"; do
  if [[ "$arg" == --reset ]]; then RESET=1; else REF="$arg"; fi
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "$HERE/deploy.config" ]] && source "$HERE/deploy.config"
APP_NAME="${APP_NAME:-myapp}"
SERVICE_NAME="${SERVICE_NAME:-$APP_NAME}"
BOT_SERVICE_NAME="${BOT_SERVICE_NAME:-$SERVICE_NAME-admin-bot}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/$APP_NAME/your-repo}"
WEBAPP_DIR="${WEBAPP_DIR:-webapp}"
BIND_PORT="${BIND_PORT:-8001}"
HEALTH_PATH="${HEALTH_PATH:-/api/v1/health}"
BACKEND_DIR="${BACKEND_DIR:-}"
BACKEND_SERVICE="${BACKEND_SERVICE:-}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
# The data root, which the unit carries as `Environment=PROMPTPOTTER_HOME` (install-service.sh) and
# every command run BY HAND from the checkout must be handed explicitly. Unset = the tree still
# lives under $INSTALL_DIR and the default resolves to it.
DATA_DIR="${DATA_DIR:-}"
# The file systemd actually loads — see deploy.config.example. Branding any other one repaints
# nothing.
ENV_FILE="${ENV_FILE:-$INSTALL_DIR/.env}"

# set_env_kv + the brand fan-out, shared with bootstrap.sh.
source "$HERE/brand-env.sh"
# wait_healthy — shared with every other script that starts something.
source "$HERE/health.sh"
source "$HERE/uv.sh"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
ok()   { printf '  \033[1;32m✓\033[0m %s\n' "$*"; }
bad()  { printf '  \033[1;31m✗\033[0m %s\n' "$*"; }
# The commit $2 names on origin after the fetch; a branch name is origin's copy, never the box's.
resolve_ref() {
  if [[ -z "$2" ]]; then git -C "$1" rev-parse --verify --quiet '@{u}^{commit}'; return; fi
  git -C "$1" rev-parse --verify --quiet "refs/remotes/origin/$2^{commit}" \
    || git -C "$1" rev-parse --verify --quiet "refs/tags/$2^{commit}" \
    || git -C "$1" rev-parse --verify --quiet "$2^{commit}"
}
# Fetch and print the ONE commit of origin $2 names: a deploy that cannot name its commit cannot be checked.
fetch_target() {
  git -C "$1" fetch --quiet --tags origin
  resolve_ref "$1" "$2" || { bad "'$2' names nothing on origin — a tag, a branch or a commit" >&2; exit 1; }
}
# Mirror commit $2 and say which.
checkout() {
  git -C "$1" reset --hard --quiet "$2"
  ok "$(basename "$1") at $(git -C "$1" describe --tags --always) ($(git -C "$1" rev-parse --short HEAD))"
}
# A verb under the unit's environment: this shell holds neither its keys, its bot token nor its data
# root, so a verb run here would judge whatever the checkout's .env happens to hold.
as_unit() {
  sudo systemd-run --quiet --wait --pipe --collect --uid="$(id -un)" \
    -p EnvironmentFile="$ENV_FILE" -p WorkingDirectory="$INSTALL_DIR" \
    -E PROMPTPOTTER_HOME="${DATA_DIR:-$INSTALL_DIR/.promptpotter}" \
    "$INSTALL_DIR/.venv/bin/python" -m promptpotter "$@"
}
has_bot() { systemctl cat "$BOT_SERVICE_NAME.service" >/dev/null 2>&1; }
# The admin bot is a SEPARATE unit running the SAME checkout, so a sync that touched its code leaves
# it on the old one until something restarts it. Silent, because a bot still answering /blocked
# looks healthy. Skipped where it was never installed.
start_services() {
  sudo systemctl restart "$SERVICE_NAME" || return 1
  if has_bot; then sudo systemctl restart "$BOT_SERVICE_NAME" || return 1; fi
}
build_webapp() {
  if [[ -f "$ENV_FILE" ]]; then brand_write_env "$ENV_FILE" || return 1; fi
  brand_export_webapp || return 1
  if [[ -d "$INSTALL_DIR/$WEBAPP_DIR" ]]; then
    ( cd "$INSTALL_DIR/$WEBAPP_DIR" && npm ci --silent && npm run build ) || return 1
  fi
}

# --- preflight: refuse the placeholder fallback -----------------------------
# Without deploy.config, INSTALL_DIR defaults to a generic placeholder that
# doesn't exist — fail with the fix instead of a cryptic `git -C` error.
if [[ ! -f "$HERE/deploy.config" ]]; then
  bad "no deploy.config — create it first:"
  echo "    cp $HERE/deploy.config.example $HERE/deploy.config && \$EDITOR $HERE/deploy.config"
  exit 1
fi
[[ -d "$INSTALL_DIR" ]] || { bad "INSTALL_DIR not found: $INSTALL_DIR — fix INSTALL_DIR in $HERE/deploy.config"; exit 1; }

# --- app: sync → deps → webapp → restart ------------------------------------
say "app: $INSTALL_DIR"
self_sha() { sha256sum "$HERE/update.sh" 2>/dev/null | cut -d' ' -f1; }
self_before="$(self_sha)"
# The re-exec'd copy below runs on a checkout this block already landed.
if [[ -z "${UPDATE_REEXECED:-}" ]]; then
  target="$(fetch_target "$INSTALL_DIR" "$REF")"
  # A run is its own process and outlives the restart (`KillMode=process`), so the landed code would
  # read the ledger shapes an older producer still writes. The RUNNING code asks before anything
  # moves, and on a yes closes admission to all but $target until an API on it starts.
  if ! landing_out="$(as_unit close-admission "$target")"; then
    printf '%s\n' "$landing_out"
    bad "landing check crashed — nothing changed"
    exit 1
  fi
  if [[ "$landing_out" != closed* ]]; then
    printf '%s\n' "$landing_out"
    bad "not landed: a live run is on other code — nothing changed; re-run once it pauses or ends"
    exit 1
  fi
  checkout "$INSTALL_DIR" "$target"
  # The checkout can replace THIS script, and bash keeps reading the descriptor it already opened —
  # so the rest of the run would be the old copy. Re-exec the new copy once, guarded against a loop.
  if [[ -n "$self_before" && "$(self_sha)" != "$self_before" ]]; then
    ok "update.sh changed in this checkout — re-running the new copy"
    export UPDATE_REEXECED=1
    exec bash "$HERE/update.sh" "$@"
  fi
fi
# Install the exact pinned graph from uv.lock — same source of truth as CI. Two syncs: the first
# `--inexact`, removing nothing, only so the synced code can run; the second exact, once the datasets
# this box offers (DATASET_OFFER) have named the extras their fetch needs.
ensure_uv
( cd "$INSTALL_DIR" && "$UV" sync --frozen --inexact --extra all )
# A roll-back that cannot finish: the box is wherever this says, its services stopped.
stuck() {
  bad "roll-back failed: $1"
  bad "checkout at $(git -C "$INSTALL_DIR" rev-parse HEAD 2>/dev/null || echo 'no readable HEAD'); services stopped"
  exit 3
}
# Puts back the commit the checkout left in ORIG_HEAD and starts it. Called after `||`, where
# `set -e` is off for the whole body, so every step is checked here.
roll_back() {
  bad "not landed: $1"
  local from verdict
  from="$(git -C "$INSTALL_DIR" rev-parse --verify ORIG_HEAD)" || stuck "ORIG_HEAD names no commit"
  git -C "$INSTALL_DIR" reset --hard --quiet "$from" || stuck "the checkout of $from failed"
  ( cd "$INSTALL_DIR" && "$UV" sync --frozen --inexact --extra all ) || stuck "the sync of $from failed"
  if [[ -n "${WEBAPP_TOUCHED:-}" ]]; then build_webapp || stuck "the webapp build of $from failed"; fi
  verdict="$(as_unit close-admission "$from")" || stuck "close-admission on $from crashed"
  [[ "$verdict" == closed* ]] || { printf '%s\n' "$verdict"; stuck "admission stays closed to the landing"; }
  start_services || stuck "the services on $from did not start"
  ok "back on $(git -C "$INSTALL_DIR" describe --tags --always)"
  exit 1
}
# Nothing writes the data root while the landed code proves it and a reset drops from it. A run is
# its own process and stays up; `reset` refuses while one is live.
sudo systemctl stop "$SERVICE_NAME"
if has_bot; then sudo systemctl stop "$BOT_SERVICE_NAME"; fi
# A survivor the landed code reshaped is migrated, never wiped. Each migration is idempotent and runs
# at every landing, so what the old code writes after a roll-back below is carried at the next one.
migrate_out="$(as_unit migrate)" || { printf '%s\n' "$migrate_out"; roll_back "a migration failed"; }
ok "$migrate_out"
# Before the restart, the landed code loads every file under the data root. A survivor that will not
# load has no migration yet; run data or a cache that will not goes with `--reset`.
# `prove-data` exits 3 on a file that does not load (`machine_status.py::PROOF_REFUSED`); any other
# failure is a crash, and rolls back as one.
proof_rc=0
proof_out="$(as_unit prove-data)" || proof_rc=$?
if (( proof_rc != 0 )); then
  printf '%s\n' "$proof_out"
  (( proof_rc == 3 )) || roll_back "the proof crashed (exit $proof_rc)"
  if grep -qvE '^(unknown |unloadable (RUN|CACHE) )' <<< "$proof_out"; then
    roll_back "a survivor above does not load under the landing code — its reshape ships no migration"
  fi
  [[ -n "$RESET" ]] || roll_back "run data above does not load under the landing code — re-run with --reset to drop it"
  as_unit reset --all-tenants --yes || roll_back "the reset above failed"
  proof_out="$(as_unit prove-data)" || { printf '%s\n' "$proof_out"; roll_back "the data root still does not load after the reset"; }
fi
ok "$proof_out"
# The offer is read from the file the UNIT loads and handed in, as PROMPTPOTTER_HOME is below: this
# shell does not load $ENV_FILE. The verb refuses a name naming no dataset, before the exact sync
# drops what that name's extras were.
offer_out="$(cd "$INSTALL_DIR" && DATASET_OFFER="$(read_env_kv "$ENV_FILE" DATASET_OFFER)" .venv/bin/python -m promptpotter offer-extras)" \
  || roll_back "DATASET_OFFER names what this code cannot offer"
offer_extras=()
for extra in $offer_out; do offer_extras+=(--extra "$extra"); done
( cd "$INSTALL_DIR" && "$UV" sync --frozen --extra all "${offer_extras[@]}" ) || roll_back "the exact sync failed"
ok "synced; offered datasets' extras: ${offer_extras[*]:-none}"
# A dep rebuild relabels new .venv files user_home_t; re-apply the persisted
# bin_t rule (set by install-service.sh) so systemd can still exec them under
# SELinux enforcing — otherwise the service silently 203/EXEC crash-loops.
if command -v getenforce >/dev/null 2>&1 && [[ "$(getenforce)" == "Enforcing" ]]; then
  sudo restorecon -RF "$INSTALL_DIR/.venv/bin" || roll_back "restorecon failed"
fi
# Brand rides every update: a sync overwrites tracked files, and the webapp bakes
# NEXT_PUBLIC_* in at build time — so skipping this would silently repaint the
# install upstream at the next deploy.
WEBAPP_TOUCHED=1
build_webapp || roll_back "the webapp build failed"
start_services || roll_back "the services did not start on the landed code"

# --- backend (optional): sync → deps → restart ------------------------------
if [[ -n "$BACKEND_DIR" ]]; then
  say "backend: $BACKEND_DIR"
  # REF names a commit of THIS repo; the backend rides its own tracked tip.
  checkout "$BACKEND_DIR" "$(fetch_target "$BACKEND_DIR" "")"
  [[ -x "$BACKEND_DIR/.venv/bin/pip" ]] && "$BACKEND_DIR/.venv/bin/pip" install -q -r "$BACKEND_DIR/requirements.txt"
  [[ -n "$BACKEND_SERVICE" ]] && sudo systemctl restart "$BACKEND_SERVICE"
fi

# --- health -----------------------------------------------------------------
# These decide the EXIT CODE, and they are the only things that do. `bad` only prints, so
# until this counter existed the script ended 0 while announcing `✗ app down` — a deploy that left
# the service dead reported success to whoever ran it and to anything reading `$?`.
say "health"
down=0
if wait_healthy "http://127.0.0.1:$BIND_PORT$HEALTH_PATH"; then ok "app up ($BIND_PORT)"; else bad "app down — journalctl -u $SERVICE_NAME -e"; down=1; fi
# The bot has no port to probe — outbound-only is the charter — so `is-active` IS its health.
if systemctl cat "$BOT_SERVICE_NAME.service" >/dev/null 2>&1; then
  if systemctl is-active --quiet "$BOT_SERVICE_NAME"; then ok "admin bot up"; else bad "admin bot down — journalctl -u $BOT_SERVICE_NAME -e"; down=1; fi
fi
# Everything the box answers for that /health cannot see (a backend, a sign-in provider's view of
# oidc.json): the verb exits 1 on a broken one, and records and tells it as the bot would.
health_out="$(as_unit health)" || down=1
while read -r state row; do
  if [[ "$state" == held ]]; then ok "$row"; elif [[ -n "$state" ]]; then bad "$row"; fi
done <<< "$health_out"
if [[ -n "$BACKEND_DIR" ]]; then
  if wait_healthy "http://127.0.0.1:$BACKEND_PORT/status"; then ok "backend up ($BACKEND_PORT)"; else bad "backend down — journalctl -u ${BACKEND_SERVICE:-?} -e"; down=1; fi
fi
exit "$down"
