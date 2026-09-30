"""Tests for CCXTExchange helper logic (no network)."""

from src.exchange.ccxt import CCXTExchange, _is_placeholder_value


def _hyperliquid_config(options=None):
    """A minimal config dict matching the exchanges.yaml hyperliquid section."""
    config = {
        "type": "ccxt",
        "enabled": True,
        "default_network": "testnet",
        "networks": {
            "mainnet": {
                "rest_base_url": "https://api.hyperliquid.xyz",
                "websocket_url": "wss://api.hyperliquid.xyz/ws",
            },
            "testnet": {
                "rest_base_url": "https://api.hyperliquid-testnet.xyz",
                "websocket_url": "wss://api.hyperliquid-testnet.xyz/ws",
            },
        },
        "fees": {"taker": 0.00015, "maker": 0.00045},
    }
    if options is not None:
        config["options"] = options
    return config


class _FakeCCXT:
    """Minimal stand-in for the ccxt instance used by _filter_hip3_dexes."""

    def __init__(self, perp_dexs):
        self.options = {
            "fetchMarkets": {
                "types": ["spot", "swap", "hip3"],
                "hip3": {"dexes": ["hyna", "xyz", "io"]},
            }
        }
        self._perp_dexs = perp_dexs

    async def publicPostInfo(self, params):
        return self._perp_dexs


# ---- _is_placeholder_value ----


def test_placeholder_values_detected():
    assert _is_placeholder_value("your_binance_api_key") is True
    assert _is_placeholder_value("your_private_key") is True
    assert _is_placeholder_value("") is True
    assert _is_placeholder_value("xxxxx") is True
    assert _is_placeholder_value("0000") is True  # all-same-character sentinel


def test_real_credentials_not_marked_placeholder():
    # Real-looking keys / addresses must NOT be dropped. These are synthetic -- never
    # paste a value from config/secrets.<network>.yaml here; it is gitignored, this file is not.
    assert _is_placeholder_value("0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef") is False
    assert _is_placeholder_value("ABC123def456xyz789") is False


# ---- _build_ccxt_config merge ----


def test_build_ccxt_config_merges_fetchmarkets_options():
    options = {"fetchMarkets": {"types": ["spot", "swap", "hip3"], "hip3": {"dexes": ["hyna", "xyz", "io"]}}}
    ex = CCXTExchange("hyperliquid", _hyperliquid_config(options), {})
    cfg = ex._build_ccxt_config()
    fm = cfg["options"]["fetchMarkets"]
    assert fm["types"] == ["spot", "swap", "hip3"]
    assert fm["hip3"]["dexes"] == ["hyna", "xyz", "io"]
    assert cfg["options"]["defaultType"] == "swap"
    assert cfg["options"]["testnet"] is True


def test_build_ccxt_config_default_excludes_hip3():
    ex = CCXTExchange("hyperliquid", _hyperliquid_config(), {})
    fm = ex._build_ccxt_config()["options"]["fetchMarkets"]
    assert fm["types"] == ["spot", "swap"]
    assert "hip3" not in fm


# ---- _filter_hip3_dexes guard ----


async def test_filter_hip3_dexes_drops_missing_dex():
    # testnet: `io` (EntropyIO) is mainnet-only, so it is dropped, hyna/xyz kept.
    ex = CCXTExchange("hyperliquid", _hyperliquid_config(), {})
    ex.ccxt_exchange = _FakeCCXT([None, {"name": "hyna"}, {"name": "xyz"}])
    await ex._filter_hip3_dexes()
    fm = ex.ccxt_exchange.options["fetchMarkets"]
    assert fm["hip3"]["dexes"] == ["hyna", "xyz"]
    assert fm["types"] == ["spot", "swap", "hip3"]


async def test_filter_hip3_dexes_disables_when_all_missing():
    ex = CCXTExchange("hyperliquid", _hyperliquid_config(), {})
    ex.ccxt_exchange = _FakeCCXT([None, {"name": "other"}])
    await ex._filter_hip3_dexes()
    fm = ex.ccxt_exchange.options["fetchMarkets"]
    assert "hip3" not in fm["types"]
    assert "dexes" not in fm.get("hip3", {})


