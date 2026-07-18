"""Platform-specific paths and API request identity.

Browser automation owns its own browser-generated headers.  The identity in
this module is intentionally used only by the HTTP API client.
"""

from __future__ import annotations

import os
import platform as stdlib_platform
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class AppPaths:
    """Resolved, absolute locations for configuration and mutable state."""

    config_dir: Path
    data_dir: Path
    log_dir: Path
    backup_dir: Path
    secrets_dir: Path
    windows: bool = False
    linux: bool = False

    @property
    def credential_file(self) -> Path:
        return self.config_dir / "credential.json"

    @property
    def workflow_db(self) -> Path:
        return self.data_dir / "workflow.db"

    @property
    def index_cache_file(self) -> Path:
        return self.data_dir / "index_cache.json" if self.windows else self.config_dir / "index_cache.json"

    @property
    def secrets_file(self) -> Path:
        return self.secrets_dir / "secrets.dpapi"

    @property
    def deployment_config(self) -> Path:
        return self.config_dir / "deployment.json"

    @property
    def daemon_log(self) -> Path:
        return self.log_dir / "daemon.log"

    @property
    def dashboard_log(self) -> Path:
        return self.log_dir / "dashboard.log"


def is_windows(system: str | None = None) -> bool:
    """Return whether *system* names Windows (or detect the current OS)."""
    value = system or stdlib_platform.system()
    return value.casefold() in {"windows", "win32", "cygwin"}


def is_linux(system: str | None = None) -> bool:
    """Return whether *system* names native Linux (or detect the current OS)."""
    value = system or stdlib_platform.system()
    return value.casefold() == "linux"


def camoufox_os_name(system: str | None = None) -> str:
    """Map the host OS to Camoufox's fingerprint constraint names."""
    detected = system or stdlib_platform.system()
    if is_windows(detected):
        return "windows"
    if detected.casefold() in {"darwin", "mac", "macos"}:
        return "macos"
    return "linux"


def ensure_private_directory(path: Path) -> Path:
    """Create a user-owned directory and restrict it on POSIX platforms."""
    path.mkdir(parents=True, exist_ok=True)
    if not is_windows():
        path.chmod(0o700)
    return path


def ensure_private_file(path: Path) -> Path:
    """Restrict an existing sensitive file on POSIX platforms."""
    if not is_windows() and path.exists():
        path.chmod(0o600)
    return path


def _xdg_home(env: Mapping[str, str], name: str, default: Path) -> Path:
    """Resolve an XDG home, ignoring relative values as required by the spec."""
    raw = env.get(name)
    if not raw:
        return default
    value = Path(raw).expanduser()
    return value if value.is_absolute() else default


def get_app_paths(
    *,
    system: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> AppPaths:
    """Resolve application paths without creating them.

    Parameters are injectable so path handling can be verified on any CI host,
    including Windows usernames containing spaces or non-ASCII characters.
    """
    env = os.environ if environ is None else environ
    user_home = (home or Path.home()).expanduser()
    if is_windows(system):
        local_app_data = env.get("LOCALAPPDATA")
        root = Path(local_app_data) if local_app_data else user_home / "AppData" / "Local"
        root = (root / "BossCLI").expanduser()
        return AppPaths(
            config_dir=root / "config",
            data_dir=root / "data",
            log_dir=root / "logs",
            backup_dir=root / "backups",
            secrets_dir=root / "secrets",
            windows=True,
        )

    if is_linux(system):
        config_home = _xdg_home(env, "XDG_CONFIG_HOME", user_home / ".config")
        data_home = _xdg_home(env, "XDG_DATA_HOME", user_home / ".local" / "share")
        state_home = _xdg_home(env, "XDG_STATE_HOME", user_home / ".local" / "state")
        return AppPaths(
            config_dir=config_home / "boss-cli",
            data_dir=data_home / "boss-cli",
            log_dir=state_home / "boss-cli" / "logs",
            backup_dir=data_home / "boss-cli" / "backups",
            secrets_dir=config_home / "boss-cli",
            linux=True,
        )

    config_dir = user_home / ".config" / "boss-cli"
    data_home = Path(env.get("XDG_DATA_HOME", str(user_home / ".local" / "share"))).expanduser()
    data_dir = data_home / "boss-cli"
    return AppPaths(
        config_dir=config_dir,
        data_dir=data_dir,
        log_dir=data_dir / "logs",
        backup_dir=data_dir / "backups",
        secrets_dir=config_dir,
    )


def legacy_windows_credential_paths(*, home: Path | None = None) -> tuple[Path, ...]:
    """Return plaintext paths used by pre-DPAPI Windows releases."""
    user_home = (home or Path.home()).expanduser()
    current = get_app_paths(system="Windows", home=user_home).credential_file
    old = user_home / ".config" / "boss-cli" / "credential.json"
    return tuple(dict.fromkeys((old, current)))


def legacy_credential_paths(*, system: str | None = None, home: Path | None = None) -> tuple[Path, ...]:
    """Return plaintext credential paths eligible for explicit migration."""
    if is_windows(system):
        return legacy_windows_credential_paths(home=home)
    return (get_app_paths(system=system, home=home).credential_file,)


def build_api_headers(system: str | None = None) -> dict[str, str]:
    """Build a consistent desktop Chrome identity for API requests."""
    detected = system or stdlib_platform.system()
    if is_windows(detected):
        platform_token = '"Windows"'
        user_agent_os = "Windows NT 10.0; Win64; x64"
    elif detected.casefold() in {"darwin", "mac", "macos"}:
        platform_token = '"macOS"'
        user_agent_os = "Macintosh; Intel Mac OS X 10_15_7"
    else:
        platform_token = '"Linux"'
        user_agent_os = "X11; Linux x86_64"

    base_url = "https://www.zhipin.com"
    return {
        "User-Agent": (f"Mozilla/5.0 ({user_agent_os}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"),
        "sec-ch-ua": '"Chromium";v="145", "Not(A:Brand";v="99", "Google Chrome";v="145"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": platform_token,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "DNT": "1",
        "Priority": "u=1, i",
        "Origin": base_url,
        "Referer": f"{base_url}/",
    }


PATHS = get_app_paths()
