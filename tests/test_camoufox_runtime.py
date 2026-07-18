"""Tests for reproducible Camoufox browser artifact management."""

from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

import pytest

from boss_cli.camoufox_runtime import (
    ASSETS,
    RUNTIME_FULL_VERSION,
    CamoufoxRuntimeError,
    resolve_asset,
    safe_extract,
    validate_manifest,
)


def test_camoufox_asset_manifest_is_complete_and_digest_pinned():
    validate_manifest()

    linux = resolve_asset("Linux", "x86_64")
    assert linux is ASSETS[("lin", "x86_64")]
    assert RUNTIME_FULL_VERSION in linux.filename
    assert len(linux.sha256) == 64
    assert linux.url.startswith("https://github.com/daijro/camoufox/releases/download/")


def test_camoufox_asset_resolution_rejects_unsupported_platform():
    with pytest.raises(CamoufoxRuntimeError, match="Unsupported"):
        resolve_asset("Plan9", "mips")


def test_camoufox_archive_extraction_rejects_path_traversal(tmp_path: Path):
    archive = tmp_path / "runtime.zip"
    with ZipFile(archive, "w") as bundle:
        bundle.writestr("../escaped", "unsafe")

    with pytest.raises(CamoufoxRuntimeError, match="Unsafe path"):
        safe_extract(archive, tmp_path / "output")
    assert not (tmp_path / "escaped").exists()


def test_camoufox_archive_extraction_accepts_normal_files(tmp_path: Path):
    archive = tmp_path / "runtime.zip"
    with ZipFile(archive, "w") as bundle:
        bundle.writestr("browser/version.txt", RUNTIME_FULL_VERSION)

    output = tmp_path / "output"
    safe_extract(archive, output)

    assert (output / "browser/version.txt").read_text(encoding="utf-8") == RUNTIME_FULL_VERSION
