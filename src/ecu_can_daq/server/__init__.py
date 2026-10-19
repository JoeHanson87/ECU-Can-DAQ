"""Web server package."""
from __future__ import annotations

from ecu_can_daq.server.app import create_app, get_app

__all__ = ["create_app", "get_app"]
