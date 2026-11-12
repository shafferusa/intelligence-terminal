"""Logging: one `finsim` logger. Level from FINSIM_LOG_LEVEL (default INFO); a rotating file when FINSIM_LOG names a path."""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Optional

_configured: dict = {}


def get_logger(name: str = "finsim", path: Optional[str] = None, level: Optional[str] = None) -> logging.Logger:
    root = logging.getLogger("finsim")
    key = (path or os.environ.get("FINSIM_LOG") or "", (level or os.environ.get("FINSIM_LOG_LEVEL") or "WARNING").upper())
    if _configured.get("key") != key:
        for h in list(root.handlers):
            root.removeHandler(h)
            try:
                h.close()
            except Exception:   # pragma: no cover
                pass
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
        if key[0]:
            os.makedirs(os.path.dirname(os.path.abspath(key[0])), exist_ok=True)
            fh = RotatingFileHandler(key[0], maxBytes=5_000_000, backupCount=3)
            fh.setFormatter(fmt)
            root.addHandler(fh)
        root.setLevel(getattr(logging, key[1], logging.INFO))
        root.propagate = False
        _configured["key"] = key
    return root if name == "finsim" else logging.getLogger(name if name.startswith("finsim.") else f"finsim.{name}")
