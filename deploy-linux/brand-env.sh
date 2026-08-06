#!/usr/bin/env bash
# Sourced by bootstrap.sh and update.sh — the two scripts that write `.env` or
# rebuild the webapp. Turns deploy.config's brand block into environment:
#
#   brand_write_env <envfile>   the engine's copy (settings.Settings reads .env)
#   brand_export_webapp         NEXT_PUBLIC_* exported for `npm run build`
#
# Both surfaces read ONE declaration, so a fork cannot brand half the install.
# An unset deploy.config value is never written or exported, which is what
# leaves the upstream default in place instead of blanking it.
#
# Full map + what must never be renamed: docs/developer/whitelabel.md — which is
# a DRAFT: these functions are exercised, but no deploy has yet run through them.

# idempotent KEY=VALUE in a .env-style file: replace the line if present, append if not.
set_env_kv() {
    local file="$1" key="$2" val="$3"
    if grep -qE "^${key}=" "$file" 2>/dev/null; then
        sed -i "s|^${key}=.*|${key}=${val}|" "$file"
    else
        printf '%s=%s\n' "$key" "$val" >> "$file"
    fi
}

# The engine's three: terminal prose, the API service title, the docs link.
brand_write_env() {
    local envfile="$1" name
    for name in BRAND_SHORT_NAME BRAND_SERVICE_NAME; do
        [[ -n "${!name:-}" ]] && set_env_kv "$envfile" "$name" "${!name}"
    done
    [[ -n "${DOCS_URL:-}" ]] && set_env_kv "$envfile" BRAND_DOCS_URL "$DOCS_URL"
    return 0
}

# The webapp's, inlined at build time by Next (`webapp/lib/brand.ts`).
# MARKETING_URL is deliberately export-if-SET rather than export-if-NON-EMPTY:
# the empty string is a meaningful value there — it drops the login showcase.
brand_export_webapp() {
    local pair from to
    for pair in \
        BRAND_NAME:NEXT_PUBLIC_BRAND_NAME \
        BRAND_SHORT_NAME:NEXT_PUBLIC_BRAND_SHORT_NAME \
        BRAND_DESCRIPTION:NEXT_PUBLIC_BRAND_DESCRIPTION \
        PUBLISHER_NAME:NEXT_PUBLIC_PUBLISHER_NAME \
        PUBLISHER_URL:NEXT_PUBLIC_PUBLISHER_URL \
        MARKETING_TITLE:NEXT_PUBLIC_MARKETING_TITLE \
        MARKETING_TAGLINE:NEXT_PUBLIC_MARKETING_TAGLINE \
        SUPPORT_URL:NEXT_PUBLIC_SUPPORT_URL \
        TERMS_URL:NEXT_PUBLIC_TERMS_URL \
        PRIVACY_URL:NEXT_PUBLIC_PRIVACY_URL \
        IMPRINT_URL:NEXT_PUBLIC_IMPRINT_URL \
        LICENSE_URL:NEXT_PUBLIC_LICENSE
    do
        from="${pair%%:*}"; to="${pair##*:}"
        [[ -n "${!from:-}" ]] && export "$to=${!from}"
    done
    [[ -n "${MARKETING_URL+set}" ]] && export NEXT_PUBLIC_MARKETING_URL="$MARKETING_URL"
    export NEXT_PUBLIC_BRAND_URL="${BRAND_URL:-https://${PUBLIC_HOSTNAME:-app.example.com}}"
    return 0
}
