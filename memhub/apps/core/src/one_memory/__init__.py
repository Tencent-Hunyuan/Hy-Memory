"""MemHub Core."""

import os

_version = os.environ.get("ONE_MEMORY_VERSION", "").strip()
__version__ = _version if _version else "development"
