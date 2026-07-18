#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

boss_die() {
    printf 'boss-automation: %s\n' "$*" >&2
    exit 1
}

boss_event() {
    local event=${1:?event is required}
    local level=${2:-info}
    printf '%s level=%s event=%s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$level" "$event" >&2
}

boss_assert_linux() {
    [[ $(uname -s) == Linux ]] || boss_die 'native Linux is required'
    [[ $(uname -m) == x86_64 ]] || boss_die 'the initial Linux deployment supports x86_64 only'
}

boss_assert_ubuntu_target() {
    [[ -r /etc/os-release ]] || boss_die '/etc/os-release is required'
    # shellcheck disable=SC1091
    source /etc/os-release
    [[ ${ID:-} == ubuntu ]] || boss_die 'the initial Linux deployment supports Ubuntu only'
    local major=${VERSION_ID%%.*}
    [[ $major =~ ^[0-9]+$ ]] || boss_die 'unable to determine the Ubuntu release'
    (( major >= 24 )) || boss_die 'Ubuntu 24.04 LTS or newer is required'
}

boss_assert_non_root() {
    (( EUID != 0 )) || boss_die 'run as the dedicated non-root desktop automation user'
}

boss_resolve_app_root() {
    local requested=${1:?app root is required}
    [[ -d $requested ]] || boss_die "application root does not exist: $requested"
    (CDPATH='' cd -- "$requested" && pwd -P)
}

boss_find_uv() {
    local requested=${1:-}
    if [[ -n $requested ]]; then
        [[ -x $requested ]] || boss_die "uv is not executable: $requested"
        printf '%s\n' "$requested"
        return
    fi
    local discovered
    discovered=$(command -v uv || true)
    [[ -n $discovered ]] || boss_die 'uv is not installed or is not available on PATH'
    printf '%s\n' "$discovered"
}

