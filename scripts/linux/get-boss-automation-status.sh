#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=scripts/linux/boss-automation-common.sh
source "$SCRIPT_DIR/boss-automation-common.sh"

app_root=$(CDPATH='' cd -- "$SCRIPT_DIR/../.." && pwd -P)
uv_path=
validation_only=false
while (($#)); do
    case $1 in
        --app-root) app_root=${2:?--app-root requires a value}; shift 2 ;;
        --uv) uv_path=${2:?--uv requires a value}; shift 2 ;;
        --validation-only) validation_only=true; shift ;;
        *) boss_die "unknown argument: $1" ;;
    esac
done

boss_assert_linux
boss_assert_non_root
boss_init_paths "$app_root" "$uv_path"
if $validation_only; then
    boss_event status_validation_succeeded
    exit 0
fi

boss_assert_systemd_user
daemon_state=$(systemctl --user is-active boss-automation-daemon.service 2>/dev/null || true)
dashboard_state=$(systemctl --user is-active boss-automation-dashboard.service 2>/dev/null || true)
backup_timer_state=$(systemctl --user is-active boss-automation-backup.timer 2>/dev/null || true)

set +e
health_output=$(boss_uv_run workflow healthcheck --db "$BOSS_DATABASE" --json)
health_code=$?
set -e
[[ -n $health_output ]] || health_output='{"ok":false,"data":{"status":"unavailable"}}'

"$BOSS_UV" --directory "$BOSS_APP_ROOT" run --frozen --no-sync python -c '
import json, sys
health = json.loads(sys.argv[4])
print(json.dumps({
    "services": {
        "daemon": sys.argv[1] or "unknown",
        "dashboard": sys.argv[2] or "unknown",
        "backup_timer": sys.argv[3] or "unknown",
    },
    "health_exit_code": int(sys.argv[5]),
    "health": health.get("data"),
}, indent=2, ensure_ascii=False))
' "$daemon_state" "$dashboard_state" "$backup_timer_state" "$health_output" "$health_code"
exit "$health_code"
