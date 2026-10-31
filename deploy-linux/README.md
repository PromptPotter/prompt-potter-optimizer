# Linux deploy via Cloudflare Tunnel

End state:
- Linux box runs `uvicorn $APP_MODULE` on `127.0.0.1:8001` under **systemd**
  (auto-restart, survives reboot).
- `cloudflared` runs as another systemd service, exposing it at
  `https://$PUBLIC_HOSTNAME` over Cloudflare's HTTPS edge.
- No open ports on your router, no static IP, free.

## Adopter config (do this first)

Every script reads its values from `deploy.config` (gitignored), falling back
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
at build time, so **the rebuild IS the rename** — there is no runtime brand config to drift.

**Swapping the mark is a file swap, not a config key.** The files under `webapp/public/brand/`, and
nothing else refers to them: `mark-pot.png` (login eyebrow, sidebar, running-jobs button,
About-this-unit, the per-surface tab mark) with its large twin `mark-pot-512.png` (the login panel's
background vessel), the `tab-icon-pot-32` ink/bone pair, and `app-icon-pot-512.png`. Three constraints they carry: **the alpha
channel is the mark** — it is masked and painted with `currentColor`, so artwork with the ink baked in
cannot tint; the tab icon is **two files**, because a raster cannot carry the light/dark media query
internally; and the app icon must stay **opaque**, because a launcher composites it onto a ground you do
not control. **Do not wrap the mark in a circle** to make one file survive both themes — the tab pair is
what solves that. `SurfaceFavicon.tsx`'s gold/ink tab grounds have no override yet, so a distributor's tab
still says *which install* this is in our colours. Why each constraint exists →
[`promptpotter-web/BRAND.md`](../../promptpotter-web/BRAND.md) § Replacing the mark.

**Tier 2** is the rest of `deploy.config` — systemd unit, cloudflared tunnel, install dir, public
hostname — which the `deploy-linux/*.sh` scripts read and nothing else. **Two files the scripts
do not write, and sign-in stays broken until both move:** `.env`'s `ALLOWED_ORIGINS` and
`.promptpotter/identity/oidc.json`'s `redirect_uri`, plus the matching redirect URI in the OAuth
provider's console. The failure is silent from the app's side — the provider rejects the callback, so
nothing on the box logs a cause.

**Tier 3** renames the `promptpotter` package, its CLI verb, the `$PROMPTPOTTER_*` variables and the
state tree. The tree is named in one place (`config/paths.py`), so the tier is cheap to *write* and
expensive to *live with*. **Move the tree; never teach the resolver to read both**, and count what
would move first: `ls .promptpotter/projects/*/campaigns`.

**Never rename**, at any tier: the **provider** (provenance, not a label) · a block pack's
**`works`** keys (a citation) · **`name:` in
`assets/optimizers/potter/pipeline.yaml`**, which identifies the optimizer pipeline rather than the seller.

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
# → seeds .env from .env.example, offers to set GROQ_API_KEY (any other provider key: edit .env),
#   provisions the shared TERMNORM_TOKEN

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
Wrap it in a systemd unit like `install-service.sh` (`ExecStart … uvicorn main:app --port 8000`). PromptPotter reaches it at `.env`'s `TERMNORM_URL` (default `http://127.0.0.1:8000`). Set `BACKEND_DIR` / `BACKEND_SERVICE` in `deploy.config` **before** `./bootstrap.sh`, so bootstrap writes the shared `TERMNORM_TOKEN` into both `.env` files and `update.sh` keeps the backend in lockstep; otherwise copy the token into the backend's `.env` by hand, with `TERMNORM_REQUIRE_AUTH=true`. `update.sh` syncs the backend checkout, reinstalls its requirements and restarts its unit beside the optimizer, so a pushed backend change ships with the ordinary update.

## Defaults you can override

Set these in `deploy.config` (or pass on the command line). `deploy.config.example` declares every
key with why it exists — the brand block included; this is the map:

