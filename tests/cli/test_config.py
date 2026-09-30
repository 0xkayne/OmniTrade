"""Network configuration selection using synthetic, local-only credentials."""

import pytest
import yaml

from src.cli.config import load_exchange_configuration, read_yaml_mapping
from src.market.instrument import NetworkType


def _write_yaml(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value))
    return path


def _exchange(network="testnet", *, enabled=True):
    return {"type": "ccxt", "enabled": enabled, "default_network": network}


def _credentials(venue, network):
    fields = {
        "hyperliquid": ("master_wallet_address", "api_wallet_address", "api_wallet_private_key"),
        "arcus": ("master_wallet_address", "api_key", "api_signing_key"),
    }.get(venue, ("apiKey", "secret"))
    return {field: f"synthetic-{venue}-{network}-{field}" for field in fields}


@pytest.fixture
def config_path(tmp_path):
    return _write_yaml(
        tmp_path / "deployment" / "exchanges.yaml",
        {"exchanges": {"binance": _exchange()}},
    )


@pytest.mark.parametrize("target_network", [None, NetworkType.TESTNET, NetworkType.MAINNET])
def test_network_override_and_per_venue_defaults(config_path, target_network):
    original = {
        "exchanges": {
            "binance": _exchange("mainnet"),
            "hyperliquid": _exchange(),
            "arcus": {"type": "native", "enabled": True},
            "disabled": _exchange("mainnet", enabled=False),
        }
    }
    _write_yaml(config_path, original)
    for network in NetworkType:
        _write_yaml(
            config_path.parent / f"secrets.{network.value}.yaml",
            {
                "network": network.value,
                **{venue: _credentials(venue, network.value) for venue in original["exchanges"]},
            },
        )

    configs, secrets = load_exchange_configuration(config_path, target_network=target_network)

    assert set(configs) == {"binance", "hyperliquid", "arcus"}
    assert set(secrets) == set(configs)
    for venue in configs:
        expected = target_network.value if target_network else ("mainnet" if venue == "binance" else "testnet")
        assert configs[venue]["default_network"] == expected
        assert secrets[venue] == _credentials(venue, expected)
    assert yaml.safe_load(config_path.read_text()) == original


def test_only_requested_network_file_is_read(config_path):
    _write_yaml(
        config_path.parent / "secrets.testnet.yaml",
        {
            "network": "testnet",
            "binance": {"apiKey": "synthetic-demo"},
        },
    )
    # These deliberately invalid files must remain unread.
    for name in ("secrets.mainnet.yaml", "secrets.yaml"):
        (config_path.parent / name).write_text("invalid: [")

    _, secrets = load_exchange_configuration(config_path)

    assert secrets == {"binance": {"apiKey": "synthetic-demo"}}


@pytest.mark.parametrize("missing", ["file", "venue"])
def test_missing_credentials_allow_public_access_without_fallback(config_path, missing):
    _write_yaml(
        config_path.parent / "secrets.mainnet.yaml",
        {
            "network": "mainnet",
            "binance": {"apiKey": "synthetic-mainnet"},
        },
    )
    _write_yaml(
        config_path.parent / "secrets.yaml",
        {
            "binance": {"apiKey": "synthetic-legacy"},
        },
    )
    if missing == "venue":
        _write_yaml(config_path.parent / "secrets.testnet.yaml", {"network": "testnet"})

    _, secrets = load_exchange_configuration(config_path)

    assert secrets == {"binance": {}}


