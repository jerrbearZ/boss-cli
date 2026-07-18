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
    boss_event start_validation_succeeded
    exit 0
fi

boss_assert_systemd_user
boss_assert_graphical_session
boss_import_graphical_environment
"$SCRIPT_DIR/validate-boss-automation-runtime.sh" \
    --app-root "$BOSS_APP_ROOT" --uv "$BOSS_UV" --require-session --require-secrets

auth_status=$(boss_uv_run status --json)
printf '%s' "$auth_status" | "$BOSS_UV" --directory "$BOSS_APP_ROOT" run --frozen --no-sync python -c '
import json, sys
data = json.load(sys.stdin)
raise SystemExit(0 if data.get("authenticated") else 1)
' || boss_die 'BOSS authentication is not healthy; run the secret/login setup again'

boss_uv_run workflow daemon-start --db "$BOSS_DATABASE"
systemctl --user start boss-automation-dashboard.service
systemctl --user start boss-automation-daemon.service
boss_event start_requested
