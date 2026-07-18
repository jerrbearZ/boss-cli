"""Versioned user-scoped secret envelopes for supported desktop platforms."""

from __future__ import annotations

import ctypes
import importlib
import json
import os
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, cast

from .platform import PATHS, is_linux, is_windows

SECRET_SCHEMA_VERSION = 1
_DESCRIPTION = "BossCLI user secrets"
_ENTROPY = b"BossCLI.DPAPI.v1"


class SecretProviderError(RuntimeError):
    """Raised when protected secret storage cannot be read or written."""


@dataclass(frozen=True)
class SecretEnvelope:
    """Secrets encrypted together so no credential is exposed independently."""

    dashscope_api_key: str | None = None
    boss_credential: dict[str, object] | None = None
    schema_version: int = SECRET_SCHEMA_VERSION

    @classmethod
    def from_bytes(cls, payload: bytes) -> "SecretEnvelope":
        try:
            raw = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SecretProviderError("Protected secret envelope is not valid JSON") from exc
        if not isinstance(raw, dict) or raw.get("schema_version") != SECRET_SCHEMA_VERSION:
            raise SecretProviderError("Unsupported protected secret envelope version")
        key = raw.get("dashscope_api_key")
        credential = raw.get("boss_credential")
        if key is not None and not isinstance(key, str):
            raise SecretProviderError("Protected API key has an invalid type")
        if credential is not None and not isinstance(credential, dict):
            raise SecretProviderError("Protected BOSS credential has an invalid type")
        return cls(dashscope_api_key=key or None, boss_credential=credential)

    def to_bytes(self) -> bytes:
        value = {
            "schema_version": self.schema_version,
            "dashscope_api_key": self.dashscope_api_key,
            "boss_credential": self.boss_credential,
        }
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")

    @property
    def empty(self) -> bool:
        return not self.dashscope_api_key and not self.boss_credential

    def status(self) -> dict[str, bool | int]:
        return {
            "schema_version": self.schema_version,
            "dashscope_api_key_present": bool(self.dashscope_api_key),
            "boss_credential_present": bool(self.boss_credential),
        }


class SecretProvider(Protocol):
    @property
    def provider_name(self) -> str: ...

    @property
    def location(self) -> str: ...

    def load(self) -> SecretEnvelope: ...

    def save(self, envelope: SecretEnvelope) -> None: ...

    def clear(self) -> None: ...


ProtectFunc = Callable[[bytes], bytes]