@pytest.mark.parametrize(
    "content",
    [
        "- synthetic-sensitive-value\n",
        "synthetic-sensitive-value\n",
        "binance: [synthetic-sensitive-value\n",
        "binance: {apiKey: synthetic-sensitive-value}\n",
        "network: mainnet\nbinance: {apiKey: synthetic-sensitive-value}\n",
        "network: testnet\nbinance: [synthetic-sensitive-value]\n",
        "network: testnet\nbinance: synthetic-sensitive-value\n",
        "network: testnet\nbinance: {secret: !!int synthetic-sensitive-value}\n",
        "network: testnet\nbinance: {secret: !!float synthetic-sensitive-value}\n",
    ],
)
def test_invalid_credentials_are_rejected_without_disclosing_values(config_path, content):
    (config_path.parent / "secrets.testnet.yaml").write_text(content)

    with pytest.raises(ValueError) as error:
        load_exchange_configuration(config_path)

    assert "synthetic-sensitive-value" not in str(error.value)


def test_explicit_credentials_path_takes_precedence(config_path, tmp_path):
    explicit = _write_yaml(
        tmp_path / "mounted" / "credentials.yaml",
        {
            "network": "mainnet",
            "binance": {"apiKey": "synthetic-explicit"},
        },
    )
    (config_path.parent / "secrets.mainnet.yaml").write_text("invalid: [")

    configs, secrets = load_exchange_configuration(
        config_path,
        explicit,
        target_network=NetworkType.MAINNET,
    )

    assert configs["binance"]["default_network"] == "mainnet"
    assert secrets == {"binance": {"apiKey": "synthetic-explicit"}}


def test_explicit_missing_file_is_not_treated_as_anonymous(config_path, tmp_path):
    with pytest.raises(FileNotFoundError):
        load_exchange_configuration(config_path, tmp_path / "missing.yaml")


@pytest.mark.parametrize("network", [None, "mainnet"])
def test_explicit_credentials_require_matching_network(config_path, tmp_path, network):
    credentials = {"binance": {"apiKey": "synthetic-explicit-sensitive"}}
    if network is not None:
        credentials["network"] = network
    explicit = _write_yaml(tmp_path / "credentials.yaml", credentials)

    with pytest.raises(ValueError) as error:
        load_exchange_configuration(config_path, explicit)

    assert "synthetic-explicit-sensitive" not in str(error.value)


def test_explicit_file_cannot_supply_mixed_networks(config_path, tmp_path):
    _write_yaml(
        config_path,
        {
            "exchanges": {
                "binance": _exchange("mainnet"),
                "hyperliquid": _exchange(),
            }
        },
    )
    explicit = _write_yaml(tmp_path / "credentials.yaml", {"network": "testnet"})

    with pytest.raises(ValueError):
        load_exchange_configuration(config_path, explicit)


def test_selected_venues_enable_disabled_adapter_and_exclude_others(config_path):
    _write_yaml(
        config_path,
        {
            "exchanges": {
                "binance": _exchange(),
                "arcus": _exchange("mainnet", enabled=False),
            }
        },
    )

    configs, secrets = load_exchange_configuration(
        config_path,
        target_network=NetworkType.TESTNET,
        venues=("arcus",),
    )

    assert configs == {"arcus": _exchange()}
    assert secrets == {"arcus": {}}


def test_selected_venue_must_exist(config_path):
    with pytest.raises(ValueError):
        load_exchange_configuration(config_path, venues=("missing",))


def test_disabled_venues_do_not_require_credentials(config_path):
    _write_yaml(config_path, {"exchanges": {"binance": _exchange(enabled=False)}})
    (config_path.parent / "secrets.testnet.yaml").write_text("invalid: [")

    assert load_exchange_configuration(config_path) == ({}, {})


@pytest.mark.parametrize("value", [[], "synthetic-sensitive-value", 42])
def test_yaml_reader_rejects_non_mapping_roots(tmp_path, value):
    path = _write_yaml(tmp_path / "config.yaml", value)

    with pytest.raises(ValueError) as error:
        read_yaml_mapping(path)

    assert "synthetic-sensitive-value" not in str(error.value)


def test_yaml_reader_preserves_mapping(tmp_path):
    expected = {"network": "testnet", "binance": {"apiKey": "synthetic-key"}}
    path = _write_yaml(tmp_path / "config.yaml", expected)

    assert read_yaml_mapping(path) == expected
