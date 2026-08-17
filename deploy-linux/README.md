# Linux deploy via Cloudflare Tunnel

End state:
- Linux box runs `uvicorn $APP_MODULE` on `127.0.0.1:8001` under **systemd**
  (auto-restart, survives reboot).
- `cloudflared` runs as another systemd service, exposing it at
  `https://$PUBLIC_HOSTNAME` over Cloudflare's HTTPS edge.
- No open ports on your router, no static IP, free.

## Adopter config (do this first)

All four scripts read their values from `deploy.config` (gitignored), falling back
to generic placeholders if it's absent. Copy the example and fill in your own app:

```bash
cd deploy-linux
cp deploy.config.example deploy.config
$EDITOR deploy.config        # set APP_NAME, APP_MODULE, REPO_URL, PUBLIC_HOSTNAME, …
```

Every value can also be overridden inline for a one-off, e.g.
`PUBLIC_HOSTNAME=staging.example.com ./install-tunnel.sh`.

**Running it under your own name?** The file's `--- brand ---` block is the one
declaration: `brand-env.sh` writes the engine's copy into `.env` and exports the
webapp's `NEXT_PUBLIC_*` twins before the build, on both `bootstrap.sh` and
`update.sh` — so editing the block and re-deploying repaints the install, and an
update never repaints it back. Anything *outside* that block (the package, the
CLI verb, the `.promptpotter/` state tree) is a different tier with a real cost:
[`docs/developer/whitelabel.md`](../docs/developer/whitelabel.md).

## One-time prep (on Cloudflare's side, ~3 min)

Cloudflare must be the authoritative DNS for `$PUBLIC_HOSTNAME`. On the Free plan
that means the **whole parent zone** lives at Cloudflare (a subdomain-only zone is
refused on Free) — add `<your-domain>` as a site and point its nameservers at
Cloudflare. Once the zone shows **Active**, `install-tunnel.sh` creates the
`$PUBLIC_HOSTNAME` CNAME for you via `cloudflared tunnel route dns`.

## Run on the Linux box, in order

```bash
# 0. copy this folder onto the box (e.g. via scp), cd into it
cd ~/deploy-linux
chmod +x *.sh         # the Windows filesystem strips the executable bit
cp deploy.config.example deploy.config && $EDITOR deploy.config   # if not done already

# 1. clone repo, install deps, build webapp:
./bootstrap.sh
# → edits .env interactively, prompts for the Groq/OpenAI key

# 2. install uvicorn as a systemd service (will start on boot)
./install-service.sh

# 3. install + register cloudflared tunnel, route the hostname
./install-tunnel.sh
```

After step 3, `https://$PUBLIC_HOSTNAME` should load the dashboard at the root.

## Backend service (the optimizer needs one)

The optimizer drives a separate backend over `/matches` — clone it as a **git sibling** (not a release zip), then run it on `127.0.0.1:8000`:

```bash
git clone <backend>.git ~/potter/<backend>; cd ~/potter/<backend>/backend-api
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && cp .env.example .env && $EDITOR .env
```
Wrap it in a systemd unit like `install-service.sh` (`ExecStart … uvicorn main:app --port 8000`); register it via PromptPotter's `POST /backends`.

## Defaults you can override

Set these in `deploy.config` (or pass on the command line):

