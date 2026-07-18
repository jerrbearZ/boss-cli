#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=scripts/linux/boss-automation-common.sh
source "$SCRIPT_DIR/boss-automation-common.sh"

app_root=$(CDPATH='' cd -- "$SCRIPT_DIR/../.." && pwd -P)
uv_path=
backup_path=
validation_only=false
while (($#)); do
    case $1 in
        --app-root) app_root=${2:?--app-root requires a value}; shift 2 ;;
        --uv) uv_path=${2:?--uv requires a value}; shift 2 ;;
        --backup) backup_path=${2:?--backup requires a value}; shift 2 ;;
        --validation-only) validation_only=true; shift ;;
        *) boss_die "unknown argument: $1" ;;
    esac
done
[[ -n $backup_path ]] || boss_die '--backup is required'

boss_assert_linux
boss_assert_non_root
boss_init_paths "$app_root" "$uv_path"
[[ -f $backup_path ]] || boss_die "backup does not exist: $backup_path"
if $validation_only; then
    boss_event restore_validation_succeeded
    exit 0
fi

"$SCRIPT_DIR/stop-boss-automation.sh" --app-root "$BOSS_APP_ROOT" --uv "$BOSS_UV"
boss_uv_run workflow restore --db "$BOSS_DATABASE" --backup "$backup_path" --yes --json
boss_event restore_completed
printf 'Restore completed with services stopped and automation paused.\n'
