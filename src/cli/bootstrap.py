"""Build the Orchestrator from config files or injected mocks."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from src.coordinator.orchestrator import Orchestrator
    from src.persistence.store import PersistenceStore

from src.core.base_exchange import NetworkType

logger = logging.getLogger(__name__)


async def build_orchestrator(
    exchanges_config_path: Path = Path("config/exchanges.yaml"),
    secrets_config_path: Path = Path("config/secrets.yaml"),
    sqlite_path: Path = Path("data/onefill.db"),
    jsonl_dir: Path = Path("logs/"),
    _exchanges: dict | None = None,
    _store: PersistenceStore | None = None,
    target_network: NetworkType | None = None,
    poll_interval_ms: int = 500,
    use_websocket: bool = True,
) -> Orchestrator:
    """
    Build a fully wired Orchestrator.

    1. Load exchanges.yaml + secrets.yaml (skip if _exchanges provided)
    2. Create ExchangeFactory, initialise exchanges (skip if _exchanges provided)
    3. Build InstrumentRegistry, load all instruments
    4. Build QuoteFetcher
    5. Build PersistenceStore (skip if _store provided), initialise
    6. Build Orchestrator(registry, quote_fetcher, exchanges, store)
    7. Return

    target_network overrides the default_network from exchanges.yaml.
    DI params _exchanges and _store are for test injection only — never exposed in CLI.
    """
    from src.coordinator.orchestrator import Orchestrator
    from src.core.exchange_factory import ExchangeFactory
    from src.market.quote_fetcher import QuoteFetcher
    from src.market.registry import InstrumentRegistry
    from src.persistence.store import PersistenceStore

    # 1. Initialise exchanges (or use injected)
    if _exchanges is not None:
        exchanges = _exchanges
    else:
        if not exchanges_config_path.exists():
            raise FileNotFoundError(
                f"Exchanges config not found at {exchanges_config_path.absolute()}. "
                f"oneFill expects to be run from the project root (current cwd: {Path.cwd()})."
            )
        if not secrets_config_path.exists():
            raise FileNotFoundError(
                f"Secrets config not found at {secrets_config_path.absolute()}. "
                f"Copy config/secrets.example.yaml to config/secrets.yaml and fill in your credentials."
            )

        with open(exchanges_config_path) as f:
            config_data = yaml.safe_load(f)
        with open(secrets_config_path) as f:
            secrets_data = yaml.safe_load(f)

        exchanges = await ExchangeFactory.initialize_exchanges(
            config_data.get("exchanges", {}),
            secrets_data,
            target_network=target_network,
        )

    # 2. Build PersistenceStore (needed early for instrument cache)
    if _store is not None:
        store = _store
    else:
        store = PersistenceStore(sqlite_path, jsonl_dir)
        await store.initialize()

    # 3. Build InstrumentRegistry (uses store for cache)
    registry = InstrumentRegistry()
    await registry.load_all(exchanges, store=store)

    # 4. Build QuoteFetcher with WebSocket orderbook cache
    from src.market.orderbook_cache import OrderbookCache

    ob_cache = None
    if use_websocket:
        # Build venue config list from loaded exchanges — the cache creates
        # its own ccxt.pro instances, decoupled from the main REST exchanges.
        venue_configs = [{"name": name, "network": exc.network_type.value} for name, exc in exchanges.items()]
        ob_cache = OrderbookCache(venue_configs)
        instruments_by_venue: dict[str, list] = {}
        for inst in registry.list_instruments():
            instruments_by_venue.setdefault(inst.venue, []).append(inst)
        try:
            await ob_cache.start(instruments_by_venue)
        except Exception:
            logger.warning("Orderbook cache warmup failed, using REST fallback", exc_info=True)

    quote_fetcher = QuoteFetcher(exchanges, cache=ob_cache)

    # 5. Load risk config (optional)
    risk_path = Path("config/risk.yaml")
    risk_validator = None
    if risk_path.exists():
        with open(risk_path) as f:
            risk_data = yaml.safe_load(f) or {}
        from src.coordinator.risk import RiskValidator

        risk_validator = RiskValidator(store, risk_data.get("risk", {}))

    # 6. Build Orchestrator
    return Orchestrator(
        registry,
        quote_fetcher,
        exchanges,
        store,
        poll_interval_ms=poll_interval_ms,
        risk_validator=risk_validator,
        use_websocket=use_websocket,
    )


async def build_store(
    sqlite_path: Path = Path("data/onefill.db"),
    jsonl_dir: Path = Path("logs/"),
) -> PersistenceStore:
    """Build and initialise a standalone PersistenceStore (no exchanges needed)."""
    from src.persistence.store import PersistenceStore

    store = PersistenceStore(sqlite_path, jsonl_dir)
    await store.initialize()
    return store


async def build_arb_scanner(
    exchanges_config_path: Path = Path("config/exchanges.yaml"),
    secrets_config_path: Path = Path("config/secrets.yaml"),
    sqlite_path: Path = Path("data/onefill.db"),
    jsonl_dir: Path = Path("logs/"),
    _exchanges: dict | None = None,
) -> tuple:
    """Build the funding rate arbitrage scanner components.

    Returns (exchanges, registry, store, cache, pair_matcher, comparator).
    """
    from src.core.exchange_factory import ExchangeFactory
    from src.market.funding_rate_cache import FundingRateCache
    from src.market.pair_matcher import PairMatcher
    from src.market.registry import InstrumentRegistry
    from src.persistence.store import PersistenceStore
    from src.strategy.funding_arb.comparator import FundingRateComparator

    store = PersistenceStore(sqlite_path, jsonl_dir)
    await store.initialize()

    if _exchanges is not None:
        exchanges = _exchanges
    else:
        factory = ExchangeFactory(exchanges_config_path, secrets_config_path)
        exchanges = await factory.initialize_exchanges()

    registry = InstrumentRegistry()
    # Load cached instruments if available; real fetch requires testnet connectivity
    try:
        cached = await store.load_instruments()
        if cached:
            for inst in cached:
                registry.add(inst)
    except Exception:
        logger.warning("No cached instruments found — run 'onefill order --dry-run' first to populate")
        pass

    cache = FundingRateCache(exchanges)
    pair_matcher = PairMatcher(registry)
    comparator = FundingRateComparator()

    return exchanges, registry, store, cache, pair_matcher, comparator
