"""YAML configuration loading and validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class InstanceConfig:
    """Connection details for a qBittorrent instance."""

    host: str
    username: str
    password: str
    name: str = ""


@dataclass
class SyncConfig:
    """Tuning knobs for the sync behaviour."""

    min_seeding_time_minutes: int = 10
    skip_hash_check: bool = True


@dataclass
class AppConfig:
    """Top-level application configuration."""

    master: InstanceConfig
    children: list[InstanceConfig]
    sync: SyncConfig = field(default_factory=SyncConfig)


class ConfigError(Exception):
    """Raised when configuration is invalid or missing."""


def _parse_instance(data: dict, default_name: str = "") -> InstanceConfig:
    missing = [k for k in ("host", "username", "password") if k not in data]
    if missing:
        raise ConfigError(f"Instance config missing required fields: {', '.join(missing)}")
    return InstanceConfig(
        host=data["host"],
        username=data["username"],
        password=data["password"],
        name=data.get("name", default_name),
    )


def load_config(path: str | Path) -> AppConfig:
    """Load and validate a YAML config file, returning an ``AppConfig``."""
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")

    with open(config_path) as fh:
        raw = yaml.safe_load(fh)

    if not isinstance(raw, dict):
        raise ConfigError("Config file must be a YAML mapping")

    if "master" not in raw:
        raise ConfigError("Config must contain a 'master' section")
    master = _parse_instance(raw["master"], default_name="master")

    if "children" not in raw or not raw["children"]:
        raise ConfigError("Config must contain a non-empty 'children' list")
    children = [
        _parse_instance(child, default_name=f"child-{i}")
        for i, child in enumerate(raw["children"], start=1)
    ]

    sync_raw = raw.get("sync", {})
    sync = SyncConfig(
        min_seeding_time_minutes=int(sync_raw.get("min_seeding_time_minutes", 10)),
        skip_hash_check=bool(sync_raw.get("skip_hash_check", True)),
    )

    return AppConfig(master=master, children=children, sync=sync)
