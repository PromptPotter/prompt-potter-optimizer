#!/usr/bin/env bash
# health.sh — the one answer to "is it serving yet?", sourced by every deploy
# script. Defines a function and touches nothing else.

# A service that is STARTING is neither up nor down, and each script here used to
# guess how long that takes with its own constant: nothing at all in update.sh,
# `sleep 2` in install-service.sh, `sleep 4` in bootstrap.sh, a 6x5s loop in
# install-tunnel.sh. Only the last one was right, and the guess is what made a
# healthy deploy end on "app down" — uvicorn needs a few seconds to bind, and
# update.sh probed the instant `systemctl restart` returned.
#
# Poll to a deadline instead, so the answer is "did it come up" rather than "was
# it up at this instant". A slow box then costs seconds instead of a false alarm,
# and a genuinely dead service still reports within the deadline.
#
#   wait_healthy <url> [seconds]   -> 0 as soon as it answers, 1 at the deadline
#
# The bound is ELAPSED time, not a number of attempts. Counting attempts reads the
# same until curl is slow: a refused connection returns at once, but a port that
# silently drops packets costs the full `--max-time`, so an attempt counter of 30
# meant thirty *attempts* — around three minutes, not thirty seconds. One in-flight
# attempt can still overshoot by up to `--max-time`.
#
# Every non-zero return sits inside an `if`, so this is safe under `set -e`.
wait_healthy() {
    local url="$1" deadline=$(( SECONDS + ${2:-30} ))
    while :; do
        if curl -fsS --max-time 5 "$url" >/dev/null 2>&1; then return 0; fi
        if [ "$SECONDS" -ge "$deadline" ]; then return 1; fi
        sleep 1
    done
}
