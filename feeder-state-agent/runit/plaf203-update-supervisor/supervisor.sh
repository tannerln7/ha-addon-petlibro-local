#!/bin/sh
set -eu

ROOT=/user/data
if [ "${PLAF203_SUPERVISOR_TEST_MODE:-0}" = "1" ]; then
    ROOT="${PLAF203_TEST_ROOT:?PLAF203_TEST_ROOT is required in test mode}"
fi

UPDATE_DIR="$ROOT/local-state-agent/update"
CANDIDATE="$UPDATE_DIR/candidate.bin"
PENDING="$UPDATE_DIR/pending.json"
STATUS="$UPDATE_DIR/status.json"
LOCK_FILE="$UPDATE_DIR/transaction.lock"
RUNSVDIR_ROOT="${PLAF203_RUNSVDIR_ROOT:-/tmp/plaf203-runsvdir}"
SERVICE_PATH="$RUNSVDIR_ROOT/plaf203-state-agent"
TOKEN_FILE="$ROOT/local-state-agent/token"
SV_BIN="${PLAF203_SV_BIN:-/usr/bin/sv}"
NC_BIN="${PLAF203_NC_BIN:-/usr/bin/nc}"
FLOCK_BIN="${PLAF203_FLOCK_BIN:-/usr/bin/flock}"
FS_HELPER="${PLAF203_FS_HELPER:-$ROOT/local-state-agent/plaf203-update-fs}"
PROBE_RETRY_DELAY_SECONDS="${PLAF203_PROBE_RETRY_DELAY_SECONDS:-2}"

mkdir -p "$UPDATE_DIR"

status_write() {
    "$FS_HELPER" --root "$ROOT" status "$1" "$2" "$3" "$4"
}

# Reads only fixed status tokens and SemVer fields; malformed input fails closed.
json_field() {
    key="$1"
    file="$2"
    value="$(sed -n "s/.*\"$key\"[[:space:]]*:[[:space:]]*\"\([A-Za-z0-9_.+-]*\)\".*/\1/p" "$file" | head -n 1)"
    if [ -z "$value" ] && [ "$key" != "candidate_version" ] && [ "$key" != "previous_version" ]; then
        return 1
    fi
    printf '%s\n' "$value"
}

transaction_values() {
    candidate_version="$(json_field candidate_version "$STATUS")" || return 1
    previous_version="$(json_field previous_version "$STATUS")" || return 1
}

cleanup_transaction() {
    "$FS_HELPER" --root "$ROOT" cleanup
}

poll_expected_version() {
    expected="$1"
    token="$(tr -d '\r\n' <"$TOKEN_FILE" 2>/dev/null || true)"
    if [ -z "$token" ] || [ ! -x "$NC_BIN" ]; then
        return 2
    fi

    tries=0
    while [ "$tries" -lt 10 ]; do
        health_body="$(
            printf 'GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer %s\r\nConnection: close\r\n\r\n' "$token" |
                "$NC_BIN" -w 2 127.0.0.1 8765 2>/dev/null |
                tr -d '\r' |
                sed '1,/^$/d'
        )"
        version_body="$(
            printf 'GET /v1/version HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer %s\r\nConnection: close\r\n\r\n' "$token" |
                "$NC_BIN" -w 2 127.0.0.1 8765 2>/dev/null |
                tr -d '\r' |
                sed '1,/^$/d'
        )"
        if printf '%s' "$health_body" | grep -F '"ok":true' >/dev/null 2>&1 &&
            printf '%s' "$version_body" | grep -F '"ok":true' >/dev/null 2>&1 &&
            printf '%s' "$version_body" | grep -F "\"version\":\"$expected\"" >/dev/null 2>&1; then
            return 0
        fi
        tries=$((tries + 1))
        if [ "$tries" -lt 10 ]; then
            sleep "$PROBE_RETRY_DELAY_SECONDS"
        fi
    done
    return 1
}

finish_success() {
    transaction_values || {
        status_write failed invalid_status_metadata "" ""
        return 1
    }
    cleanup_transaction
    status_write idle update_applied "" ""
}

restore_backup() {
    candidate_version="$1"
    previous_version="$2"
    reason="$3"
    if ! "$FS_HELPER" --root "$ROOT" validate-backup; then
        cleanup_transaction
        status_write failed backup_invalid "$candidate_version" "$previous_version"
        return 1
    fi
    "$SV_BIN" -w 10 down "$SERVICE_PATH" >/dev/null 2>&1 || true
    if ! "$FS_HELPER" --root "$ROOT" restore; then
        cleanup_transaction
        status_write failed backup_restore_failed "$candidate_version" "$previous_version"
        return 1
    fi
    if ! "$SV_BIN" -w 10 up "$SERVICE_PATH" >/dev/null 2>&1; then
        cleanup_transaction
        status_write failed restored_service_start_failed "$candidate_version" "$previous_version"
        return 1
    fi
    cleanup_transaction
    status_write rolled_back "$reason" "$candidate_version" "$previous_version"
}

rollback_candidate() {
    status_write rollback_in_progress "$3" "$1" "$2"
    restore_backup "$1" "$2" "$3"
}

