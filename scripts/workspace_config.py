"""Select private data without copying or migrating an existing workspace."""
import os
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[1]


def select_root(explicit=None):
    """CLI wins over environment; the existing local layout is compatible."""
    return Path(explicit or os.environ.get("APPLYD_WORKSPACE") or CODE_ROOT).expanduser().resolve()