async def test_filter_hip3_dexes_ignores_non_hyperliquid():
    ex = CCXTExchange("binance", _hyperliquid_config(), {})
    ex.ccxt_exchange = _FakeCCXT([None, {"name": "hyna"}])
    await ex._filter_hip3_dexes()
    fm = ex.ccxt_exchange.options["fetchMarkets"]
    assert fm["types"] == ["spot", "swap", "hip3"]  # untouched


async def test_filter_hip3_dexes_disables_on_perpdexs_failure():
    ex = CCXTExchange("hyperliquid", _hyperliquid_config(), {})
    fake = _FakeCCXT([None, {"name": "hyna"}])

    async def boom(params):
        raise RuntimeError("network")

    fake.publicPostInfo = boom
    ex.ccxt_exchange = fake
    await ex._filter_hip3_dexes()
    fm = ex.ccxt_exchange.options["fetchMarkets"]
    assert "hip3" not in fm["types"]
    assert "dexes" not in fm.get("hip3", {})


async def _assert_hyperliquid_account_and_signer(*, vault_enabled: bool) -> None:
    """Run real CCXT queries/signing with ephemeral, unrelated test identities."""
    from unittest.mock import AsyncMock

    import ccxt.async_support as ccxt
    import pytest
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, utils

    master = ec.generate_private_key(ec.SECP256K1())
    agent = ec.generate_private_key(ec.SECP256K1())
    agent_key = agent.private_numbers().private_value.to_bytes(32, "big").hex()
    probe = ccxt.hyperliquid()
    master_address = probe.privateKeyToAddress(master.private_numbers().private_value.to_bytes(32, "big").hex())
    agent_address = probe.privateKeyToAddress(agent_key)
    vault_address = None
    if vault_enabled:
        vault_key = ec.generate_private_key(ec.SECP256K1())
        vault_address = probe.privateKeyToAddress(vault_key.private_numbers().private_value.to_bytes(32, "big").hex())
    await probe.close()
    credentials = {
        "master_wallet_address": master_address,
        "api_wallet_address": agent_address,
        "api_wallet_private_key": agent_key,
    }
    if vault_address:
        credentials["vaultAddress"] = vault_address
    adapter = CCXTExchange("hyperliquid", _hyperliquid_config(), credentials)
    config = adapter._build_ccxt_config()
    assert master_address != agent_address
    assert config["walletAddress"] == master_address
    assert config["privateKey"] == agent_key
    client = ccxt.hyperliquid(config)
    adapter.ccxt_exchange = client
    client.set_markets(
        [
            {
                "id": "BTC",
                "symbol": "BTC/USDC:USDC",
                "base": "BTC",
                "baseId": "0",
                "quote": "USDC",
                "settle": "USDC",
                "type": "swap",
                "spot": False,
                "swap": True,
                "linear": True,
                "inverse": False,
                "contract": True,
                "contractSize": 1,
                "precision": {"amount": 0.001, "price": 1},
                "info": {"szDecimals": 3},
            },
        ]
    )
    client.publicPostInfo = AsyncMock(
        return_value={"marginSummary": {"accountValue": "10000", "totalMarginUsed": "0"}, "withdrawable": "10000"}
    )
    # create_orders_request only constructs/signs a payload; never submit it.
    client.fetch = AsyncMock(side_effect=AssertionError("network transport is prohibited in this test"))
    signed_messages = []
    original_sign_message = client.sign_message

    def sign_message(message, private_key):
        signed_messages.append(message)
        return original_sign_message(message, private_key)

    client.sign_message = sign_message
    try:
        balance = await adapter.fetch_balance({"type": "swap", "enableUnifiedMargin": False})
        assert balance["free"]["USDC"] == 10000
        client.publicPostInfo.assert_awaited_once_with({"type": "clearinghouseState", "user": master_address})
        payload = client.create_orders_request(
            [
                {
                    "symbol": "BTC/USDC:USDC",
                    "type": "limit",
                    "side": "buy",
                    "amount": "0.01",
                    "price": "20000",
                    "params": {"timeInForce": "IOC"},
                }
            ]
        )
        signature = payload["signature"]
        encoded_signature = utils.encode_dss_signature(int(signature["r"], 16), int(signature["s"], 16))
        digest = bytes.fromhex(client.hash_message(signed_messages[-1]).removeprefix("0x"))
        # The digest is already Keccak-hashed by CCXT. Prehashed avoids hashing
        # it again; SHA256 here specifies the shared 32-byte digest size only.
        verification = ec.ECDSA(utils.Prehashed(hashes.SHA256()))
        agent.public_key().verify(encoded_signature, digest, verification)
        with pytest.raises(InvalidSignature):
            master.public_key().verify(encoded_signature, digest, verification)
        if vault_address:
            assert config["options"]["vaultAddress"] == vault_address
            assert payload["vaultAddress"] == vault_address.removeprefix("0x")
            # vaultAddress selects the signed action target, not the default
            # INFO query user. Explicitly request that account when reading it.
            await adapter.fetch_balance({"type": "swap", "user": vault_address, "enableUnifiedMargin": False})
            assert client.publicPostInfo.call_args.args[0]["user"] == vault_address
        else:
            assert "vaultAddress" not in payload
        client.fetch.assert_not_awaited()
    finally:
        await adapter.close()