complete_candidate_probation() {
    candidate_version="$1"
    previous_version="$2"
    status_write candidate_active probation "$candidate_version" "$previous_version"
    if ! "$SV_BIN" -w 10 up "$SERVICE_PATH" >/dev/null 2>&1; then
        rollback_candidate "$candidate_version" "$previous_version" candidate_service_start_failed
        return 1
    fi
    if poll_expected_version "$candidate_version"; then
        status_write probation_confirmed health_and_version_confirmed "$candidate_version" "$previous_version"
        finish_success
        return 0
    else
        probe_result=$?
    fi
    if [ "$probe_result" -eq 2 ]; then
        rollback_candidate "$candidate_version" "$previous_version" probe_tool_unavailable
    else
        rollback_candidate "$candidate_version" "$previous_version" version_probe_failed
    fi
    return 1
}

activate_pending_update() {
    if [ ! -f "$PENDING" ] || [ ! -f "$CANDIDATE" ]; then
        cleanup_transaction
        status_write failed staged_artifact_missing "" ""
        return 1
    fi
    candidate_version="$(json_field candidate_version "$PENDING")" || {
        status_write failed invalid_pending_metadata "" ""
        cleanup_transaction
        return 1
    }
    previous_version="$(json_field previous_version "$PENDING")" || {
        status_write failed invalid_pending_metadata "" ""
        cleanup_transaction
        return 1
    }

    if [ ! -x "$NC_BIN" ]; then
        status_write failed probe_tool_unavailable "$candidate_version" "$previous_version"
        cleanup_transaction
        return 1
    fi

    status_write activating pre_swap "$candidate_version" "$previous_version"
    if ! "$SV_BIN" -w 10 down "$SERVICE_PATH" >/dev/null 2>&1; then
        status_write failed service_stop_failed "$candidate_version" "$previous_version"
        cleanup_transaction
        return 1
    fi
    if ! "$FS_HELPER" --root "$ROOT" backup; then
        status_write failed backup_create_failed "$candidate_version" "$previous_version"
        "$SV_BIN" -w 10 up "$SERVICE_PATH" >/dev/null 2>&1 || true
        cleanup_transaction
        return 1
    fi
    status_write activating backup_committed "$candidate_version" "$previous_version"
    if ! "$FS_HELPER" --root "$ROOT" activate; then
        rollback_candidate "$candidate_version" "$previous_version" candidate_activate_failed
        return 1
    fi
    complete_candidate_probation "$candidate_version" "$previous_version"
}

recover_transaction() {
    if [ ! -f "$STATUS" ]; then
        cleanup_transaction
        return 0
    fi
    phase="$(json_field status "$STATUS")" || {
        status_write failed invalid_status_metadata "" ""
        cleanup_transaction
        return 1
    }
    case "$phase" in
        pending)
            activate_pending_update
            ;;
        activating)
            reason="$(json_field reason "$STATUS")" || reason=invalid
            if [ "$reason" = "pre_swap" ]; then
                activate_pending_update
            else
                transaction_values || return 1
                if [ "$reason" = "backup_committed" ] && [ -f "$CANDIDATE" ]; then
                    if "$FS_HELPER" --root "$ROOT" activate; then
                        complete_candidate_probation "$candidate_version" "$previous_version"
                    else
                        rollback_candidate "$candidate_version" "$previous_version" candidate_activate_failed
                    fi
                else
                    rollback_candidate "$candidate_version" "$previous_version" reboot_during_activation
                fi
            fi
            ;;
        candidate_active)
            transaction_values || return 1
            rollback_candidate "$candidate_version" "$previous_version" reboot_during_probation
            ;;
        probation_confirmed)
            finish_success
            ;;
        rollback_in_progress)
            transaction_values || return 1
            reason="$(json_field reason "$STATUS")" || reason=rollback_resumed
            restore_backup "$candidate_version" "$previous_version" "$reason"
            ;;
        rolled_back|failed)
            cleanup_transaction
            ;;
        idle)
            cleanup_transaction
            ;;
        *)
            cleanup_transaction
            status_write failed invalid_transaction_phase "" ""
            return 1
            ;;
    esac
}

run_transaction_once() {
    exec 9>"$LOCK_FILE"
    if ! "$FLOCK_BIN" -n 9; then
        return 0
    fi
    recover_transaction || true
    "$FLOCK_BIN" -u 9
}

main() {
    if [ ! -x "$FLOCK_BIN" ] || [ ! -x "$SV_BIN" ] || [ ! -x "$FS_HELPER" ]; then
        if [ -x "$FS_HELPER" ]; then
            status_write failed transaction_tool_unavailable "" ""
        fi
        if [ "${PLAF203_SUPERVISOR_RUN_ONCE:-0}" = "1" ]; then
            exit 0
        fi
        while [ ! -x "$FLOCK_BIN" ] || [ ! -x "$SV_BIN" ] || [ ! -x "$FS_HELPER" ]; do
            sleep 60
        done
    fi
    if [ ! -x "$NC_BIN" ]; then
        status_write failed probe_tool_unavailable "" ""
        if [ "${PLAF203_SUPERVISOR_RUN_ONCE:-0}" = "1" ]; then
            exit 0
        fi
        while [ ! -x "$NC_BIN" ]; do
            sleep 60
        done
    fi
    run_transaction_once
    if [ "${PLAF203_SUPERVISOR_RUN_ONCE:-0}" = "1" ]; then
        exit 0
    fi
    while true; do
        sleep 2
        run_transaction_once
    done
}

main
