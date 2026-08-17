#!/bin/sh

# Start feeder-local state services before AF203_FW via a dedicated runsvdir tree.
STATE_AGENT_ROOT="${PLAF203_STATE_AGENT_ROOT:-/user/data}"
STATE_AGENT_HOME="$STATE_AGENT_ROOT/local-state-agent"
if [ -f "$STATE_AGENT_ROOT/enable_state_agent" ] &&
    [ -x "$STATE_AGENT_HOME/plaf203-state-agent" ] &&
    [ -x "$STATE_AGENT_HOME/plaf203-update-fs" ]; then
    if [ ! -f "$STATE_AGENT_HOME/token" ] && [ -n "${EXTERNAL_STATE_AGENT_TOKEN:-}" ]; then
        previous_umask="$(umask)"
        umask 077
        printf '%s\n' "$EXTERNAL_STATE_AGENT_TOKEN" >"$STATE_AGENT_HOME/token"
        chmod 0600 "$STATE_AGENT_HOME/token"
        umask "$previous_umask"
    fi

    RUNSVDIR_ROOT="${PLAF203_RUNSVDIR_ROOT:-/tmp/plaf203-runsvdir}"
    SERVICES_SRC="$STATE_AGENT_HOME/runit"
    mkdir -p "$RUNSVDIR_ROOT"

    if [ -d "$SERVICES_SRC/plaf203-state-agent" ] && [ ! -e "$RUNSVDIR_ROOT/plaf203-state-agent" ]; then
        ln -s "$SERVICES_SRC/plaf203-state-agent" "$RUNSVDIR_ROOT/plaf203-state-agent"
    fi
    if [ -d "$SERVICES_SRC/plaf203-update-supervisor" ] && [ ! -e "$RUNSVDIR_ROOT/plaf203-update-supervisor" ]; then
        ln -s "$SERVICES_SRC/plaf203-update-supervisor" "$RUNSVDIR_ROOT/plaf203-update-supervisor"
    fi

    if [ "${PLAF203_SKIP_RUNSVDIR_START:-0}" != "1" ]; then
        pidof runsvdir >/dev/null 2>&1 || runsvdir "$RUNSVDIR_ROOT" &
    fi
fi