class DPAPISecretProvider:
    """Store a single encrypted envelope using current-user Windows DPAPI."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        protect: ProtectFunc | None = None,
        unprotect: ProtectFunc | None = None,
    ) -> None:
        self.path = path or PATHS.secrets_file
        self._protect = protect or dpapi_protect
        self._unprotect = unprotect or dpapi_unprotect

    provider_name = "windows_dpapi_current_user"

    @property
    def location(self) -> str:
        return str(self.path)

    def load(self) -> SecretEnvelope:
        if not self.path.exists():
            return SecretEnvelope()
        try:
            protected = self.path.read_bytes()
            return SecretEnvelope.from_bytes(self._unprotect(protected))
        except OSError as exc:
            raise SecretProviderError("Unable to read the protected secret envelope") from exc

    def save(self, envelope: SecretEnvelope) -> None:
        if envelope.empty:
            self.clear()
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            temporary.write_bytes(self._protect(envelope.to_bytes()))
            os.replace(temporary, self.path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise SecretProviderError("Unable to write the protected secret envelope") from exc

    def clear(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            raise SecretProviderError("Unable to remove the protected secret envelope") from exc


KeyringGet = Callable[[str, str], str | None]
KeyringSet = Callable[[str, str, str], None]
KeyringDelete = Callable[[str, str], None]


class LinuxKeyringSecretProvider:
    """Store one envelope in the current desktop user's Secret Service keyring."""

    provider_name = "linux_secret_service_current_user"
    service_name = "boss-cli"
    account_name = "automation-secrets-v1"
    location = "Secret Service default collection"

    def __init__(
        self,
        *,
        get_password: KeyringGet | None = None,
        set_password: KeyringSet | None = None,
        delete_password: KeyringDelete | None = None,
    ) -> None:
        self._get_password: KeyringGet
        self._set_password: KeyringSet
        self._delete_password: KeyringDelete
        supplied = (get_password, set_password, delete_password)
        if any(item is not None for item in supplied) and not all(item is not None for item in supplied):
            raise ValueError("All keyring operations must be supplied together")
        if all(item is not None for item in supplied):
            assert get_password is not None
            assert set_password is not None
            assert delete_password is not None
            self._get_password = get_password
            self._set_password = set_password
            self._delete_password = delete_password
            return
        try:
            secret_service = importlib.import_module("keyring.backends.SecretService")
            backend_class = getattr(secret_service, "Keyring", None)
            if not callable(backend_class):
                raise SecretProviderError("The Linux Secret Service keyring backend is unavailable")
            backend = backend_class()
            if float(getattr(backend, "priority", 0)) <= 0:
                raise SecretProviderError("No usable Linux Secret Service keyring backend is available")
            operations = (
                getattr(backend, "get_password", None),
                getattr(backend, "set_password", None),
                getattr(backend, "delete_password", None),
            )
            if not all(callable(operation) for operation in operations):
                raise SecretProviderError("The Linux Secret Service backend is incomplete")
            self._get_password = cast(KeyringGet, operations[0])
            self._set_password = cast(KeyringSet, operations[1])
            self._delete_password = cast(KeyringDelete, operations[2])
        except (ImportError, SecretProviderError):
            raise
        except Exception as exc:  # noqa: BLE001 - backend discovery errors need one stable operator message.
            raise SecretProviderError("Unable to initialize the Linux Secret Service keyring") from exc

    def load(self) -> SecretEnvelope:
        try:
            value = self._get_password(self.service_name, self.account_name)
        except Exception as exc:  # noqa: BLE001 - keyring backends expose provider-specific exceptions.
            raise SecretProviderError("Unable to read the Linux protected secret envelope") from exc
        if value is None:
            return SecretEnvelope()
        return SecretEnvelope.from_bytes(value.encode("utf-8"))

    def save(self, envelope: SecretEnvelope) -> None:
        if envelope.empty:
            self.clear()
            return
        try:
            self._set_password(self.service_name, self.account_name, envelope.to_bytes().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - keyring backends expose provider-specific exceptions.
            raise SecretProviderError("Unable to write the Linux protected secret envelope") from exc

    def clear(self) -> None:
        try:
            if self._get_password(self.service_name, self.account_name) is not None:
                self._delete_password(self.service_name, self.account_name)
        except Exception as exc:  # noqa: BLE001 - keyring backends expose provider-specific exceptions.
            raise SecretProviderError("Unable to remove the Linux protected secret envelope") from exc


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _as_blob(value: bytes) -> tuple[_DATA_BLOB, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(value)
    pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    return _DATA_BLOB(len(value), pointer), buffer


def _crypt_data(value: bytes, *, protect: bool) -> bytes:
    if not is_windows():
        raise SecretProviderError("Windows DPAPI secret storage is available only on Windows")
    input_blob, input_buffer = _as_blob(value)
    entropy_blob, entropy_buffer = _as_blob(_ENTROPY)
    output_blob = _DATA_BLOB()
    crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    blob_pointer = ctypes.POINTER(_DATA_BLOB)
    crypt32.CryptProtectData.argtypes = [
        blob_pointer,
        wintypes.LPCWSTR,
        blob_pointer,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        blob_pointer,
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        blob_pointer,
        ctypes.POINTER(wintypes.LPWSTR),
        blob_pointer,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        blob_pointer,
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN; deliberately excludes LOCAL_MACHINE.
    description = wintypes.LPWSTR()
    if protect:
        ok = crypt32.CryptProtectData(
            ctypes.byref(input_blob),
            _DESCRIPTION,
            ctypes.byref(entropy_blob),
            None,
            None,
            flags,
            ctypes.byref(output_blob),
        )
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(input_blob),
            ctypes.byref(description),
            ctypes.byref(entropy_blob),
            None,
            None,
            flags,
            ctypes.byref(output_blob),
        )
    # Keep the backing buffers alive until the native call has completed.
    del input_buffer, entropy_buffer
    if not ok:
        if output_blob.pbData:
            kernel32.LocalFree(output_blob.pbData)
        if description.value is not None:
            kernel32.LocalFree(description)
        raise SecretProviderError(f"Windows DPAPI {'encryption' if protect else 'decryption'} failed")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)
        if description.value is not None:
            kernel32.LocalFree(description)


def dpapi_protect(value: bytes) -> bytes:
    return _crypt_data(value, protect=True)


def dpapi_unprotect(value: bytes) -> bytes:
    return _crypt_data(value, protect=False)


def supports_protected_secrets(system: str | None = None) -> bool:
    """Return whether the platform has an implemented user-scoped provider."""
    return is_windows(system) or is_linux(system)


def get_secret_provider() -> SecretProvider:
    if is_windows():
        return DPAPISecretProvider()
    if is_linux():
        return LinuxKeyringSecretProvider()
    raise SecretProviderError("Protected persistent secrets are supported only on Windows and Linux")


def load_protected_envelope() -> SecretEnvelope:
    """Load protected secrets, returning an empty envelope on unsupported platforms."""
    return get_secret_provider().load() if supports_protected_secrets() else SecretEnvelope()


def load_windows_envelope() -> SecretEnvelope:
    """Load Windows secrets, returning an empty envelope on other platforms."""
    return get_secret_provider().load() if is_windows() else SecretEnvelope()


def load_dashscope_api_key() -> str:
    """Resolve the process override first, then the platform protected value."""
    override = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if override:
        return override
    return load_protected_envelope().dashscope_api_key or ""