| var | default | meaning |
|---|---|---|
| `APP_NAME` | `myapp` | slug → systemd unit, tunnel name, install-dir, Description |
| `APP_MODULE` | `myapp.main:app` | uvicorn ASGI target |
| `ADMIN_BOT_MODULE` | `myapp.presentation.admin_bot` | admin-bot module (optional) |
| `REPO_URL` | `…/CHANGE-ME/your-repo.git` | git clone source — **edit before bootstrap** |
| `INSTALL_DIR` | `$HOME/$APP_NAME/your-repo` | where the repo lands |
| `RUN_USER` | `$USER` | systemd `User=` |
| `BIND_HOST` | `127.0.0.1` | uvicorn host (don't change unless you also expose LAN) |
| `BIND_PORT` | `8001` | uvicorn port |
| `WEBAPP_DIR` | `webapp` | static frontend dir built by `npm run build` |
| `HEALTH_PATH` | `/api/v1/health` | liveness endpoint the scripts curl |
| `TUNNEL_NAME` | `$APP_NAME` | cloudflared tunnel name |
| `PUBLIC_HOSTNAME` | `app.example.com` | public hostname to route |
| `ALLOWED_ORIGINS` | `https://app.example.com` | CORS origin written into `.env` |
| `OIDC_CALLBACK_PATH` | `/api/v1/auth/callback/google` | OAuth callback path |

## What's where after install

| thing | path |
|---|---|
| repo | `$INSTALL_DIR` |
| Python venv | `$INSTALL_DIR/.venv` |
| webapp build | `$INSTALL_DIR/$WEBAPP_DIR/out/` |
| env file (secrets) | `$ENV_FILE`, default `$INSTALL_DIR/.env` — **0600 perms, don't commit**. Seeded by bootstrap, named as `EnvironmentFile` by the app unit and, unless `BOT_ENV_FILE` splits it, by the bot too; under SELinux it must move to `/etc` (see `deploy.config.example`) |
| writable surface | `$DATA_DIR` when set (campaigns, measurements, the run readout), else `$INSTALL_DIR`. Only the first stops the service being able to rewrite its own source, venv and env file |
| uvicorn unit | `/etc/systemd/system/$APP_NAME.service` |
| cloudflared config | `~/.cloudflared/config.yml` + `~/.cloudflared/<UUID>.json` |
| logs (uvicorn) | `journalctl -u $APP_NAME -f` |
| logs (tunnel) | `journalctl -u cloudflared -f` |

## Verifying

```bash
# local — uvicorn alive
curl http://127.0.0.1:8001/api/v1/health

# tunnel up
sudo systemctl status cloudflared

# from outside
curl -I https://$PUBLIC_HOSTNAME/api/v1/health
```

## Updating later — one command

`update.sh` is the whole routine after any change: it mirrors origin, refreshes
deps, rebuilds the webapp, restarts the app — and does the same for the backend
when `BACKEND_DIR` is set in `deploy.config`. Re-runnable; never stalls on a
diverged box (tracked files are force-matched to origin; `.env`/runtime survive).
The sync can replace the script mid-run, so it re-execs the new copy once and says
so — a fix to `update.sh` itself takes effect on the deploy that ships it. It
restarts the admin bot but never rewrites its unit: a change to that unit (its data
root, its env file) needs `./install-admin-bot.sh`.
It needs `deploy.config` (same one from setup) and aborts with the fix if it's
missing — without it there's no real `INSTALL_DIR` to act on.

The closing health line polls for up to 30s (`health.sh::wait_healthy`) rather
than probing once, because uvicorn takes a few seconds to bind and the old
one-shot check reported `✗ app down` on deploys that were fine. So a red cross
there now means it really did not come up.

```bash
cd "$INSTALL_DIR/deploy-linux" && ./update.sh   # deploy-linux lives inside the repo
```

`./update.sh: Permission denied`? The exec bit didn't survive the clone — run
`bash update.sh` once; the pull it does restores `100755` for next time.

## Security posture

> The full model + the post-install hardening checklist (systemd unit, PP↔TermNorm
> token, firewall decision) is [`docs/operations/access-model.md`](../docs/operations/access-model.md)
> § Deploy actions. This section is the perimeter summary.

Stage-1 OIDC. Provider config: `.promptpotter/identity/oidc.json`. Signing up grants
access, so what bounds a stranger is `FREE_TIER_SPEND_CAP_USD`, not an approval queue;
`.promptpotter/identity/blocklist.json` is the revoke (re-read on every request — edits
are instant, no restart). Set `HOST_ADMIN_EMAIL` in `.env` or nothing ever claims this
box, and `HOST_ADMIN_ISSUER` beside it so the claim is pinned to one provider rather than to an
address any wired provider could assert. Don't stack Cloudflare Access — double-gate.

**The one rule:** a control-plane change never has an inbound door open to the
internet. The blocklist is your front-door lock; editing it is a privileged action,
so it is **not** exposed as a public endpoint. Instead an **on-box admin bot**
reaches *out* to Telegram (long-poll, no open port, nothing new to attack) and edits
the local file — the zero-trust / Purdue posture (protected zone never reachable from
the lowest-trust zone). Full rationale:
[`docs/adr/0004-operator-admin-channels.md`](../docs/adr/0004-operator-admin-channels.md).

### Block someone from your phone

```bash
# .env (0600, never committed):
#   ADMIN_BOT_TELEGRAM_TOKEN=...   (from @BotFather; the API sends sign-in alerts on it too,
#                                   so it stays here even after a BOT_ENV_FILE split)
#   ADMIN_BOT_CHAT_ID=...          (your numeric chat id, locks the bot to you)
#   ADMIN_BOT_PASSPHRASE=...       (optional 2nd factor; move to BOT_ENV_FILE — bot-only)
./install-admin-bot.sh            # systemd service, outbound-only, auto-restart
```

Then message the bot `/block them@example.com`, `/unblock ...`, `/blocked`. Changes are
audited to `.promptpotter/identity/blocklist_audit.jsonl`. Step-by-step + secret
hygiene: [`docs/operations/access-model.md`](../docs/operations/access-model.md).

| logs (admin bot) | `journalctl -u $APP_NAME-admin-bot -f` |

## Uninstall

```bash
sudo systemctl disable --now $APP_NAME
sudo rm /etc/systemd/system/$APP_NAME.service
sudo cloudflared service uninstall
cloudflared tunnel delete $TUNNEL_NAME
rm -rf $INSTALL_DIR ~/.cloudflared
```
