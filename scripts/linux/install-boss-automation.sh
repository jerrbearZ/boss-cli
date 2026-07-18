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
boss_validate_script_set

if $validation_only; then
    boss_event install_validation_succeeded
    exit 0
fi

boss_assert_ubuntu_target
boss_assert_systemd_user
boss_assert_graphical_session
boss_assert_browser
boss_create_private_directories

"$BOSS_UV" --directory "$BOSS_APP_ROOT" sync --locked --extra browser --extra dev
"$BOSS_UV" --directory "$BOSS_APP_ROOT" run --frozen --no-sync python -m boss_cli.camoufox_runtime install --smoke

# This also proves that the desktop Secret Service backend is reachable.
boss_uv_run secrets status --json >/dev/null

if [[ -f $BOSS_DEPLOYMENT_CONFIG ]]; then
    boss_uv_run deployment validate --config "$BOSS_DEPLOYMENT_CONFIG" --json >/dev/null
    boss_uv_run deployment disable-live --config "$BOSS_DEPLOYMENT_CONFIG"
else
    boss_uv_run deployment init --config "$BOSS_DEPLOYMENT_CONFIG" --json >/dev/null
fi
boss_uv_run workflow init-db --db "$BOSS_DATABASE" --json >/dev/null
boss_uv_run workflow install-templates --db "$BOSS_DATABASE" >/dev/null

install -d -m 0700 -- "$BOSS_SYSTEMD_USER_DIR"
"$BOSS_UV" --directory "$BOSS_APP_ROOT" run --frozen --no-sync python "$SCRIPT_DIR/render-systemd-units.py" \
    --app-root "$BOSS_APP_ROOT" \
    --uv "$BOSS_UV" \
    --xdg-config-home "$BOSS_XDG_CONFIG_HOME" \
    --xdg-data-home "$BOSS_XDG_DATA_HOME" \
    --xdg-state-home "$BOSS_XDG_STATE_HOME" \
    --xdg-cache-home "$BOSS_XDG_CACHE_HOME" \
    --output-dir "$BOSS_SYSTEMD_USER_DIR" >/dev/null

systemctl --user daemon-reload
systemctl --user enable boss-automation-daemon.service boss-automation-dashboard.service boss-automation-backup.timer >/dev/null

if command -v loginctl >/dev/null 2>&1; then
    linger=$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || true)
    [[ $linger != yes ]] || boss_event lingering_user_manager_detected warning
fi

boss_event install_completed
printf 'Installed in dry mode. Set protected secrets, then start the user services.\n'
