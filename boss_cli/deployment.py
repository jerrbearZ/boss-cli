"""Validated non-secret deployment configuration."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .platform import PATHS, ensure_private_directory, ensure_private_file

DEPLOYMENT_SCHEMA_VERSION = 1


class DeploymentConfigError(ValueError):
    """Raised when deployment configuration is missing or unsafe."""


@dataclass(frozen=True)
class DeploymentConfig:
    schema_version: int = DEPLOYMENT_SCHEMA_VERSION
    live: bool = False
    poll_interval_seconds: int = 30
    error_backoff_seconds: int = 120
    candidate_limit: int = 20
    max_actions_per_cycle: int = 10
    action_delay_seconds: int = 60
    request_wechat: bool = True
    model: str = "qwen-plus"
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8765

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "DeploymentConfig":
        expected = set(cls.__dataclass_fields__)
        unknown = sorted(set(value) - expected)
        missing = sorted(expected - set(value))
        if unknown:
            raise DeploymentConfigError(f"Unknown deployment configuration keys: {', '.join(unknown)}")
        if missing:
            raise DeploymentConfigError(f"Missing deployment configuration keys: {', '.join(missing)}")
        try:
            config = cls(**value)
        except TypeError as exc:
            raise DeploymentConfigError("Deployment configuration has invalid value types") from exc
        config.validate()
        return config

    def validate(self) -> None:
        if (
            not isinstance(self.schema_version, int)
            or isinstance(self.schema_version, bool)
            or self.schema_version != DEPLOYMENT_SCHEMA_VERSION
        ):
            raise DeploymentConfigError(f"Unsupported deployment schema version: {self.schema_version}")
        for field in ("live", "request_wechat"):
            if not isinstance(getattr(self, field), bool):
                raise DeploymentConfigError(f"{field} must be true or false")
        ranges = {
            "poll_interval_seconds": (1, 86400),
            "error_backoff_seconds": (1, 86400),
            "candidate_limit": (1, 1000),
            "max_actions_per_cycle": (0, 100),
            "action_delay_seconds": (0, 86400),
            "dashboard_port": (1, 65535),
        }
        for field, (minimum, maximum) in ranges.items():
            item = getattr(self, field)
            if not isinstance(item, int) or isinstance(item, bool) or not minimum <= item <= maximum:
                raise DeploymentConfigError(f"{field} must be an integer from {minimum} to {maximum}")
        if self.dashboard_host != "127.0.0.1":
            raise DeploymentConfigError("dashboard_host must be exactly 127.0.0.1")
        if not isinstance(self.model, str) or not self.model.strip():
            raise DeploymentConfigError("model must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_deployment_config_path() -> Path:
    override = os.environ.get("BOSS_DEPLOYMENT_CONFIG")
    return Path(override).expanduser() if override else PATHS.deployment_config


def load_deployment_config(path: Path | str | None = None) -> DeploymentConfig:
    config_path = Path(path).expanduser() if path else default_deployment_config_path()
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DeploymentConfigError(f"Deployment configuration does not exist: {config_path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise DeploymentConfigError(f"Deployment configuration is not valid JSON: {config_path}") from exc
    if not isinstance(raw, dict):
        raise DeploymentConfigError("Deployment configuration must contain a JSON object")
    return DeploymentConfig.from_dict(raw)


def write_deployment_config(
    config: DeploymentConfig,
    path: Path | str | None = None,
    *,
    force: bool = False,
) -> Path:
    """Atomically write a validated non-secret configuration."""
    config.validate()
    config_path = Path(path).expanduser() if path else default_deployment_config_path()
    if config_path.exists() and not force:
        raise DeploymentConfigError(f"Deployment configuration already exists: {config_path}")
    ensure_private_directory(config_path.parent)
    temporary = config_path.with_suffix(config_path.suffix + ".tmp")
    temporary.write_text(json.dumps(config.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    ensure_private_file(temporary)
    os.replace(temporary, config_path)
    ensure_private_file(config_path)
    return config_path


def set_live_mode(path: Path | str | None, *, live: bool) -> DeploymentConfig:
    current = load_deployment_config(path)
    value = current.to_dict()
    value["live"] = live
    updated = DeploymentConfig.from_dict(value)
    write_deployment_config(updated, path, force=True)
    return updated
