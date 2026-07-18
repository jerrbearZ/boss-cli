#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=scripts/linux/boss-automation-common.sh
source "$SCRIPT_DIR/boss-automation-common.sh"

app_root=$(CDPATH='' cd -- "$SCRIPT_DIR/../.." && pwd -P)
uv_path=
revision=
validation_only=false
while (($#)); do
    case $1 in
        --app-root) app_root=${2:?--app-root requires a value}; shift 2 ;;
        --uv) uv_path=${2:?--uv requires a value}; shift 2 ;;
        --revision) revision=${2:?--revision requires a value}; shift 2 ;;
        --validation-only) validation_only=true; shift ;;
        *) boss_die "unknown argument: $1" ;;
    esac
done

boss_assert_linux
boss_assert_non_root
boss_init_paths "$app_root" "$uv_path"
if $validation_only; then
    boss_event update_validation_succeeded
    exit 0
fi
[[ $revision =~ ^[0-9a-fA-F]{40}$ ]] || boss_die '--revision must be a full 40-character commit SHA'
git -C "$BOSS_APP_ROOT" cat-file -e "$revision^{commit}" 2>/dev/null || boss_die 'the requested commit is not available locally'
boss_require_clean_worktree
previous_revision=$(git -C "$BOSS_APP_ROOT" rev-parse HEAD)

rollback() {
    local exit_code=${1:-1}
    trap - ERR
    boss_event update_failed_rollback_started error
    git -C "$BOSS_APP_ROOT" checkout --detach "$previous_revision" || true
    "$BOSS_UV" --directory "$BOSS_APP_ROOT" sync --locked --extra browser --extra dev || true
    boss_uv_run deployment disable-live --config "$BOSS_DEPLOYMENT_CONFIG" || true
    if [[ -f $BOSS_APP_ROOT/scripts/linux/render-systemd-units.py ]]; then
        "$BOSS_UV" --directory "$BOSS_APP_ROOT" run --frozen --no-sync python "$BOSS_APP_ROOT/scripts/linux/render-systemd-units.py" \
            --app-root "$BOSS_APP_ROOT" --uv "$BOSS_UV" \
            --xdg-config-home "$BOSS_XDG_CONFIG_HOME" \
            --xdg-data-home "$BOSS_XDG_DATA_HOME" \
            --xdg-state-home "$BOSS_XDG_STATE_HOME" \
            --xdg-cache-home "$BOSS_XDG_CACHE_HOME" \
            --output-dir "$BOSS_SYSTEMD_USER_DIR" >/dev/null || true
        systemctl --user daemon-reload || true
    fi
    exit "$exit_code"
}
trap 'rollback $?' ERR

"$SCRIPT_DIR/stop-boss-automation.sh" --app-root "$BOSS_APP_ROOT" --uv "$BOSS_UV"
"$SCRIPT_DIR/backup-boss-automation.sh" --app-root "$BOSS_APP_ROOT" --uv "$BOSS_UV" --kind pre-update
boss_uv_run deployment disable-live --config "$BOSS_DEPLOYMENT_CONFIG"

git -C "$BOSS_APP_ROOT" checkout --detach "$revision"
"$BOSS_UV" --directory "$BOSS_APP_ROOT" sync --locked --extra browser --extra dev
"$BOSS_UV" --directory "$BOSS_APP_ROOT" run --frozen --no-sync python -m boss_cli.camoufox_runtime install --smoke
boss_uv_run workflow init-db --db "$BOSS_DATABASE" --json >/dev/null
"$BOSS_UV" --directory "$BOSS_APP_ROOT" run --frozen --no-sync ruff check .
"$BOSS_UV" --directory "$BOSS_APP_ROOT" run --frozen --no-sync ruff format --check .
"$BOSS_UV" --directory "$BOSS_APP_ROOT" run --frozen --no-sync pyright boss_cli
"$BOSS_UV" --directory "$BOSS_APP_ROOT" run --frozen --no-sync python -m pytest -p no:capture -q -m 'not smoke'
"$BOSS_UV" --directory "$BOSS_APP_ROOT" build
find "$BOSS_APP_ROOT/scripts/linux" -type f -name '*.sh' -print0 | xargs -0 -n1 bash -n

"$BOSS_UV" --directory "$BOSS_APP_ROOT" run --frozen --no-sync python "$SCRIPT_DIR/render-systemd-units.py" \
    --app-root "$BOSS_APP_ROOT" --uv "$BOSS_UV" \
    --xdg-config-home "$BOSS_XDG_CONFIG_HOME" \
    --xdg-data-home "$BOSS_XDG_DATA_HOME" \
    --xdg-state-home "$BOSS_XDG_STATE_HOME" \
    --xdg-cache-home "$BOSS_XDG_CACHE_HOME" \
    --output-dir "$BOSS_SYSTEMD_USER_DIR" >/dev/null
systemctl --user daemon-reload
boss_uv_run workflow resume --db "$BOSS_DATABASE"
"$SCRIPT_DIR/start-boss-automation.sh" --app-root "$BOSS_APP_ROOT" --uv "$BOSS_UV"

trap - ERR
boss_event update_completed
printf 'Updated to %s and restarted in dry mode.\n' "$revision"