boss_init_paths() {
    BOSS_APP_ROOT=$(boss_resolve_app_root "${1:?app root is required}")
    BOSS_UV=$(boss_find_uv "${2:-}")
    BOSS_XDG_CONFIG_HOME=${XDG_CONFIG_HOME:-"$HOME/.config"}
    BOSS_XDG_DATA_HOME=${XDG_DATA_HOME:-"$HOME/.local/share"}
    BOSS_XDG_STATE_HOME=${XDG_STATE_HOME:-"$HOME/.local/state"}
    BOSS_XDG_CACHE_HOME=${XDG_CACHE_HOME:-"$HOME/.cache"}
    [[ $BOSS_XDG_CONFIG_HOME == /* ]] || boss_die 'XDG_CONFIG_HOME must be an absolute path'
    [[ $BOSS_XDG_DATA_HOME == /* ]] || boss_die 'XDG_DATA_HOME must be an absolute path'
    [[ $BOSS_XDG_STATE_HOME == /* ]] || boss_die 'XDG_STATE_HOME must be an absolute path'
    [[ $BOSS_XDG_CACHE_HOME == /* ]] || boss_die 'XDG_CACHE_HOME must be an absolute path'
    BOSS_CONFIG_ROOT=$BOSS_XDG_CONFIG_HOME/boss-cli
    BOSS_DATA_ROOT=$BOSS_XDG_DATA_HOME/boss-cli
    BOSS_STATE_ROOT=$BOSS_XDG_STATE_HOME/boss-cli
    BOSS_SYSTEMD_USER_DIR=$BOSS_XDG_CONFIG_HOME/systemd/user
    BOSS_DEPLOYMENT_CONFIG=$BOSS_CONFIG_ROOT/deployment.json
    BOSS_DATABASE=$BOSS_DATA_ROOT/workflow.db
    BOSS_BACKUP_DIR=$BOSS_DATA_ROOT/backups
    BOSS_LOG_DIR=$BOSS_STATE_ROOT/logs
    BOSS_DAEMON_LOG=$BOSS_LOG_DIR/daemon.log
    BOSS_DASHBOARD_LOG=$BOSS_LOG_DIR/dashboard.log
    BOSS_CAMOUFOX_CACHE_ROOT=$BOSS_XDG_CACHE_HOME/camoufox
    export XDG_CONFIG_HOME=$BOSS_XDG_CONFIG_HOME
    export XDG_DATA_HOME=$BOSS_XDG_DATA_HOME
    export XDG_STATE_HOME=$BOSS_XDG_STATE_HOME
    export XDG_CACHE_HOME=$BOSS_XDG_CACHE_HOME
    export BOSS_APP_ROOT BOSS_UV BOSS_XDG_CONFIG_HOME BOSS_XDG_DATA_HOME BOSS_XDG_STATE_HOME BOSS_XDG_CACHE_HOME
    export BOSS_CONFIG_ROOT BOSS_DATA_ROOT BOSS_STATE_ROOT
    export BOSS_SYSTEMD_USER_DIR BOSS_DEPLOYMENT_CONFIG BOSS_DATABASE BOSS_BACKUP_DIR
    export BOSS_LOG_DIR BOSS_DAEMON_LOG BOSS_DASHBOARD_LOG BOSS_CAMOUFOX_CACHE_ROOT
}

boss_create_private_directories() {
    install -d -m 0700 -- \
        "$BOSS_CONFIG_ROOT" "$BOSS_DATA_ROOT" "$BOSS_BACKUP_DIR" \
        "$BOSS_STATE_ROOT" "$BOSS_LOG_DIR" "$BOSS_CAMOUFOX_CACHE_ROOT"
}

boss_uv_run() {
    "$BOSS_UV" --directory "$BOSS_APP_ROOT" run --frozen --no-sync boss "$@"
}

boss_validate_script_set() {
    local required=(
        install-boss-automation.sh
        set-boss-automation-secrets.sh
        start-boss-automation.sh
        stop-boss-automation.sh
        get-boss-automation-status.sh
        backup-boss-automation.sh
        restore-boss-automation.sh
        update-boss-automation.sh
        uninstall-boss-automation.sh
        invoke-boss-automation-daemon.sh
        invoke-boss-automation-dashboard.sh
        validate-boss-automation-runtime.sh
        render-systemd-units.py
    )
    local name
    for name in "${required[@]}"; do
        [[ -f $BOSS_APP_ROOT/scripts/linux/$name ]] || boss_die "required Linux deployment file is missing: $name"
    done
}

boss_assert_systemd_user() {
    command -v systemctl >/dev/null 2>&1 || boss_die 'systemctl is required'
    systemctl --user show-environment >/dev/null 2>&1 || boss_die 'the systemd user manager is unavailable in this session'
}

boss_assert_graphical_session() {
    [[ -n ${DISPLAY:-} || -n ${WAYLAND_DISPLAY:-} ]] || boss_die 'an interactive X11 or Wayland desktop session is required'
    [[ -n ${DBUS_SESSION_BUS_ADDRESS:-} ]] || boss_die 'DBUS_SESSION_BUS_ADDRESS is required for the desktop keyring'
}

boss_assert_browser() {
    local candidate
    for candidate in google-chrome google-chrome-stable chromium chromium-browser microsoft-edge brave-browser firefox; do
        if command -v "$candidate" >/dev/null 2>&1; then
            return
        fi
    done
    boss_die 'install Chrome, Chromium, Edge, Brave, or Firefox before commissioning authentication'
}

boss_import_graphical_environment() {
    local names=()
    local name
    for name in DISPLAY WAYLAND_DISPLAY XAUTHORITY DBUS_SESSION_BUS_ADDRESS XDG_CURRENT_DESKTOP XDG_SESSION_TYPE; do
        [[ -n ${!name:-} ]] && names+=("$name")
    done
    ((${#names[@]} > 0)) && systemctl --user import-environment "${names[@]}"
    if command -v dbus-update-activation-environment >/dev/null 2>&1; then
        dbus-update-activation-environment --systemd "${names[@]}" >/dev/null
    fi
}

boss_service_active() {
    systemctl --user is-active --quiet "$1"
}

boss_daemon_has_owner() {
    local output
    if ! output=$(boss_uv_run workflow daemon-status --db "$BOSS_DATABASE" --json 2>/dev/null); then
        return 1
    fi
    printf '%s' "$output" | "$BOSS_UV" --directory "$BOSS_APP_ROOT" run --frozen --no-sync python -c \
        'import json,sys; value=json.load(sys.stdin); raise SystemExit(0 if (value.get("data") or {}).get("owner_id") else 1)'
}

boss_require_clean_worktree() {
    [[ -z $(git -C "$BOSS_APP_ROOT" status --porcelain --untracked-files=normal) ]] || \
        boss_die 'the deployment checkout must be clean before update'
}

boss_safe_purge_root() {
    local path=${1:?purge path is required}
    [[ -n $path && $path != / && $path != "$HOME" ]] || boss_die 'refusing unsafe purge path'
    rm -rf -- "$path"
}
