"""Local dashboard command."""

from __future__ import annotations

from pathlib import Path

import click

from ..dashboard.server import run_dashboard
from ..deployment import DeploymentConfigError, load_deployment_config
from ..workflow import default_db_path


@click.command("dashboard")
@click.option(
    "--db",
    "db_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="SQLite workflow database path.",
)
@click.option("--host", default="127.0.0.1", show_default=True, help="Dashboard bind host.")
@click.option("--port", default=8765, show_default=True, type=int, help="Dashboard bind port.")
@click.option("--deployment-config", type=click.Path(dir_okay=False, path_type=Path), default=None, envvar="BOSS_DEPLOYMENT_CONFIG")
@click.option("--open/--no-open", "open_browser", default=True, show_default=True, help="Open browser automatically.")
def dashboard(db_path: Path | None, host: str, port: int, deployment_config: Path | None, open_browser: bool) -> None:
    """Run the local Boss workflow dashboard."""
    if deployment_config:
        try:
            config = load_deployment_config(deployment_config)
        except DeploymentConfigError as exc:
            raise click.ClickException(str(exc)) from exc
        host = config.dashboard_host
        port = config.dashboard_port
    if host != "127.0.0.1":
        raise click.ClickException("The workflow dashboard may bind only to 127.0.0.1")
    run_dashboard(
        db_path=db_path or default_db_path(),
        host=host,
        port=port,
        open_browser=open_browser,
    )
