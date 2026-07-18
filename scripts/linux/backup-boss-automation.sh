#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=scripts/linux/boss-automation-common.sh
source "$SCRIPT_DIR/boss-automation-common.sh"

app_root=$(CDPATH='' cd -- "$SCRIPT_DIR/../.." && pwd -P)
uv_path=
kind=daily
validation_only=false
while (($#)); do
    case $1 in
        --app-root) app_root=${2:?--app-root requires a value}; shift 2 ;;
        --uv) uv_path=${2:?--uv requires a value}; shift 2 ;;
        --kind) kind=${2:?--kind requires a value}; shift 2 ;;
        --validation-only) validation_only=true; shift ;;
        *) boss_die "unknown argument: $1" ;;
    esac
done
[[ $kind == daily || $kind == pre-update || $kind == manual ]] || boss_die '--kind must be daily, pre-update, or manual'

boss_assert_linux
boss_assert_non_root
boss_init_paths "$app_root" "$uv_path"
if $validation_only; then
    boss_event backup_validation_succeeded
    exit 0
fi

boss_uv_run workflow backup --db "$BOSS_DATABASE" --kind "$kind" --json
boss_event backup_completed
