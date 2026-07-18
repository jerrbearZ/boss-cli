#!/usr/bin/env python3
"""Render user-systemd units without invoking a shell or losing path quoting."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def systemd_quote(value: str) -> str:
    if any(character in value for character in ("\0", "\n", "\r")):
        raise ValueError("systemd arguments cannot contain control characters")
    escaped = value.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_units(
    app_root: Path,
    uv_path: Path,
    output_dir: Path,
    *,
    xdg_config_home: Path | None = None,
    xdg_data_home: Path | None = None,
    xdg_state_home: Path | None = None,
    xdg_cache_home: Path | None = None,
) -> list[Path]:
    app_root = app_root.expanduser().resolve(strict=True)
    uv_path = uv_path.expanduser().resolve(strict=True)
    source_dir = Path(__file__).with_name("systemd")
    output_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(output_dir, 0o700)
    config_home = (xdg_config_home or Path.home() / ".config").expanduser().resolve()
    data_home = (xdg_data_home or Path.home() / ".local/share").expanduser().resolve()
    state_home = (xdg_state_home or Path.home() / ".local/state").expanduser().resolve()
    cache_home = (xdg_cache_home or Path.home() / ".cache").expanduser().resolve()
    replacements = {
        "@APP_ROOT@": systemd_quote(str(app_root)),
        "@UV@": systemd_quote(str(uv_path)),
        "@VALIDATE_SCRIPT@": systemd_quote(str(app_root / "scripts/linux/validate-boss-automation-runtime.sh")),
        "@DAEMON_SCRIPT@": systemd_quote(str(app_root / "scripts/linux/invoke-boss-automation-daemon.sh")),
        "@DASHBOARD_SCRIPT@": systemd_quote(str(app_root / "scripts/linux/invoke-boss-automation-dashboard.sh")),
        "@BACKUP_SCRIPT@": systemd_quote(str(app_root / "scripts/linux/backup-boss-automation.sh")),
        "@XDG_CONFIG_ENV@": systemd_quote(f"XDG_CONFIG_HOME={config_home}"),
        "@XDG_DATA_ENV@": systemd_quote(f"XDG_DATA_HOME={data_home}"),
        "@XDG_STATE_ENV@": systemd_quote(f"XDG_STATE_HOME={state_home}"),
        "@XDG_CACHE_ENV@": systemd_quote(f"XDG_CACHE_HOME={cache_home}"),
        "@CONFIG_ROOT@": systemd_quote(str(config_home / "boss-cli")),
        "@DATA_ROOT@": systemd_quote(str(data_home / "boss-cli")),
        "@STATE_ROOT@": systemd_quote(str(state_home / "boss-cli")),
        "@CAMOUFOX_CACHE_ROOT@": systemd_quote(str(cache_home / "camoufox")),
    }
    rendered: list[Path] = []
    for template in sorted(source_dir.glob("*.in")):
        text = template.read_text(encoding="utf-8")
        for placeholder, value in replacements.items():
            text = text.replace(placeholder, value)
        unresolved = sorted(token for token in replacements if token in text)
        if unresolved:
            raise ValueError(f"unresolved placeholders in {template.name}: {', '.join(unresolved)}")
        target = output_dir / template.name.removesuffix(".in")
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
        rendered.append(target)
    if not rendered:
        raise ValueError(f"no systemd unit templates found in {source_dir}")
    return rendered


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-root", required=True, type=Path)
    parser.add_argument("--uv", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--xdg-config-home", type=Path)
    parser.add_argument("--xdg-data-home", type=Path)
    parser.add_argument("--xdg-state-home", type=Path)
    parser.add_argument("--xdg-cache-home", type=Path)
    args = parser.parse_args()
    for path in render_units(
        args.app_root,
        args.uv,
        args.output_dir,
        xdg_config_home=args.xdg_config_home,
        xdg_data_home=args.xdg_data_home,
        xdg_state_home=args.xdg_state_home,
        xdg_cache_home=args.xdg_cache_home,
    ):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
