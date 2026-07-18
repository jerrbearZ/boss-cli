#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=scripts/linux/boss-automation-common.sh
source "$SCRIPT_DIR/boss-automation-common.sh"

app_root=$(CDPATH='' cd -- "$SCRIPT_DIR/../.." && pwd -P)
uv_path=
timeout_seconds=90
validation_only=false
while (($#)); do
    case $1 in
        --app-root) app_root=${2:?--app-root requires a value}; shift 2 ;;
        --uv) uv_path=${2:?--uv requires a value}; shift 2 ;;
        --timeout) timeout_seconds=${2:?--timeout requires a value}; shift 2 ;;
        --validation-only) validation_only=true; shift ;;
        *) boss_die "unknown argument: $1" ;;
    esac
done
if [[ ! $timeout_seconds =~ ^[0-9]+$ ]] || ((timeout_seconds < 5 || timeout_seconds > 600)); then
    boss_die '--timeout must be an integer from 5 to 600 seconds'
fi

boss_assert_linux
boss_assert_non_root
boss_init_paths "$app_root" "$uv_path"
if $validation_only; then
    boss_event stop_validation_succeeded
    exit 0
fi

boss_assert_systemd_user
released=true
if [[ -f $BOSS_DATABASE ]]; then
    boss_uv_run workflow pause --db "$BOSS_DATABASE" --reason linux_maintenance
    boss_uv_run workflow daemon-stop --db "$BOSS_DATABASE" --reason linux_maintenance --json >/dev/null
    systemctl --user stop --no-block boss-automation-daemon.service || true
    released=false
    deadline=$((SECONDS + timeout_seconds))
    while ((SECONDS < deadline)); do
        if ! boss_daemon_has_owner; then
            released=true
            break
        fi
        sleep 1
    done
fi

if ! $released; then
    systemctl --user kill --kill-whom=all --signal=SIGKILL boss-automation-daemon.service || true
    systemctl --user stop boss-automation-daemon.service || true
    boss_uv_run workflow daemon-force-release --db "$BOSS_DATABASE" --yes --json >/dev/null
    boss_event daemon_force_released warning
else
    systemctl --user stop boss-automation-daemon.service || true
fi
systemctl --user stop boss-automation-dashboard.service || true
boss_event stop_completed
