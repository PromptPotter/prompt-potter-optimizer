#!/usr/bin/env bash
# Installs cloudflared, registers a named tunnel, writes its config, routes the
# public hostname to it, then runs it as a systemd service.
#
# Prereq: Cloudflare must be the authoritative DNS for $PUBLIC_HOSTNAME — see
# the "One-time prep" section of README.md.
set -euo pipefail

# --- knobs --------------------------------------------------------------
TUNNEL_NAME="${TUNNEL_NAME:-potter}"
PUBLIC_HOSTNAME="${PUBLIC_HOSTNAME:-app.promptpotter.dev}"
BIND_HOST="${BIND_HOST:-127.0.0.1}"
BIND_PORT="${BIND_PORT:-8001}"
# ------------------------------------------------------------------------

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!! \033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx \033[0m %s\n' "$*" >&2; exit 1; }

# --- 1. install cloudflared ---------------------------------------------
if command -v cloudflared >/dev/null 2>&1; then
    say "cloudflared already installed: $(cloudflared --version | head -1)"
else
    arch="$(uname -m)"
    case "$arch" in
        x86_64)  pkg=cloudflared-linux-amd64 ;;
        aarch64) pkg=cloudflared-linux-arm64 ;;
        armv7l)  pkg=cloudflared-linux-arm   ;;
        *)       die "unsupported arch: $arch" ;;
    esac
    say "downloading $pkg"
    sudo curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/$pkg" \
        -o /usr/local/bin/cloudflared
    sudo chmod +x /usr/local/bin/cloudflared
fi

# --- 2. login (opens browser; only needed first time) -------------------
CERT_PATH="$HOME/.cloudflared/cert.pem"
if [[ ! -f "$CERT_PATH" ]]; then
    say "you'll now be asked to log in to Cloudflare (a URL will be printed)."
    say "open it in any browser, pick the zone that owns $PUBLIC_HOSTNAME, authorize."
    cloudflared tunnel login
    [[ -f "$CERT_PATH" ]] || die "login did not produce $CERT_PATH"
else
    say "$CERT_PATH exists — login already done"
fi

# --- 3. create the tunnel (idempotent) ----------------------------------
if cloudflared tunnel list 2>/dev/null | awk '{print $2}' | grep -qx "$TUNNEL_NAME"; then
    say "tunnel '$TUNNEL_NAME' already exists — reusing"
else
    say "creating tunnel '$TUNNEL_NAME'"
    cloudflared tunnel create "$TUNNEL_NAME"
fi

TUNNEL_UUID="$(cloudflared tunnel list 2>/dev/null | awk -v n="$TUNNEL_NAME" '$2==n {print $1}')"
[[ -n "$TUNNEL_UUID" ]] || die "could not resolve UUID for tunnel '$TUNNEL_NAME'"
say "tunnel UUID: $TUNNEL_UUID"

# --- 4. write config.yml ------------------------------------------------
CFG_DIR="$HOME/.cloudflared"
CFG_FILE="$CFG_DIR/config.yml"
say "writing $CFG_FILE"
cat >"$CFG_FILE" <<EOF
tunnel: $TUNNEL_UUID
credentials-file: $CFG_DIR/$TUNNEL_UUID.json

# Edge-side timeouts. The dashboard uses long-poll-style SSE in places;
# generous read timeouts avoid the edge cutting streams mid-flight.
originRequest:
  connectTimeout: 10s
  noTLSVerify: false
  noHappyEyeballs: false
  keepAliveTimeout: 90s

ingress:
  - hostname: $PUBLIC_HOSTNAME
    service: http://$BIND_HOST:$BIND_PORT
  - service: http_status:404
EOF

# --- 5. route DNS -------------------------------------------------------
say "routing $PUBLIC_HOSTNAME → tunnel $TUNNEL_NAME"
# `tunnel route dns` is idempotent: re-running just confirms.
cloudflared tunnel route dns "$TUNNEL_NAME" "$PUBLIC_HOSTNAME" || \
    warn "route dns returned non-zero — usually fine if the CNAME already exists"

# --- 6. systemd ---------------------------------------------------------
# `cloudflared service install` writes /etc/systemd/system/cloudflared.service
# pointing at /etc/cloudflared/config.yml. Mirror our config there.
say "installing cloudflared systemd service"
sudo mkdir -p /etc/cloudflared
sudo cp "$CFG_FILE" /etc/cloudflared/config.yml
sudo cp "$CFG_DIR/$TUNNEL_UUID.json" /etc/cloudflared/

# `service install` errors if already installed — tolerate that.
sudo cloudflared service install 2>&1 | grep -vE "(already installed|file exists)" || true
sudo systemctl daemon-reload
sudo systemctl enable cloudflared
sudo systemctl restart cloudflared

sleep 3
if sudo systemctl is-active --quiet cloudflared; then
    say "cloudflared is up"
else
    die "cloudflared not active — see: journalctl -u cloudflared -e"
fi

# --- 7. end-to-end check ------------------------------------------------
say "checking https://$PUBLIC_HOSTNAME/api/v1/health (may take ~10s for DNS to propagate)"
ok=0
for i in 1 2 3 4 5 6; do
    if curl -fsS --max-time 10 "https://$PUBLIC_HOSTNAME/api/v1/health" >/dev/null; then
        ok=1; break
    fi
    sleep 5
done

if [[ $ok -eq 1 ]]; then
    printf '\n\033[1;32mtunnel is live — https://%s\033[0m\n\n' "$PUBLIC_HOSTNAME"
    cat <<EOF
  - dashboard: https://$PUBLIC_HOSTNAME/ui/
  - api docs:  https://$PUBLIC_HOSTNAME/docs
  - health:    https://$PUBLIC_HOSTNAME/api/v1/health

logs:
  journalctl -u cloudflared -f   # tunnel
  journalctl -u promptpotter -f  # uvicorn

reminder: this is currently public + unauthenticated. See "Security note"
in deploy-linux/README.md for the Cloudflare Access setup.
EOF
else
    warn "could not reach https://$PUBLIC_HOSTNAME yet. DNS may still be propagating."
    warn "verify locally first:  curl http://$BIND_HOST:$BIND_PORT/api/v1/health"
    warn "then check tunnel logs: journalctl -u cloudflared -e"
fi