async def test_hyperliquid_master_account_and_api_agent_signer_are_independent():
    await _assert_hyperliquid_account_and_signer(vault_enabled=False)


async def test_hyperliquid_vault_is_action_target_not_implicit_query_user():
    await _assert_hyperliquid_account_and_signer(vault_enabled=True)


async def test_hyperliquid_signing_network_is_pinned_after_user_options():
    import ccxt.async_support as ccxt
    from cryptography.hazmat.primitives.asymmetric import ec

    agent = ec.generate_private_key(ec.SECP256K1())
    agent_key = agent.private_numbers().private_value.to_bytes(32, "big").hex()
    signatures = []
    for network, expected_source in (("testnet", "b"), ("mainnet", "a")):
        # Deliberately contradictory user flags cannot override network choice.
        config = _hyperliquid_config({"sandboxMode": network == "mainnet", "testnet": network == "mainnet"})
        config["default_network"] = network
        probe = ccxt.hyperliquid()
        master = ec.generate_private_key(ec.SECP256K1())
        credentials = {
            "master_wallet_address": probe.privateKeyToAddress(
                master.private_numbers().private_value.to_bytes(32, "big").hex()
            ),
            "api_wallet_address": probe.privateKeyToAddress(agent_key),
            "api_wallet_private_key": agent_key,
        }
        await probe.close()
        adapter = CCXTExchange("hyperliquid", config, credentials)
        client = ccxt.hyperliquid(adapter._build_ccxt_config())
        adapter.ccxt_exchange = client
        phantom_agents = []
        original = client.construct_phantom_agent

        def construct_phantom_agent(action_hash, is_testnet=True, *, _original=original, _agents=phantom_agents):
            result = _original(action_hash, is_testnet)
            _agents.append(result)
            return result

        client.construct_phantom_agent = construct_phantom_agent
        try:
            signatures.append(client.sign_l1_action({"type": "cancel", "cancels": []}, 1700000000000))
            assert phantom_agents[-1]["source"] == expected_source
            assert client.options["sandboxMode"] is (network == "testnet")
            assert ("testnet" in client.urls["api"]["private"]) is (network == "testnet")
        finally:
            await adapter.close()
    assert signatures[0] != signatures[1]


def _hyperliquid_test_credentials() -> dict[str, str]:
    """Generate disposable fixture identities; never read configured wallets."""
    import ccxt.async_support as ccxt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    def wallet():
        key = ec.generate_private_key(ec.SECP256K1())
        private = key.private_numbers().private_value.to_bytes(32, "big").hex()
        public = key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        return "0x" + ccxt.Exchange.hash(public[1:], "keccak", "hex")[-40:], private

    master, _ = wallet()
    address, private = wallet()
    return {"master_wallet_address": master, "api_wallet_address": address, "api_wallet_private_key": private}


