"""Legacy entry point — the implementation lives in ``src/legacy/main.py``.

Kept as a shim so the long-documented ``python -m src.main --mode ...``
invocation keeps working after the legacy tree moved into ``src/legacy/``.
"""

from src.legacy.main import main

if __name__ == "__main__":
    main()
