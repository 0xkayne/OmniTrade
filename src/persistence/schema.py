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
    instrument_selection_log TEXT,
    funding_rate_at_plan REAL,
    next_funding_time_at_plan REAL
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
