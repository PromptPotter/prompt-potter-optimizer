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

## Running it under your own name

**The brand is data; the identity is code.** Anything a customer reads — names, URLs, legal links —
comes from declarations outside the source tree, and upstream never has to know. The package name,
the CLI verb and the on-disk state tree are identity: renaming them costs you every merge from
upstream afterwards. Four renames, increasingly expensive, and **most forks only ever do the first
two**:

| Tier | You change | Costs you |
|---|---|---|
| 0 | the repo name, and fork-vs-mirror | nothing, if you decide it first |
| 1 | `deploy.config`'s `--- brand ---` block → rebuild | nothing — it is data |
| 2 | `deploy.config`'s hostname/unit block | one re-run of the install scripts |
| 3 | the package, the CLI verb, the `.promptpotter/` state tree | merge conflicts forever, and orphaned campaigns |

Do them in order, and **verify sign-in end to end between 2 and 3** — tier 2 is what breaks the OIDC
round trip, and tier 3 makes that breakage much harder to attribute.

**Tier 1.** The `--- brand ---` block is the ONE declaration: `brand-env.sh` writes the engine's copy
into `.env` and exports the webapp's `NEXT_PUBLIC_*` twins before the build, on both `bootstrap.sh`
and `update.sh` — so editing the block and re-deploying repaints the install, and an update never
repaints it back. An unset value is never written, so a half-filled block leaves upstream defaults
standing. Three rules the fields encode: **`PUBLISHER_*` is yours, the provider is not** (the
provider names who *powers* it — provenance, the one field with no override);
**`MARKETING_URL=""` drops the login showcase whole**, so a reseller never funnels its paying users
upstream; and **`TERMS_URL` / `PRIVACY_URL` / `IMPRINT_URL` are separate overrides**, because
clearing the marketing URL must not take the consent links down with it. The webapp inlines its half
at build time, so **the rebuild IS the rename** — there is no runtime brand config to drift. Swapping
the mark is a file swap rather than a config key: [`../BRAND.md`](../BRAND.md) § Replacing the mark.

**Tier 2** is the rest of `deploy.config` — systemd unit, cloudflared tunnel, install dir, public
hostname — which the four `deploy-linux/*.sh` scripts read and nothing else. **Two files the scripts
do not write, and sign-in stays broken until both move:** `.env`'s `ALLOWED_ORIGINS` and
`.promptpotter/identity/oidc.json`'s `redirect_uri`, plus the matching redirect URI in the OAuth
provider's console. The failure is silent from the app's side — the provider rejects the callback, so
nothing on the box logs a cause.

**Tier 3** renames the `promptpotter` package, its CLI verb, the `$PROMPTPOTTER_*` variables and the
state tree. The tree is named in one place (`config/paths.py`), so the tier is cheap to *write* and
expensive to *live with*. **Move the tree; never teach the resolver to read both**, and count what
would move first: `ls .promptpotter/projects/*/campaigns`.

**Never rename**, at any tier: the **provider** (provenance, not a label) · `prompt_variants.json`'s
**`"source"`** (a citation) · **dataset names and `campaign_id`**, since `sample_id` is part of the
measurement cache key and renaming voids the archive without saying so · **`name:` in
`assets/optimizer/pipeline.yaml`**, which identifies the optimizer pipeline rather than the seller.

*(Written against the live single-tenant install; no second unit has been built from it, so tier 3
in particular has never been walked. Expect the first real adopter to find a gap.)*

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
| logs (admin bot) | `journalctl -u $APP_NAME-admin-bot -f` |

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

```bash
cd "$INSTALL_DIR/deploy-linux" && ./update.sh   # deploy-linux lives inside the repo
```

It mirrors origin, refreshes deps, rebuilds the webapp and restarts the app — plus the backend when
`BACKEND_DIR` is set. Re-runnable, and it never stalls on a diverged box: tracked files are
force-matched to origin while `.env` and runtime survive. Four things worth knowing:

- **The sync can replace the script mid-run**, so it re-execs the new copy once and says so — a fix
  to `update.sh` itself takes effect on the deploy that ships it.
- **It restarts the admin bot but never rewrites its unit.** A change to that unit — its data root,
  its env file — needs `./install-admin-bot.sh`.
- **The closing health line polls for up to 30s** (`health.sh::wait_healthy`) rather than probing
  once, since uvicorn takes a few seconds to bind. A red cross there means it really did not come up.
- **`Permission denied`?** The exec bit didn't survive the clone — run `bash update.sh` once, and the
  pull it does restores `100755`.

## Security posture

**The whole model, the post-install hardening checklist and the admin-bot setup** are owned by
[`docs/operations/access-model.md`](../docs/operations/access-model.md). This is the perimeter in
four lines:

- **Signing up IS the grant.** Stage-1 OIDC, provider config at `.promptpotter/identity/oidc.json`;
  what bounds a stranger is `FREE_TIER_SPEND_CAP_USD`, not an approval queue.
- **`blocklist.json` is the revoke**, re-read on every request — edits are instant, no restart.
- **Set `HOST_ADMIN_EMAIL` and `HOST_ADMIN_ISSUER`** or nothing ever claims this box. The issuer
  pins the claim to one provider rather than to an address any wired provider could assert.
- **Don't stack Cloudflare Access in front of the OIDC gate** — that is a double-gate; pick one.

**The one rule: a control-plane change never has an inbound door open to the internet.** The
blocklist is the front-door lock, so editing it is not a public endpoint. An on-box admin bot reaches
*out* to Telegram — long-poll, no open port — and edits the local file, which is the zero-trust
posture of a protected zone never reachable from the lowest-trust one. Rationale:
[`docs/adr/0004-operator-admin-channels.md`](../docs/adr/0004-operator-admin-channels.md); the bot's
keys, commands and secret hygiene are access-model.md's. Install it with `./install-admin-bot.sh`.

## Uninstall

```bash
sudo systemctl disable --now $APP_NAME
sudo rm /etc/systemd/system/$APP_NAME.service
sudo cloudflared service uninstall
cloudflared tunnel delete $TUNNEL_NAME
rm -rf $INSTALL_DIR ~/.cloudflared
```