| var | default | meaning |
|---|---|---|
| `APP_NAME` | `myapp` | slug → systemd unit, tunnel name, install-dir, Description |
| `APP_MODULE` | `myapp.main:app` | uvicorn ASGI target |
| `ENV_FILE` | `$INSTALL_DIR/.env` | the env file both units load — under SELinux it must move to `/etc` |
| `ADMIN_BOT_MODULE` | `myapp.presentation.admin_bot` | admin-bot module (optional) |
| `BOT_ENV_FILE` | the app's `ENV_FILE` | the admin bot's own env file, once split |
| `REPO_URL` | `…/CHANGE-ME/your-repo.git` | git clone source — **edit before bootstrap** |
| `INSTALL_DIR` | `$HOME/$APP_NAME/your-repo` | where the repo lands |
| `RUN_USER` | `$USER` | systemd `User=` |
| `DATA_DIR` | unset (`$INSTALL_DIR`) | the one directory the service may write |
| `BIND_HOST` | `127.0.0.1` | uvicorn host (don't change unless you also expose LAN) |
| `BIND_PORT` | `8001` | uvicorn port |
| `WEBAPP_DIR` | `webapp` | static frontend dir built by `npm run build` |
| `HEALTH_PATH` | `/api/v1/health` | liveness endpoint the scripts curl |
| `MEMORY_MAX` | empty (no limit) | cgroup `MemoryMax` for the app unit |
| `BACKEND_DIR` / `BACKEND_SERVICE` / `BACKEND_PORT` | empty / empty / `8000` | the co-managed backend; empty skips it |
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
cd "$INSTALL_DIR/deploy-linux" && ./update.sh            # deploy-linux lives inside the repo
cd "$INSTALL_DIR/deploy-linux" && ./update.sh v0.8.17    # a release: name its tag
```

It mirrors origin — the tracked branch's tip, or the tag, branch or commit you name, printed as
`git describe` before anything else moves — refreshes deps, rebuilds the webapp and restarts the
app, plus the backend when `BACKEND_DIR` is set. Re-runnable, and it never stalls on a diverged
box: tracked files are force-matched to that commit while `.env` and runtime survive. Worth
knowing:

- **The sync can replace the script mid-run**, so it re-execs the new copy once and says so — a fix
  to `update.sh` itself takes effect on the deploy that ships it.
- **A restart signals the API alone** (`KillMode=process`): a run is its own process, so one in
  flight finishes and the returning API reads it off its lock. The app's unit is rewritten only by
  `./install-service.sh`.
- **It refuses to land while a run on other code is live**: such a run writes ledger shapes the
  landed code may refuse. Before anything moves, the running checkout's `close-admission <target>`
  names every live job whose `code_revision` is not the target, and the script exits with nothing
  changed. Re-run once that run pauses or ends. On a yes, every launch is refused until an API on the
  target starts, so nothing starts on the old code mid-deploy; a deploy that fails before the restart
  leaves launches refused until the next one lands.
- **It refuses to land code that cannot load the data root.** After the checkout and before the
  restart, the landed code's `prove-data` verb loads every file under it. On an `unloadable SURVIVOR`
  line — a file that outlives a release, pinned in `docs/specs/data-root.generated.json` — the script
  checks the previous commit back out, reopens admission to the API still running it, and exits 1:
  rewrite the named file on the box, then re-run. Run data, a cache or a file no row names that will
  not load refuses the same way unless you pass `--reset` (`./update.sh v0.8.19 --reset`), which runs
  `reset --all-tenants --yes` under the landing code and proves again.
- **It restarts the admin bot but never rewrites its unit.** A change to that unit — its data root,
  its env file — needs `./install-admin-bot.sh`.
- **The closing health line polls for up to 30s** (`health.sh::wait_healthy`) rather than probing
  once, since uvicorn takes a few seconds to bind. A red cross there means it really did not come up.
- **`Permission denied`?** The exec bit didn't survive the clone — run `bash update.sh` once, and the
  pull it does restores `100755`.

## Security posture

**The trust boundaries and where each is enforced** are
[`docs/operations/access-model.md`](../docs/operations/access-model.md)'s. On the box:

- **Signing up IS the grant.** OIDC provider config at `.promptpotter/identity/oidc.json`; what
  bounds a stranger is the host coupon `HOST_COUPON`, and how many there may be `MAX_ACCOUNTS`, not an
  approval queue.
- **`blocklist.json` is the revoke**, re-read on every request — edits are instant, no restart.
- **Set `HOST_ADMIN_EMAIL` and `HOST_ADMIN_ISSUER`** or nothing ever claims this box; the issuer
  pins the claim to one provider. Unset, the terminal stays on the `default` tenant and the app logs
  a warning.
- **Don't stack Cloudflare Access in front of the OIDC gate** — a double-gate; pick one.

**The one rule: a control-plane change never has an inbound door open to the internet** — owned by
[`docs/decisions.md` § 0004](../docs/decisions.md#0004--operator-admin-channels). The blocklist edit
happens on the box, which reaches *out* to your phone. An external tool that must drive an admin
action goes behind an edge broker (Cloudflare Access service token) **plus** an app token, never a
bare public route.

### Hardening checklist

Each step is idempotent.

1. **Apply the hardened unit** — `./install-service.sh`, then `systemctl status $APP_NAME` and the
   health curl. A unit that fails to start: first suspects are `ProtectSystem=strict` (add the write
   path to `ReadWritePaths`) and `SystemCallFilter`. `MemoryDenyWriteExecute` is omitted on purpose;
   add it only after a clean smoke test.
2. **(Optional) cgroup bound** — `MEMORY_MAX="2G"` in `deploy.config`, re-run `install-service.sh`;
   a memory-starved run then dies as a clean cgroup OOM.
3. **Provision the PP↔TermNorm token.** A first install's `bootstrap.sh` generates `TERMNORM_TOKEN`;
   on an installed box it will not re-run (its `REPO_URL` guard exits first), so set the same token
   by hand in both `.env` files, with `TERMNORM_REQUIRE_AUTH=true` on TermNorm's side.
4. **Restart BOTH services** — PromptPotter reads `TERMNORM_TOKEN` once at boot. Verify
   `127.0.0.1:8000/status`: no `Authorization` → **401**, bearer → **200**. PP's reachability probe
   (`connectors/termnorm.py::_termnorm_preflight`) is unauthenticated and fails only on a refused
   connect.
5. **Confirm TermNorm's IP allowlist** (`backend-api/config/users.json`) lists PP's source IP; a
   remote PP needs its IP added, or `/matches` dropped from `protected_paths`.
6. **Never run TermNorm's dev launcher in prod** — `start-server-py-LLMs.sh` binds `0.0.0.0:8000`.
7. **Verify no surprise listener** — `ss -tlnp` shows only loopback `:8000` / `:8001` and
   cloudflared's outbound; the admin bot adds no port.
8. **The firewall is yours, deliberately not scripted** — ingress is tunnel-only, so a host firewall
   is defense-in-depth with real SSH-lockout risk. Add default-deny-inbound that **preserves SSH** by
   hand.

### Admin bot — blocking, delegating, auditing from Telegram

The only recurring admin action is taking access away. The bot long-polls Telegram (outbound only)
and is a courtesy control, not a boundary: a blocked person can sign up again from another address,
and the spend ceiling is what bounds a stranger.

**Setup.** Create a bot via [@BotFather](https://t.me/BotFather) (its OWN token — two units polling
one token fight over the offset), read your `message.chat.id` off `getUpdates`, put the keys in the
env file, then `./install-admin-bot.sh`. Every key is a `Settings` field read from the environment
or the install's `.env` (`config/paths.py::env_file_path`).

```bash
ADMIN_BOT_TELEGRAM_TOKEN=123456:AA...           # from BotFather
ADMIN_BOT_CHAT_ID=987654321                      # your numeric chat id
ADMIN_BOT_PASSPHRASE=optional-extra-word         # optional 2nd factor
HOST_ADMIN_EMAIL=you@example.com                 # who may claim this box
HOST_ADMIN_ISSUER=https://accounts.google.com    # ...and via which provider
```

- **`ADMIN_BOT_PASSPHRASE` belongs in the BOT's file** once `BOT_ENV_FILE` is split: a copy in the
  API's environment turns a read of that process into command authority.
- **Token and chat id stay in BOTH files** — the API sends on the same bot (new sign-ins, sign-ups
  refused at `MAX_ACCOUNTS`, health changes), and without them `notify_operator` fails silently. `install-admin-bot.sh` warns.

| You send | Effect |
|---|---|
| `/block <email>` · `/unblock <email>` · `/blocked` | Withdraw access (every command refused from the next request), give it back, list. |
| `/grant <sub_user_id> step,create` · `/revoke <sub_user_id>` · `/grants` | Delegate an attenuated sub-principal into your workspace, remove it, list. The id is the delegate's own account modal's (`/auth/me`). |
| `/trail <email>` | What that account did, oldest first, across every workspace it owns or acts in; the terminal's `trail <email>` reads the same. |
| `/spend` | Every account's spend and output. |

With a passphrase set, prefix **every** message with it (`my-word /block alice@example.com`). A
refused message and a lost one look identical in Telegram; `journalctl -u $APP_NAME-admin-bot`
tells them apart. Changes are audited to `blocklist_audit.jsonl` / `grants_audit.jsonl` in
`.promptpotter/identity/`.

### Optional outbound feeds

- **New accounts into your CRM** — `N8N_SIGNUP_WEBHOOK_URL`; the app POSTs the new account's
  contact fields on its first `/auth/me`, best-effort (`promptpotter/presentation/admin_bot.py::forward_new_account_to_crm`).
  Copy the path from the workflow's webhook node, not its file name; a `POST`-only webhook answers
  *"not registered"* to a browser GET, so confirm with the receiver's own API.
- **What accounts did, into your morning briefing** — `N8N_TRAIL_WEBHOOK_URL` +
  `N8N_TRAIL_WEBHOOK_SECRET` in the **bot's** file; every half hour the bot POSTs the last 24 h of
  `/trail` lines plus the box's health, secret in `X-Trail-Secret`
  (`promptpotter/presentation/admin_bot.py::forward_trail_to_briefing`). Each send is the whole window, so an offline box
  shows as an old `as_of`. The briefing cannot ask — that would be an inbound cross-tenant read.
- **The box's health** — `promptpotter health` runs every probe (`application/health.py::probes`:
  remote backends, each sign-in provider's view of `oidc.json`, each LLM key); a state change is
  recorded on the box's workspace ledger and told to the bot's chat. It runs at app start, before
  each briefing, and in `update.sh`, where a broken probe fails the deploy.
- **Your website's records** — `SITE_RECORDS_TOKEN` in the **app's** file, then restart. A caller
  sending `Authorization: Bearer <token>` can `POST /api/v1/site-records/{table}` (`{row, key}`),
  `GET …?since=`, `DELETE …?before=` (keyed rows survive). The rows are files under `site_records/`;
  the box's backup is their only copy.

### Secret hygiene

- `.env` is `chmod 600` and never committed.
- A leaked `ADMIN_BOT_TELEGRAM_TOKEN`: re-issue via @BotFather, paste it into every env file
  carrying it, restart both units.

## Uninstall

```bash
sudo systemctl disable --now $APP_NAME
sudo rm /etc/systemd/system/$APP_NAME.service
sudo cloudflared service uninstall
cloudflared tunnel delete $TUNNEL_NAME
rm -rf $INSTALL_DIR ~/.cloudflared
```
