"""User-scoped protected-secret management commands."""

from __future__ import annotations

import json
from pathlib import Path

import click

from ..auth import Credential, credential_from_cookie_header, verify_credential
from ..platform import legacy_credential_paths
from ..secrets import SecretEnvelope, SecretProviderError, get_secret_provider, supports_protected_secrets
from ._common import _output_structured, console, structured_output_options


@click.group("secrets")
def secrets_group() -> None:
    """Manage user-scoped protected secrets."""


def _provider():
    if not supports_protected_secrets():
        raise click.ClickException("Persistent protected secrets are available only on Windows and Linux")
    try:
        return get_secret_provider()
    except SecretProviderError as exc:
        raise click.ClickException(str(exc)) from exc


@secrets_group.command("set")
@click.option("--api-key", "set_api_key", is_flag=True, help="Prompt for the Alibaba Model Studio API key.")
@click.option("--boss-cookies", "set_boss_cookies", is_flag=True, help="Prompt for a serialized BOSS Cookie header.")
def set_secrets(set_api_key: bool, set_boss_cookies: bool) -> None:
    """Set secrets through hidden prompts; values are never command arguments."""
    provider = _provider()
    current = provider.load()
    if not set_api_key and not set_boss_cookies:
        set_api_key = set_boss_cookies = True

    api_key = current.dashscope_api_key
    boss_credential = current.boss_credential
    if set_api_key:
        api_key = click.prompt("Alibaba API key", hide_input=True, confirmation_prompt=True).strip()
        if not api_key:
            raise click.ClickException("API key cannot be empty")
    if set_boss_cookies:
        raw = click.prompt("BOSS Cookie header", hide_input=True, confirmation_prompt=True)
        credential = credential_from_cookie_header(raw)
        if credential is None:
            raise click.ClickException("BOSS Cookie header did not contain any key=value pairs")
        boss_credential = credential.to_dict()

    provider.save(SecretEnvelope(dashscope_api_key=api_key, boss_credential=boss_credential))
    console.print("[green]Protected secrets updated for the current operating-system user.[/green]")


@secrets_group.command("status")
@structured_output_options
def secrets_status(as_json: bool, as_yaml: bool) -> None:
    """Report secret presence only; never decrypt values into command output."""
    if not supports_protected_secrets():
        data: dict[str, object] = {"supported": False, "provider": None}
    else:
        provider = _provider()
        data = {
            "supported": True,
            "provider": provider.provider_name,
            "location": provider.location,
            **provider.load().status(),
        }
    if as_json or as_yaml or not click.get_text_stream("stdout").isatty():
        _output_structured(data, as_json=as_json, as_yaml=as_yaml)
        return
    if not data["supported"]:
        console.print("Protected persistent secrets are not enabled on this platform.")
        return
    console.print(
        "api_key="
        f"{'present' if data['dashscope_api_key_present'] else 'missing'} "
        "boss_credential="
        f"{'present' if data['boss_credential_present'] else 'missing'}"
    )


@secrets_group.command("clear")
@click.option("--api-key", "clear_api_key", is_flag=True, help="Clear only the Alibaba API key.")
@click.option("--boss-credential", is_flag=True, help="Clear only the BOSS credential.")
@click.option("--all", "clear_all", is_flag=True, help="Clear the entire protected envelope.")
def clear_secrets(clear_api_key: bool, boss_credential: bool, clear_all: bool) -> None:
    """Clear selected protected values."""
    provider = _provider()
    if not any((clear_api_key, boss_credential, clear_all)):
        raise click.ClickException("Choose --api-key, --boss-credential, or --all")
    if clear_all:
        provider.clear()
    else:
        current = provider.load()
        provider.save(
            SecretEnvelope(
                dashscope_api_key=None if clear_api_key else current.dashscope_api_key,
                boss_credential=None if boss_credential else current.boss_credential,
            )
        )
    console.print("[green]Requested protected secrets cleared.[/green]")


@secrets_group.command("migrate-credential")
@click.option("--path", "credential_path", type=click.Path(path_type=Path, dir_okay=False), default=None, hidden=True)
@click.option("--yes", is_flag=True, help="Confirm migration and deletion of the validated plaintext file.")
def migrate_credential(credential_path: Path | None, yes: bool) -> None:
    """Explicitly migrate a legacy plaintext credential into protected storage."""
    provider = _provider()
    paths = (credential_path,) if credential_path else legacy_credential_paths()
    source = next((path for path in paths if path and path.exists()), None)
    if source is None:
        raise click.ClickException("No legacy plaintext credential file was found")
    if not yes and not click.confirm(f"Encrypt and remove the validated plaintext credential at {source}?"):
        raise click.Abort()
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
        credential = Credential.from_dict(raw)
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise click.ClickException("Legacy credential is not a valid credential document") from exc
    if not credential.is_valid:
        raise click.ClickException("Legacy credential is empty")
    authenticated, reason = verify_credential(credential, force_refresh=True)
    if not authenticated:
        raise click.ClickException(f"Legacy credential validation failed: {reason or 'not authenticated'}")

    current = provider.load()
    provider.save(SecretEnvelope(dashscope_api_key=current.dashscope_api_key, boss_credential=raw))
    reloaded = provider.load().boss_credential
    if reloaded != raw:
        raise click.ClickException("Protected credential verification failed; plaintext file was retained")
    source.unlink()
    console.print("[green]Legacy credential migrated and plaintext removed.[/green]")
