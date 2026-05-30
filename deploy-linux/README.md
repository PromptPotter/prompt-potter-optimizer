# PromptPotter — Linux deploy via Cloudflare Tunnel → app.promptpotter.dev

End state:
- Linux box runs `uvicorn promptpotter.main:app` on `127.0.0.1:8001` under **systemd** (auto-restart, survives reboot).
- `cloudflared` runs as another systemd service, exposing it at `https://app.promptpotter.dev` over Cloudflare's HTTPS edge.
- No open ports on your router, no static IP, free.

## One-time prep (on Cloudflare's side, takes ~3 min)

Cloudflare must be the authoritative DNS for `app.promptpotter.dev`. Two options:

1. **Move the whole `promptpotter.dev` zone to Cloudflare** (easiest if Vercel isn't deeply wired into DNS-level things). Add the domain at https://dash.cloudflare.com, point the registrar's nameservers at the two CF nameservers Cloudflare gives you. Wait for propagation (a few minutes to a few hours).
2. **Delegate just `app.promptpotter.dev`** (keeps Vercel DNS for the root). Add `app.promptpotter.dev` as a *separate zone* at Cloudflare, then at Vercel DNS create NS records for the host `app` pointing to the two CF nameservers. This is "subdomain delegation."

Either works. Pick 1 unless you have something brittle on Vercel DNS.

## Run on the Linux box, in order

```bash
# 0. copy this folder onto the box (e.g. via scp), cd into it
cd ~/deploy-linux
chmod +x *.sh         # the Windows filesystem strips the executable bit

# 1. edit REPO_URL at the top of bootstrap.sh (it currently says CHANGE-ME),
#    then clone repo, install deps, build webapp:
./bootstrap.sh
# → edits .env interactively, prompts for the Groq/OpenAI key

# 2. install uvicorn as a systemd service (will start on boot)
./install-service.sh

# 3. install + register cloudflared tunnel, route the hostname
./install-tunnel.sh
```

After step 3, `https://app.promptpotter.dev` should redirect to `/ui/` and load the dashboard.

## Defaults you can override

Each script reads these env vars (with sensible defaults):

| var | default | meaning |
|---|---|---|
| `REPO_URL` | `git@github.com:<your-handle>/prompt-potter-optimizer.git` | git clone source — **edit before running bootstrap.sh** |
| `INSTALL_DIR` | `$HOME/prompt-potter-optimizer` | where the repo lands |
| `RUN_USER` | `$USER` | systemd `User=` |
| `BIND_HOST` | `127.0.0.1` | uvicorn host (don't change unless you also expose LAN) |
| `BIND_PORT` | `8001` | uvicorn port |
| `TUNNEL_NAME` | `potter` | cloudflared tunnel name |
| `PUBLIC_HOSTNAME` | `app.promptpotter.dev` | public hostname to route |

Override on the command line: `PUBLIC_HOSTNAME=foo.example.com ./install-tunnel.sh`.

## What's where after install

| thing | path |
|---|---|
| repo | `$INSTALL_DIR` |
| Python venv | `$INSTALL_DIR/.venv` |
| webapp build | `$INSTALL_DIR/webapp/out/` |
| `.env` (secrets) | `$INSTALL_DIR/.env` — **0600 perms, don't commit** |
| uvicorn unit | `/etc/systemd/system/promptpotter.service` |
| cloudflared config | `~/.cloudflared/config.yml` + `~/.cloudflared/<UUID>.json` |
| logs (uvicorn) | `journalctl -u promptpotter -f` |
| logs (tunnel) | `journalctl -u cloudflared -f` |

## Verifying

```bash
# local — uvicorn alive
curl http://127.0.0.1:8001/api/v1/health

# tunnel up
sudo systemctl status cloudflared

# from outside
curl -I https://app.promptpotter.dev/api/v1/health
```

## Updating the app later

```bash
cd $INSTALL_DIR
git pull
source .venv/bin/activate
pip install -e ".[all,dev]"
cd webapp && npm install && npm run build && cd ..
sudo systemctl restart promptpotter
```

## Security posture

Stage-1 OIDC. Provider config: `.promptpotter/identity/oidc.json`. Email gate: `.promptpotter/identity/allowlist.json` (re-read on every sign-in — edits are instant, no restart). Don't stack Cloudflare Access — double-gate.

**The one rule:** a control-plane change never has an inbound door open to the internet. The allowlist is your front-door lock; editing it is a privileged action, so it is **not** exposed as a public endpoint. Instead an **on-box admin bot** reaches *out* to Telegram (long-poll, no open port, nothing new to attack) and edits the local file — the zero-trust / Purdue posture (protected zone never reachable from the lowest-trust zone). Full rationale: [`docs/adr/0004-operator-admin-channels.md`](../docs/adr/0004-operator-admin-channels.md).

### Manage the allowlist from your phone

```bash
# .env (0600, never committed):
#   ADMIN_BOT_TELEGRAM_TOKEN=...   (from @BotFather)
#   ADMIN_BOT_CHAT_ID=...          (your numeric chat id, locks the bot to you)
#   ADMIN_BOT_PASSPHRASE=...       (optional 2nd factor; prefix commands with it)
./install-allowlist-bot.sh        # systemd service, outbound-only, auto-restart
```

Then message the bot `/allow you@example.com`, `/deny ...`, `/list`. Changes are audited to `.promptpotter/identity/allowlist_audit.jsonl`. Step-by-step + secret hygiene: [`docs/operations/secure-hosting.md`](../docs/operations/secure-hosting.md).

| logs (allowlist bot) | `journalctl -u promptpotter-allowlist-bot -f` |

## Uninstall

```bash
sudo systemctl disable --now promptpotter
sudo rm /etc/systemd/system/promptpotter.service
sudo cloudflared service uninstall
cloudflared tunnel delete potter
rm -rf $INSTALL_DIR ~/.cloudflared
```
