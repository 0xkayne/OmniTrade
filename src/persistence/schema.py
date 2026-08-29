# SQLite table definitions as constants.
# Subagent B uses these to CREATE TABLE; CLI query commands use them for column names.

INTENTS_TABLE = """
CREATE TABLE IF NOT EXISTS intents (
    intent_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'PENDING',
    raw_intent_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

LEGS_TABLE = """
CREATE TABLE IF NOT EXISTS legs (
    leg_id TEXT PRIMARY KEY,
    intent_id TEXT NOT NULL REFERENCES intents(intent_id),
    venue TEXT NOT NULL,
    instrument_venue_symbol TEXT NOT NULL,
    instrument_base TEXT NOT NULL,
    instrument_quote TEXT NOT NULL,
    instrument_market_type TEXT NOT NULL,
    quote_preference_matched TEXT,
    planned_notional_usd REAL NOT NULL,
    planned_qty_base REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING_SEND',
    sent_at TEXT,
    order_id TEXT,
    filled_amount REAL,
    avg_price REAL,
    fee_usd REAL,
    error_msg TEXT,
    compensation_order_id TEXT,
    compensation_filled_amount REAL,
    compensation_avg_price REAL,
    compensation_fee_usd REAL,
    instrument_selection_log TEXT,
    funding_rate_at_plan REAL,
    next_funding_time_at_plan REAL,
    leverage INTEGER NOT NULL DEFAULT 1,
    filled_at TEXT,
    compensated_at TEXT
)
"""

AUDIT_TABLE = """
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    intent_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL
)
"""

INSTRUMENTS_TABLE = """
CREATE TABLE IF NOT EXISTS instruments (
    venue           TEXT NOT NULL,
    network         TEXT NOT NULL,
    market_type     TEXT NOT NULL,
    base            TEXT NOT NULL,
    quote           TEXT NOT NULL,
    venue_symbol    TEXT NOT NULL,
    min_qty         REAL NOT NULL DEFAULT 0.0,
    qty_step        REAL NOT NULL DEFAULT 0.0,
    price_step      REAL NOT NULL DEFAULT 0.0,
    min_notional    REAL NOT NULL DEFAULT 0.0,
    taker_fee_rate  REAL NOT NULL DEFAULT 0.0,
    maker_fee_rate  REAL NOT NULL DEFAULT 0.0,
    contract_size   REAL NOT NULL DEFAULT 1.0,
    is_inverse      INTEGER NOT NULL DEFAULT 0,
    listing_status  TEXT NOT NULL DEFAULT 'trading',
    cached_at       TEXT NOT NULL,
    PRIMARY KEY (venue, network, market_type, base, quote)
)
"""

FUNDING_RATE_SNAPSHOTS_TABLE = """
    CREATE TABLE IF NOT EXISTS funding_rate_snapshots (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        venue           TEXT NOT NULL,
        symbol          TEXT NOT NULL,
        funding_rate    REAL,
        mark_price      REAL,
        next_funding_time REAL,
        fetched_at      TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(venue, symbol, fetched_at)
    )
    """


HEDGED_POSITIONS_TABLE = """
    CREATE TABLE IF NOT EXISTS hedged_positions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        position_id     TEXT UNIQUE NOT NULL,
        base            TEXT NOT NULL,
        venue_long      TEXT NOT NULL,
        venue_short     TEXT NOT NULL,
        notional_usd    REAL NOT NULL,
        intent_open     TEXT NOT NULL,
        leg_long_id     TEXT NOT NULL,
        leg_short_id    TEXT NOT NULL,
        rate_at_open_a  REAL,
        rate_at_open_b  REAL,
        opened_at       TEXT NOT NULL DEFAULT (datetime('now')),
        intent_close    TEXT,
        closed_at       TEXT,
        status          TEXT NOT NULL DEFAULT 'OPEN'
    )
    """


WATCH_CANDLES_TABLE = """
CREATE TABLE IF NOT EXISTS watch_candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset TEXT NOT NULL,
    venue TEXT NOT NULL,
    interval TEXT NOT NULL,
    ts TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    UNIQUE(asset, venue, interval, ts)
)
"""

WATCH_CANDLES_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_watch_candles_asset_venue_interval_ts ON watch_candles(asset, venue, interval, ts);",
]

DERIVED_CANDLES_TABLE = """
CREATE TABLE IF NOT EXISTS derived_candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset TEXT NOT NULL,
    venue TEXT NOT NULL,
    interval TEXT NOT NULL,
    ts TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    UNIQUE(asset, venue, interval, ts)
)
"""

DERIVED_CANDLES_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_derived_candles_asset_venue_interval_ts ON derived_candles(asset, venue, interval, ts);",
]

TRADES_TABLE = """
CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    venue TEXT,
    symbol TEXT NOT NULL,
    tag TEXT,
    side TEXT NOT NULL,
    qty REAL NOT NULL,
    price REAL NOT NULL,
    notional_usd REAL NOT NULL,
    fee_usd REAL,
    pnl_usd REAL,
    strategy TEXT,
    reason TEXT,
    note TEXT,
    matched_buy_id TEXT,
    created_at TEXT NOT NULL
)
"""

TRADES_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(ts);",
    "CREATE INDEX IF NOT EXISTS idx_trades_tag ON trades(tag);",
    "CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);",
]

TELEGRAM_SUBSCRIBERS_TABLE = """
CREATE TABLE IF NOT EXISTS telegram_subscribers (
    chat_id TEXT PRIMARY KEY,
    added_at TEXT NOT NULL
)
"""


LEGS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_legs_venue_status ON legs(venue, status);",
    "CREATE INDEX IF NOT EXISTS idx_legs_status ON legs(status);",
    "CREATE INDEX IF NOT EXISTS idx_legs_intent_id ON legs(intent_id);",
    "CREATE INDEX IF NOT EXISTS idx_legs_filled_at ON legs(filled_at);",
    "CREATE INDEX IF NOT EXISTS idx_legs_compensated_at ON legs(compensated_at);",
]

INTENTS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_intents_updated_at ON intents(updated_at);",
    "CREATE INDEX IF NOT EXISTS idx_intents_status ON intents(status);",
]

INSTRUMENTS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_instruments_lookup ON instruments(base, venue, network, market_type);",
    "CREATE INDEX IF NOT EXISTS idx_instruments_venue_type ON instruments(venue, network, market_type);",
    "CREATE INDEX IF NOT EXISTS idx_instruments_cached_at ON instruments(cached_at);",
]
