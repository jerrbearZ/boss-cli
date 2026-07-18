#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=scripts/linux/boss-automation-common.sh
source "$SCRIPT_DIR/boss-automation-common.sh"

app_root=$(CDPATH='' cd -- "$SCRIPT_DIR/../.." && pwd -P)
uv_path=
while (($#)); do
    case $1 in
        --app-root) app_root=${2:?--app-root requires a value}; shift 2 ;;
        --uv) uv_path=${2:?--uv requires a value}; shift 2 ;;
        *) boss_die "unknown argument: $1" ;;
    esac
done

boss_init_paths "$app_root" "$uv_path"
exec "$BOSS_UV" --directory "$BOSS_APP_ROOT" run --frozen --no-sync boss \
    --log-file "$BOSS_DAEMON_LOG" \
    workflow daemon \
    --db "$BOSS_DATABASE" \
    --deployment-config "$BOSS_DEPLOYMENT_CONFIG"
