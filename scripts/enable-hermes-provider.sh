#!/usr/bin/env bash
# Enable the memx memory provider for every Hermes profile.
#
# Usage:
#   bash scripts/enable-hermes-provider.sh            # enable for all profiles
#   bash scripts/enable-hermes-provider.sh --off      # roll back to built-in memory
#   bash scripts/enable-hermes-provider.sh --profiles default,worker,researcher
#
# This only edits config.yaml (via `hermes config set`). It does not restart
# anything: the gateway picks the change up on its next restart.
set -euo pipefail

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PROFILE_DIR="$HERMES_HOME/profiles"
MODE="on"
PROFILES=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --off) MODE="off"; shift ;;
        --profiles) PROFILES="$2"; shift 2 ;;
        *) echo "unknown argument: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "$PROFILES" ]]; then
    PROFILES="default"
    if [[ -d "$PROFILE_DIR" ]]; then
        for d in "$PROFILE_DIR"/*/; do
            [[ -d "$d" ]] || continue
            PROFILES="$PROFILES,$(basename "$d")"
        done
    fi
fi

# A shared store makes the memory fleet-wide. Override with MEMX_STORE.
export MEMX_STORE="${MEMX_STORE:-$HERMES_HOME/memx}"

echo "store: $MEMX_STORE"

IFS=',' read -ra LIST <<< "$PROFILES"
for p in "${LIST[@]}"; do
    [[ -n "$p" ]] || continue
    if [[ "$p" == "default" ]]; then
        home="$HERMES_HOME"; target=()
    else
        home="$PROFILE_DIR/$p"; target=(-p "$p")
    fi

    if [[ ! -d "$home" ]]; then
        echo "!! $p: no such profile directory, skipping" >&2
        continue
    fi

    if [[ ! -f "$home/plugins/memx/__init__.py" ]]; then
        echo "!! $p: provider not installed at $home/plugins/memx/ — see integrations/hermes/README.md" >&2
        continue
    fi

    if [[ "$MODE" == "on" ]]; then
        hermes "${target[@]}" config set memory.provider memx >/dev/null
        echo "ok  $p -> memory.provider = memx"
    else
        hermes "${target[@]}" memory off >/dev/null
        echo "ok  $p -> built-in memory only"
    fi
done

echo
echo "--- status ---"
for p in "${LIST[@]}"; do
    [[ -n "$p" ]] || continue
    if [[ "$p" == "default" ]]; then
        out=$(hermes memory status 2>/dev/null)
    else
        out=$(hermes -p "$p" memory status 2>/dev/null)
    fi
    prov=$(printf '%s\n' "$out" | grep -m1 "Provider:" | sed 's/^ *//')
    printf '%-12s %s\n' "$p" "${prov:-Provider: (unknown)}"
done

if [[ "$MODE" == "on" ]]; then
    echo
    echo "Restart the gateway process to activate it."
fi
