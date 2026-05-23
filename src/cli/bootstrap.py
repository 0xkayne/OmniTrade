"""Build the Orchestrator from config files or injected mocks."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from src.coordinator.orchestrator import Orchestrator
    from src.persistence.store import PersistenceStore

logger = logging.getLogger(__name__)


async def build_orchestrator(
    exchanges_config_path: Path = Path("config/exchanges.yaml"),
    secrets_config_path: Path = Path("config/secrets.yaml"),
    sqlite_path: Path = Path("data/onefill.db"),
    jsonl_dir: Path = Path("logs/"),
    _exchanges: dict | None = None,
    _store: PersistenceStore | None = None,
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

    DI params _exchanges and _store are for test injection only — never exposed in CLI.
    """
    from src.core.exchange_factory import ExchangeFactory
    from src.coordinator.orchestrator import Orchestrator
    from src.market.quote_fetcher import QuoteFetcher
    from src.market.registry import InstrumentRegistry
    from src.persistence.store import PersistenceStore

    # 1. Initialise exchanges (or use injected)
    if _exchanges is not None:
        exchanges = _exchanges
    else:
        if not exchanges_config_path.exists():
            raise FileNotFoundError(f"Exchanges config not found at {exchanges_config_path}")
        if not secrets_config_path.exists():
            raise FileNotFoundError(f"Secrets config not found at {secrets_config_path}")

        with open(exchanges_config_path) as f:
            config_data = yaml.safe_load(f)
        with open(secrets_config_path) as f:
            secrets_data = yaml.safe_load(f)

        exchanges = await ExchangeFactory.initialize_exchanges(
            config_data.get("exchanges", {}),
            secrets_data,
        )

    # 2. Build InstrumentRegistry
    registry = InstrumentRegistry()
    await registry.load_all(exchanges)

    # 3. Build QuoteFetcher
    quote_fetcher = QuoteFetcher(exchanges)

    # 4. Build PersistenceStore (or use injected)
    if _store is not None:
        store = _store
    else:
        store = PersistenceStore(sqlite_path, jsonl_dir)
        await store.initialize()

    # 5. Build Orchestrator
    return Orchestrator(registry, quote_fetcher, exchanges, store)


async def build_store(
    sqlite_path: Path = Path("data/onefill.db"),
    jsonl_dir: Path = Path("logs/"),
) -> PersistenceStore:
    """Build and initialise a standalone PersistenceStore (no exchanges needed)."""
    from src.persistence.store import PersistenceStore

    store = PersistenceStore(sqlite_path, jsonl_dir)
    await store.initialize()
    return store
