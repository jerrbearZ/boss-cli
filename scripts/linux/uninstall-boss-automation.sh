#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=scripts/linux/boss-automation-common.sh
source "$SCRIPT_DIR/boss-automation-common.sh"

app_root=$(CDPATH='' cd -- "$SCRIPT_DIR/../.." && pwd -P)
uv_path=
purge_state=false
confirmed=false
validation_only=false
while (($#)); do
    case $1 in
        --app-root) app_root=${2:?--app-root requires a value}; shift 2 ;;
        --uv) uv_path=${2:?--uv requires a value}; shift 2 ;;
        --purge-state) purge_state=true; shift ;;
        --yes) confirmed=true; shift ;;
        --validation-only) validation_only=true; shift ;;
        *) boss_die "unknown argument: $1" ;;
    esac
done
$purge_state && ! $confirmed && boss_die '--purge-state requires --yes'

boss_assert_linux
boss_assert_non_root
boss_init_paths "$app_root" "$uv_path"
if $validation_only; then
    boss_event uninstall_validation_succeeded
    exit 0
fi

boss_assert_systemd_user
if [[ -f $BOSS_DATABASE ]]; then
    "$SCRIPT_DIR/stop-boss-automation.sh" --app-root "$BOSS_APP_ROOT" --uv "$BOSS_UV"
fi
systemctl --user disable --now \
    boss-automation-daemon.service \
    boss-automation-dashboard.service \
    boss-automation-backup.timer >/dev/null 2>&1 || true
rm -f -- \
    "$BOSS_SYSTEMD_USER_DIR/boss-automation-daemon.service" \
    "$BOSS_SYSTEMD_USER_DIR/boss-automation-dashboard.service" \
    "$BOSS_SYSTEMD_USER_DIR/boss-automation-backup.service" \
    "$BOSS_SYSTEMD_USER_DIR/boss-automation-backup.timer"
systemctl --user daemon-reload

if $purge_state; then
    boss_uv_run secrets clear --all || true
    boss_safe_purge_root "$BOSS_CONFIG_ROOT"
    boss_safe_purge_root "$BOSS_DATA_ROOT"
    boss_safe_purge_root "$BOSS_STATE_ROOT"
    boss_safe_purge_root "$BOSS_CAMOUFOX_CACHE_ROOT"
fi
boss_event uninstall_completed
