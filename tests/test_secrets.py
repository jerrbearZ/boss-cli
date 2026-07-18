"""Protected secret envelope tests that do not require secret values in output."""

import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from boss_cli.cli import cli
from boss_cli.secrets import (
    DPAPISecretProvider,
    LinuxKeyringSecretProvider,
    SecretEnvelope,
    SecretProviderError,
    dpapi_protect,
    dpapi_unprotect,
    load_dashscope_api_key,
)


def _protect(value: bytes) -> bytes:
    return b"protected:" + value[::-1]


def _unprotect(value: bytes) -> bytes:
    assert value.startswith(b"protected:")
    return value.removeprefix(b"protected:")[::-1]


def test_secret_provider_round_trip_is_atomic_and_file_has_no_plaintext(tmp_path):
    path = tmp_path / "用户 data" / "secrets.dpapi"
    provider = DPAPISecretProvider(path, protect=_protect, unprotect=_unprotect)
    envelope = SecretEnvelope(
        dashscope_api_key="sk-private-value",
        boss_credential={"cookies": {"wt2": "cookie-private-value"}, "saved_at": 1},
    )

    provider.save(envelope)
    stored = path.read_bytes()

    assert provider.load() == envelope
    assert b"sk-private-value" not in stored
    assert b"cookie-private-value" not in stored
    assert not path.with_suffix(".dpapi.tmp").exists()


def test_secret_provider_clear_removes_file(tmp_path):
    provider = DPAPISecretProvider(tmp_path / "secrets.dpapi", protect=_protect, unprotect=_unprotect)
    provider.save(SecretEnvelope(dashscope_api_key="secret"))
    provider.clear()
    assert provider.load().empty


def test_linux_keyring_provider_round_trip_uses_one_user_scoped_item():
    values: dict[tuple[str, str], str] = {}

    def get_password(service: str, account: str) -> str | None:
        return values.get((service, account))

    def set_password(service: str, account: str, value: str) -> None:
        values[(service, account)] = value

    def delete_password(service: str, account: str) -> None:
        del values[(service, account)]

    provider = LinuxKeyringSecretProvider(
        get_password=get_password,
        set_password=set_password,
        delete_password=delete_password,
    )
    envelope = SecretEnvelope(
        dashscope_api_key="sk-linux-private",
        boss_credential={"cookies": {"wt2": "linux-cookie"}},
    )

    provider.save(envelope)
    stored = next(iter(values.values()))

    assert provider.load() == envelope
    assert provider.provider_name == "linux_secret_service_current_user"
    assert len(values) == 1
    assert "sk-linux-private" in stored  # Encryption is the Secret Service backend's responsibility.

    provider.clear()
    assert provider.load().empty


def test_linux_keyring_provider_rejects_unusable_secret_service_backend():
    backend_module = SimpleNamespace(Keyring=lambda: SimpleNamespace(priority=0))
    with (
        patch("boss_cli.secrets.importlib.import_module", return_value=backend_module),
        pytest.raises(SecretProviderError, match="No usable Linux Secret Service"),
    ):
        LinuxKeyringSecretProvider()


def test_dashscope_process_override_has_priority():
    with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "process-only"}):
        assert load_dashscope_api_key() == "process-only"


@pytest.mark.skipif(sys.platform in {"win32", "linux"}, reason="Windows and Linux have protected providers")
def test_secret_status_on_unsupported_platform_reports_support_only():
    result = CliRunner().invoke(cli, ["secrets", "status", "--json"])
    assert result.exit_code == 0
    assert "process-only" not in result.output
    assert '"supported": false' in result.output.lower()


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is available only on Windows")
def test_real_windows_dpapi_round_trip_is_current_user_scoped(tmp_path):
    envelope = SecretEnvelope(
        dashscope_api_key="windows-ci-api-key",
        boss_credential={"cookies": {"wt2": "windows-ci-cookie"}},
    )
    provider = DPAPISecretProvider(tmp_path / "secrets.dpapi")
    provider.save(envelope)
    stored = provider.path.read_bytes()

    assert provider.load() == envelope
    assert b"windows-ci-api-key" not in stored
    assert b"windows-ci-cookie" not in stored
    plaintext = b"boss-cli-dpapi-round-trip"
    assert dpapi_unprotect(dpapi_protect(plaintext)) == plaintext
