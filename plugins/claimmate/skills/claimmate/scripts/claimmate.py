#!/usr/bin/env python3
"""Compatibility CLI for the modular ClaimMate core."""

from __future__ import annotations

import sys
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_PLUGIN_SHARED = _THIS_DIR.parents[2] / "scripts"
_SHARED = _PLUGIN_SHARED if (_PLUGIN_SHARED / "claimmate_core").is_dir() else _THIS_DIR
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

from claimmate_core.cli import *  # noqa: F401,F403,E402


if __name__ == "__main__":
    raise SystemExit(main())
