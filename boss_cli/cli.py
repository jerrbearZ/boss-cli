"""CLI entry point for Boss CLI.

Usage:
    boss login / status / logout
    boss search <keyword> [--city C] [--salary S] [--exp E] [--degree D]
    boss recommend [--page N]
    boss me / applied / interviews / chat
    boss greet <securityId>
    boss batch-greet <keyword> [-n N] [--city C] [--dry-run]
    boss cities
"""

from __future__ import annotations

import logging
from pathlib import Path

import click

from . import __version__
from .commands import auth, dashboard, deployment, personal, recruiter, search, secrets, social, workflow


@click.group()
@click.version_option(version=__version__, prog_name="boss")
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose logging (show request URLs, timing)")
@click.option("--log-file", type=click.Path(path_type=Path, dir_okay=False), envvar="BOSS_LOG_FILE", hidden=True)
@click.pass_context
def cli(ctx, verbose: bool, log_file: Path | None) -> None:
    """Boss CLI — 在终端使用 BOSS 直聘 🤝"""
    ctx.ensure_object(dict)
    if verbose:
        logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING)
    if log_file:
        from .logging_utils import configure_rotating_file_logging

        configure_rotating_file_logging(log_file, verbose=verbose)


# ─── Auth commands ───────────────────────────────────────────────────

cli.add_command(auth.login)
cli.add_command(auth.logout)
cli.add_command(auth.status)
cli.add_command(auth.me)
cli.add_command(secrets.secrets_group)
cli.add_command(deployment.deployment_group)

# ─── Search & Browse commands ────────────────────────────────────────

cli.add_command(search.search)
cli.add_command(search.recommend)
cli.add_command(search.detail)
cli.add_command(search.show)
cli.add_command(search.export)
cli.add_command(search.history)
cli.add_command(search.cities)

# ─── Personal Center commands ────────────────────────────────────────

cli.add_command(personal.applied)
cli.add_command(personal.interviews)

# ─── Social commands ────────────────────────────────────────────────

cli.add_command(social.chat_list)
cli.add_command(social.greet)
cli.add_command(social.batch_greet)

# ─── Recruiter (Boss) commands ──────────────────────────────────────

cli.add_command(recruiter.recruiter)

# ─── Workflow automation commands ──────────────────────────────────

cli.add_command(workflow.workflow)

cli.add_command(dashboard.dashboard)


if __name__ == "__main__":
    cli()
