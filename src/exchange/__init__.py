"""Exchange layer — the only package that talks to a trading venue.

``base`` defines the ``BaseExchange`` port, ``ccxt`` implements it,
``factory`` builds adapters from ``config/exchanges.yaml``, ``account_type``
maps ccxt account options, ``orderbook_cache`` streams WS market data, and
``mock`` is the test double.

This layer may import ``market`` (an adapter's job is to turn raw venue data
into domain objects). The reverse is forbidden — see
``docs/developer-guide/standards/directory-structure.md`` §3.
"""
