#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=scripts/linux/boss-automation-common.sh
source "$SCRIPT_DIR/boss-automation-common.sh"

app_root=$(CDPATH='' cd -- "$SCRIPT_DIR/../.." && pwd -P)
uv_path=
require_session=false
require_secrets=false
validation_only=false
while (($#)); do
    case $1 in
        --app-root) app_root=${2:?--app-root requires a value}; shift 2 ;;
        --uv) uv_path=${2:?--uv requires a value}; shift 2 ;;
        --require-session) require_session=true; shift ;;
        --require-secrets) require_secrets=true; shift ;;
        --validation-only) validation_only=true; shift ;;
        *) boss_die "unknown argument: $1" ;;
    esac
done

boss_assert_linux
boss_assert_non_root
boss_init_paths "$app_root" "$uv_path"
boss_validate_script_set
if $validation_only; then
    boss_event runtime_validation_succeeded
    exit 0
fi

[[ -f $BOSS_DEPLOYMENT_CONFIG ]] || boss_die 'deployment configuration is missing; run the installer'
[[ -f $BOSS_DATABASE ]] || boss_die 'workflow database is missing; run the installer'
boss_uv_run deployment validate --config "$BOSS_DEPLOYMENT_CONFIG" --json >/dev/null
$require_session && boss_assert_graphical_session

if $require_secrets; then
    secret_status=$(boss_uv_run secrets status --json)
    printf '%s' "$secret_status" | "$BOSS_UV" --directory "$BOSS_APP_ROOT" run --frozen --no-sync python -c '
import json, sys
data = (json.load(sys.stdin).get("data") or {})
ok = data.get("dashscope_api_key_present") and data.get("boss_credential_present")
raise SystemExit(0 if ok else 1)
' || boss_die 'both the protected Alibaba API key and BOSS credential are required'
fi

boss_event runtime_validation_succeeded
