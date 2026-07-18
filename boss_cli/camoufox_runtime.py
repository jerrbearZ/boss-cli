"""Install and verify the exact Camoufox browser artifact selected by this release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import sys
import tempfile
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

CAMOUFOX_PACKAGE_VERSION = "0.4.11"
PLAYWRIGHT_PACKAGE_VERSION = "1.50.0"
RUNTIME_VERSION = "152.0.4"
RUNTIME_RELEASE = "beta.27"
RUNTIME_FULL_VERSION = f"{RUNTIME_VERSION}-{RUNTIME_RELEASE}"
RELEASE_TAG = f"v{RUNTIME_FULL_VERSION}"
RELEASE_BASE_URL = f"https://github.com/daijro/camoufox/releases/download/{RELEASE_TAG}"
RECEIPT_NAME = ".boss-runtime.json"


class CamoufoxRuntimeError(RuntimeError):
    """Raised when the pinned browser cannot be installed or verified."""


@dataclass(frozen=True)
class RuntimeAsset:
    os_name: str
    architecture: str
    filename: str
    sha256: str
    size: int

    @property
    def url(self) -> str:
        return f"{RELEASE_BASE_URL}/{self.filename}"


ASSETS = {
    ("lin", "arm64"): RuntimeAsset(
        "lin",
        "arm64",
        "camoufox-152.0.4-beta.27-lin.arm64.zip",
        "5fd73824da8fbf30b41aeca664e1c5d8dda4a020cf9d5508bc187f0aabd8633a",
        653_813_650,
    ),
    ("lin", "x86_64"): RuntimeAsset(
        "lin",
        "x86_64",
        "camoufox-152.0.4-beta.27-lin.x86_64.zip",
        "c6d24b18196d8fabcf074e9d53df7a026323cb2e30b25fd3630bdae025c1a44b",
        663_428_684,
    ),
    ("mac", "arm64"): RuntimeAsset(
        "mac",
        "arm64",
        "camoufox-152.0.4-beta.27-mac.arm64.zip",
        "0ee2b71cb234234021df71c637a4c5394efd12e73f0d9e397094d755f0fc1c47",
        312_625_611,
    ),
    ("mac", "x86_64"): RuntimeAsset(
        "mac",
        "x86_64",
        "camoufox-152.0.4-beta.27-mac.x86_64.zip",
        "2251e2f8745f1a729d907d2bc021521240dc6486debe6d868fc805f6f63069b7",
        319_865_885,
    ),
    ("win", "i686"): RuntimeAsset(
        "win",
        "i686",
        "camoufox-152.0.4-beta.27-win.i686.zip",
        "3483068f23c03fc3958d51da9f2f2c4de67ac42505f41b2095e6fc6908d28ca6",
        478_098_904,
    ),
    ("win", "x86_64"): RuntimeAsset(
        "win",
        "x86_64",
        "camoufox-152.0.4-beta.27-win.x86_64.zip",
        "d5b83d1c419f4fdfcdea08428f82a34cd1f06f754388354d33c4ff64877cfb94",
        492_369_094,
    ),
}

_SYSTEM_NAMES = {"darwin": "mac", "linux": "lin", "windows": "win"}
_ARCHITECTURES = {
    "amd64": "x86_64",
    "x86_64": "x86_64",
    "x86": "i686",
    "i386": "i686",
    "i686": "i686",
    "aarch64": "arm64",
    "arm64": "arm64",
}
_LAUNCH_PATHS = {
    "lin": Path("camoufox-bin"),
    "mac": Path("Camoufox.app/Contents/MacOS/camoufox"),
    "win": Path("camoufox.exe"),
}


def validate_manifest() -> None:
    """Fail if a release asset is incomplete, malformed, or internally inconsistent."""
    if set(ASSETS) != {(asset.os_name, asset.architecture) for asset in ASSETS.values()}:
        raise CamoufoxRuntimeError("Camoufox asset keys do not match their metadata")
    filenames: set[str] = set()
    for asset in ASSETS.values():
        if asset.filename in filenames or not re.fullmatch(r"[0-9a-f]{64}", asset.sha256):
            raise CamoufoxRuntimeError("Camoufox asset manifest is malformed")
        if RUNTIME_FULL_VERSION not in asset.filename or asset.size <= 0:
            raise CamoufoxRuntimeError("Camoufox asset version or size is invalid")
        filenames.add(asset.filename)


def resolve_asset(system: str | None = None, machine: str | None = None) -> RuntimeAsset:
    """Resolve one exact release asset for the current OS and architecture."""
    os_name = _SYSTEM_NAMES.get((system or platform.system()).casefold())
    architecture = _ARCHITECTURES.get((machine or platform.machine()).casefold())
    if os_name is None or architecture is None or (os_name, architecture) not in ASSETS:
        raise CamoufoxRuntimeError(f"Unsupported Camoufox runtime platform: {system or platform.system()} {machine or platform.machine()}")
    return ASSETS[(os_name, architecture)]


def _runtime_root() -> Path:
    try:
        from camoufox.pkgman import INSTALL_DIR
    except ImportError as exc:
        raise CamoufoxRuntimeError("Install the locked browser extra before installing Camoufox") from exc
    return Path(INSTALL_DIR)


def _assert_dependency_versions() -> None:
    expected = {"camoufox": CAMOUFOX_PACKAGE_VERSION, "playwright": PLAYWRIGHT_PACKAGE_VERSION}
    for name, required in expected.items():
        try:
            installed = package_version(name)
        except PackageNotFoundError as exc:
            raise CamoufoxRuntimeError(f"Required package is missing: {name}=={required}") from exc
        if installed != required:
            raise CamoufoxRuntimeError(f"Expected {name}=={required}, found {installed}")


def _expected_receipt(asset: RuntimeAsset) -> dict[str, object]:
    return {
        "schema_version": 1,
        "camoufox_package": CAMOUFOX_PACKAGE_VERSION,
        "playwright_package": PLAYWRIGHT_PACKAGE_VERSION,
        "runtime_version": RUNTIME_VERSION,
        "runtime_release": RUNTIME_RELEASE,
        "asset": asdict(asset),
    }


def _launch_path(root: Path, asset: RuntimeAsset) -> Path:
    return root / _LAUNCH_PATHS[asset.os_name]


def verify_runtime(*, smoke: bool = False) -> Path:
    """Verify dependency pins, artifact receipt, executable, and optionally a real browser context."""
    validate_manifest()
    _assert_dependency_versions()
    asset = resolve_asset()
    root = _runtime_root()
    receipt_path = root / RECEIPT_NAME
    version_path = root / "version.json"
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        installed_version = json.loads(version_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CamoufoxRuntimeError("Pinned Camoufox receipt or version metadata is missing or invalid") from exc
    if receipt != _expected_receipt(asset):
        raise CamoufoxRuntimeError("Installed Camoufox receipt does not match this release")
    if installed_version != {"version": RUNTIME_VERSION, "release": RUNTIME_RELEASE}:
        raise CamoufoxRuntimeError("Installed Camoufox browser version does not match this release")
    executable = _launch_path(root, asset)
    if not executable.is_file() or (asset.os_name != "win" and not os.access(executable, os.X_OK)):
        raise CamoufoxRuntimeError(f"Pinned Camoufox executable is missing or not executable: {executable}")
    if smoke:
        _smoke_browser_context()
    return executable


def _download(asset: RuntimeAsset, destination: Path) -> None:
    digest = hashlib.sha256()
    downloaded = 0
    request = urllib.request.Request(asset.url, headers={"User-Agent": "boss-cli-runtime-installer/1"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, destination.open("xb") as output:  # noqa: S310 - fixed HTTPS URL.
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                digest.update(chunk)
                downloaded += len(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    if downloaded != asset.size or digest.hexdigest() != asset.sha256:
        destination.unlink(missing_ok=True)
        raise CamoufoxRuntimeError("Downloaded Camoufox asset failed size or SHA-256 verification")


def safe_extract(archive_path: Path, destination: Path) -> None:
    """Extract a verified archive without traversal or symlink entries."""
    destination_root = destination.resolve()
    with ZipFile(archive_path) as archive:
        for info in archive.infolist():
            member = PurePosixPath(info.filename)
            unix_mode = info.external_attr >> 16
            if member.is_absolute() or ".." in member.parts or stat.S_ISLNK(unix_mode):
                raise CamoufoxRuntimeError(f"Unsafe path in Camoufox archive: {info.filename}")
            target = (destination / Path(*member.parts)).resolve()
            try:
                target.relative_to(destination_root)
            except ValueError as exc:
                raise CamoufoxRuntimeError(f"Unsafe path in Camoufox archive: {info.filename}") from exc
        archive.extractall(destination)
        if os.name != "nt":
            for info in archive.infolist():
                mode = (info.external_attr >> 16) & 0o777
                target = destination / Path(*PurePosixPath(info.filename).parts)
                if mode and target.exists():
                    target.chmod(mode)


def install_runtime(*, force: bool = False, smoke: bool = False) -> Path:
    """Atomically install the pinned, digest-verified runtime into Camoufox's cache."""
    validate_manifest()
    _assert_dependency_versions()
    if not force:
        try:
            return verify_runtime(smoke=smoke)
        except CamoufoxRuntimeError:
            pass

    asset = resolve_asset()
    root = _runtime_root()
    root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{root.name}-staging-", dir=root.parent))
    archive_path = root.parent / f".{asset.filename}.{uuid.uuid4().hex}.download"
    previous: Path | None = None
    replaced = False
    try:
        _download(asset, archive_path)
        safe_extract(archive_path, staging)
        (staging / "version.json").write_text(
            json.dumps({"version": RUNTIME_VERSION, "release": RUNTIME_RELEASE}, separators=(",", ":")),
            encoding="utf-8",
        )
        (staging / RECEIPT_NAME).write_text(
            json.dumps(_expected_receipt(asset), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        if os.name != "nt":
            staging.chmod(0o700)
            (staging / "version.json").chmod(0o600)
            (staging / RECEIPT_NAME).chmod(0o600)
            _launch_path(staging, asset).chmod(0o700)
        if root.exists():
            previous = root.with_name(f".{root.name}-previous-{uuid.uuid4().hex}")
            root.rename(previous)
        staging.rename(root)
        replaced = True
        executable = verify_runtime(smoke=smoke)
        if previous is not None:
            shutil.rmtree(previous, ignore_errors=True)
        return executable
    except Exception:
        if replaced and root.exists():
            shutil.rmtree(root, ignore_errors=True)
        if previous is not None and previous.exists() and not root.exists():
            previous.rename(root)
        raise
    finally:
        archive_path.unlink(missing_ok=True)
        if staging.exists():
            shutil.rmtree(staging)


def _smoke_browser_context() -> None:
    from camoufox import DefaultAddons
    from camoufox.sync_api import Camoufox
    from playwright.sync_api import Browser
    from typing import cast

    with Camoufox(
        headless=True,
        persistent_context=False,
        exclude_addons=[DefaultAddons.UBO],
        os={"lin": "linux", "mac": "macos", "win": "windows"}[resolve_asset().os_name],
    ) as launched:
        browser = cast(Browser, launched)
        context = browser.new_context(locale="zh-CN")
        page = context.new_page()
        page.goto("about:blank")
        if page.evaluate("2 + 2") != 4:
            raise CamoufoxRuntimeError("Camoufox browser context smoke test returned an invalid result")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    install_parser = subparsers.add_parser("install")
    install_parser.add_argument("--force", action="store_true")
    install_parser.add_argument("--smoke", action="store_true")
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--smoke", action="store_true")
    subparsers.add_parser("validate-manifest")
    args = parser.parse_args(argv)

    try:
        if args.command == "install":
            executable = install_runtime(force=args.force, smoke=args.smoke)
            print(executable)
        elif args.command == "verify":
            executable = verify_runtime(smoke=args.smoke)
            print(executable)
        else:
            validate_manifest()
            _assert_dependency_versions()
            print(RUNTIME_FULL_VERSION)
    except CamoufoxRuntimeError as exc:
        print(f"boss-cli: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
