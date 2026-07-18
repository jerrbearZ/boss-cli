"""Non-secret deployment configuration commands."""

from __future__ import annotations

import importlib.metadata
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import click

from ..deployment import (
    DeploymentConfig,
    DeploymentConfigError,
    default_deployment_config_path,
    load_deployment_config,
    set_live_mode,
    write_deployment_config,
)
from ..workflow import init_db
from ..workflow.db import default_db_path
from ._common import _output_structured, console, structured_output_options


@click.group("deployment")
def deployment_group() -> None:
    """Manage validated, non-secret deployment configuration."""


def _path_option(function):
    return click.option(
        "--config",
        "config_path",
        type=click.Path(path_type=Path, dir_okay=False),
        default=None,
        help="Deployment JSON path (default: platform application config directory).",
    )(function)


@deployment_group.command("init")
@_path_option
@click.option("--force", is_flag=True, help="Replace an existing file; the new file is always dry mode.")
@structured_output_options
def init_config(config_path: Path | None, force: bool, as_json: bool, as_yaml: bool) -> None:
    """Create a safe dry-mode deployment configuration."""
    try:
        path = write_deployment_config(DeploymentConfig(live=False), config_path, force=force)
    except DeploymentConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    data = {"path": str(path), **load_deployment_config(path).to_dict()}
    if as_json or as_yaml or not click.get_text_stream("stdout").isatty():
        _output_structured(data, as_json=as_json, as_yaml=as_yaml)
    else:
        console.print(f"[green]Dry-mode deployment configuration written:[/green] {path}")


@deployment_group.command("validate")
@_path_option
@structured_output_options
def validate_config(config_path: Path | None, as_json: bool, as_yaml: bool) -> None:
    """Validate a deployment file without changing it."""
    try:
        config = load_deployment_config(config_path)
    except DeploymentConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    data = {"valid": True, "path": str(config_path or default_deployment_config_path()), **config.to_dict()}
    if as_json or as_yaml or not click.get_text_stream("stdout").isatty():
        _output_structured(data, as_json=as_json, as_yaml=as_yaml)
    else:
        console.print(f"valid=true live={str(config.live).lower()}")


@deployment_group.command("show")
@_path_option
@structured_output_options
def show_config(config_path: Path | None, as_json: bool, as_yaml: bool) -> None:
    """Show the non-secret deployment configuration."""
    validate_config.callback(config_path, as_json, as_yaml)  # type: ignore[attr-defined]


@deployment_group.command("enable-live")
@_path_option
@click.option("--yes", is_flag=True, help="Confirm that platform acceptance gates have passed.")
def enable_live(config_path: Path | None, yes: bool) -> None:
    """Explicitly enable browser writes after the acceptance gates pass."""
    if not yes and not click.confirm("Have the dry-run, browser, canary, and recovery acceptance gates passed?"):
        raise click.Abort()
    try:
        set_live_mode(config_path, live=True)
    except DeploymentConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    console.print("[yellow]Live mode enabled. Restart the daemon to apply it.[/yellow]")


@deployment_group.command("disable-live")
@_path_option
def disable_live(config_path: Path | None) -> None:
    """Return the deployment to decision-only dry mode."""
    try:
        set_live_mode(config_path, live=False)
    except DeploymentConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    console.print("[green]Dry mode enabled. Restart the daemon to apply it.[/green]")


@deployment_group.command("diagnostics")
@click.option("--db", "db_path", type=click.Path(path_type=Path, dir_okay=False), default=None)
@structured_output_options
def diagnostics(db_path: Path | None, as_json: bool, as_yaml: bool) -> None:
    """Report non-secret runtime versions for certification and support."""
    root = Path(__file__).parents[2]
    camoufox_runtime = _camoufox_runtime()
    data: dict[str, object] = {
        "os": platform.platform(),
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "executable": sys.executable,
        "app_commit": _command_version(["git", "-C", str(root), "rev-parse", "HEAD"]),
        "uv": _command_version([shutil.which("uv") or "uv", "--version"]),
        "package": _package_version("kabi-boss-cli"),
        "camoufox": _package_version("camoufox"),
        "playwright": _package_version("playwright"),
        "camoufox_binary": camoufox_runtime.get("executable"),
        "camoufox_runtime": camoufox_runtime,
        "schema_version": None,
    }
    effective_db = db_path or default_db_path()
    if effective_db.exists():
        with init_db(effective_db) as store:
            data["schema_version"] = store.current_schema_version()
    if as_json or as_yaml or not click.get_text_stream("stdout").isatty():
        _output_structured(data, as_json=as_json, as_yaml=as_yaml)
    else:
        for key, value in data.items():
            console.print(f"{key}={value if value is not None else '-'}")


def _command_version(arguments: list[str]) -> str | None:
    try:
        return subprocess.run(arguments, check=True, capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _camoufox_runtime() -> dict[str, object]:
    try:
        from ..camoufox_runtime import RUNTIME_FULL_VERSION, resolve_asset, verify_runtime

        asset = resolve_asset()
        executable = verify_runtime()
        return {
            "verified": True,
            "version": RUNTIME_FULL_VERSION,
            "asset": asset.filename,
            "asset_sha256": asset.sha256,
            "executable": str(executable),
        }
    except Exception as exc:  # noqa: BLE001 - diagnostics must report a missing runtime, never install or fail.
        return {"verified": False, "error": type(exc).__name__}
