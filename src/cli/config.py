"""Load configuration at the CLI boundary and bind credentials to a network."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from src.market.instrument import NetworkType


def read_yaml_mapping(path: Path) -> dict[str, Any]:
    """Read a YAML mapping without exposing document contents in errors."""
    try:
        with path.open() as handle:
            data = yaml.safe_load(handle)
    except (yaml.YAMLError, ValueError, TypeError, OverflowError):
        raise ValueError(f"Invalid YAML in configuration file: {path}") from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Configuration must be a YAML mapping: {path}")
    return data


def load_exchange_configuration(
    exchanges_config_path: Path = Path("config/exchanges.yaml"),
    secrets_config_path: Path | None = None,
    *,
    target_network: NetworkType | None = None,
    venues: tuple[str, ...] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Return enabled venue configurations and their matching credentials.

    Network files live beside ``exchanges_config_path``. Missing automatic
    files or venue entries mean anonymous access, never a fallback to another
    network. An explicit secrets file must exist and match every selected
    venue's effective network. ``venues`` also selects normally disabled venues
    for the guarded testnet commands. Input configuration is not modified.
    """
    document = read_yaml_mapping(exchanges_config_path)
    configured = document.get("exchanges", {})
    if not isinstance(configured, dict):
        raise ValueError(f"exchanges must be a mapping: {exchanges_config_path}")
    if venues is not None:
        if not venues:
            raise ValueError("Select at least one exchange")
        missing = [name for name in venues if name not in configured]
        if missing:
            raise ValueError(f"Missing exchange configuration: {', '.join(missing)}")

    effective: dict[str, dict[str, Any]] = {}
    for name in dict.fromkeys(venues) if venues is not None else configured:
        config = configured[name]
        if not isinstance(config, dict):
            raise ValueError(f"Exchange configuration must be a mapping: venue={name}")
        if venues is None and not config.get("enabled", False):
            continue
        try:
            network = NetworkType(
                target_network if target_network is not None else config.get("default_network", "testnet")
            )
        except (TypeError, ValueError):
            raise ValueError(f"Invalid network for venue={name}; expected testnet or mainnet") from None
        effective[name] = {**config, "enabled": True, "default_network": network.value}

    loaded: dict[Path, dict[str, Any]] = {}
    secrets: dict[str, dict[str, Any]] = {}
    for name, config in effective.items():
        network_name = config["default_network"]
        path = (
            secrets_config_path
            if secrets_config_path is not None
            else (exchanges_config_path.parent / f"secrets.{network_name}.yaml")
        )
        if path not in loaded:
            # Only an absent automatic path permits anonymous access. Invalid
            # files, permission errors and missing explicit paths must surface.
            try:
                loaded[path] = read_yaml_mapping(path)
            except FileNotFoundError:
                if secrets_config_path is not None:
                    raise
                loaded[path] = {"network": network_name}
        credentials = loaded[path]
        if credentials.get("network") != network_name:
            raise ValueError(
                f"Credentials network mismatch for venue={name}: {path} must declare network: {network_name}. "
                "Use separate secrets.testnet.yaml / secrets.mainnet.yaml files; legacy unmarked files are unsupported."
            )
        venue_secrets = credentials.get(name, {})
        if not isinstance(venue_secrets, dict):
            raise ValueError(f"Credentials must be a mapping for venue={name}: {path}")
        secrets[name] = dict(venue_secrets)
    return effective, secrets