async def test_hyperliquid_legacy_credentials_rejected_before_client_or_network(monkeypatch):
    from unittest.mock import Mock

    import pytest

    constructor = Mock(side_effect=AssertionError("CCXT client construction must not happen"))
    monkeypatch.setattr("src.exchange.ccxt.ccxt.hyperliquid", constructor)
    for field in ("walletAddress", "wallet_address", "privateKey", "private_key"):
        with pytest.raises(
            ValueError, match="master_wallet_address, api_wallet_address and api_wallet_private_key"
        ) as error:
            CCXTExchange("hyperliquid", _hyperliquid_config(), {field: "legacy-value-must-not-be-printed"})
        assert "legacy-value-must-not-be-printed" not in str(error.value)
    constructor.assert_not_called()


def test_hyperliquid_api_keypair_mismatch_has_safe_error():
    import pytest

    credentials = _hyperliquid_test_credentials()
    credentials["api_wallet_address"] = _hyperliquid_test_credentials()["api_wallet_address"]
    with pytest.raises(ValueError, match="does not match") as error:
        CCXTExchange("hyperliquid", _hyperliquid_config(), credentials)
    assert all(value not in str(error.value) for value in credentials.values())


def test_hyperliquid_api_wallet_requires_complete_pair_and_master():
    import pytest

    for missing in ("api_wallet_address", "api_wallet_private_key", "master_wallet_address"):
        credentials = _hyperliquid_test_credentials()
        credentials.pop(missing)
        with pytest.raises(ValueError, match=r"must be configured together|required with API wallet"):
            CCXTExchange("hyperliquid", _hyperliquid_config(), credentials)


def test_hyperliquid_master_key_cannot_be_used_as_api_wallet():
    import pytest

    credentials = _hyperliquid_test_credentials()
    credentials["master_wallet_address"] = credentials["api_wallet_address"]
    with pytest.raises(ValueError, match="API wallet must differ"):
        CCXTExchange("hyperliquid", _hyperliquid_config(), credentials)


def test_hyperliquid_addresses_accept_case_and_optional_prefix():
    credentials = _hyperliquid_test_credentials()
    master, address, private = credentials.values()
    credentials["master_wallet_address"] = master.upper()
    credentials["api_wallet_address"] = address[2:].upper()
    credentials["api_wallet_private_key"] = "0X" + private.upper()
    adapter = CCXTExchange("hyperliquid", _hyperliquid_config(), credentials)
    normalized = adapter._build_ccxt_config()
    assert normalized["walletAddress"] == master
    assert normalized["privateKey"] == private
    assert adapter.has_credentials()
    assert "api_wallet_address" not in normalized


async def test_hyperliquid_master_only_supports_balance_queries_without_signing():
    from unittest.mock import AsyncMock

    import ccxt.async_support as ccxt

    master = _hyperliquid_test_credentials()["master_wallet_address"]
    adapter = CCXTExchange("hyperliquid", _hyperliquid_config(), {"master_wallet_address": master})
    assert not adapter.has_credentials()
    config = adapter._build_ccxt_config()
    assert config["walletAddress"] == master and "privateKey" not in config
    client = ccxt.hyperliquid(config)
    adapter.ccxt_exchange = client
    client.publicPostInfo = AsyncMock(
        return_value={"marginSummary": {"accountValue": "12", "totalMarginUsed": "0"}, "withdrawable": "12"}
    )
    try:
        result = await adapter.fetch_balance({"type": "swap", "enableUnifiedMargin": False})
        assert result["free"]["USDC"] == 12
        client.publicPostInfo.assert_awaited_once_with({"type": "clearinghouseState", "user": master})
    finally:
        await adapter.close()


def test_hyperliquid_placeholder_three_fields_are_public_only():
    fields = ("master_wallet_address", "api_wallet_address", "api_wallet_private_key")
    adapter = CCXTExchange("hyperliquid", _hyperliquid_config(), {field: "your_" + field for field in fields})
    config = adapter._build_ccxt_config()
    assert not adapter.has_credentials()
    assert "walletAddress" not in config and "privateKey" not in config
