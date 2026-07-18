#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=scripts/linux/boss-automation-common.sh
source "$SCRIPT_DIR/boss-automation-common.sh"

app_root=$(CDPATH='' cd -- "$SCRIPT_DIR/../.." && pwd -P)
uv_path=
cookie_source=chrome
skip_boss_login=false
migrate_credential=false
validation_only=false
while (($#)); do
    case $1 in
        --app-root) app_root=${2:?--app-root requires a value}; shift 2 ;;
        --uv) uv_path=${2:?--uv requires a value}; shift 2 ;;
        --cookie-source) cookie_source=${2:?--cookie-source requires a value}; shift 2 ;;
        --skip-boss-login) skip_boss_login=true; shift ;;
        --migrate-credential) migrate_credential=true; shift ;;
        --validation-only) validation_only=true; shift ;;
        *) boss_die "unknown argument: $1" ;;
    esac
done

boss_assert_linux
boss_assert_non_root
boss_init_paths "$app_root" "$uv_path"
if $validation_only; then
    boss_event secret_setup_validation_succeeded
    exit 0
fi

boss_assert_graphical_session
if [[ -f $BOSS_CONFIG_ROOT/credential.json ]]; then
    if $migrate_credential; then
        boss_uv_run secrets migrate-credential --path "$BOSS_CONFIG_ROOT/credential.json" --yes
    else
        boss_die 'legacy plaintext credential found; rerun with --migrate-credential after reviewing it'
    fi
fi

boss_uv_run secrets set --api-key
if ! $skip_boss_login; then
    boss_uv_run login --cookie-source "$cookie_source"
fi
boss_uv_run secrets status
boss_event secret_setup_completed
