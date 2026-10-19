"""A2L package: legacy parser + pya2l facade"""
from __future__ import annotations

# Legacy parser for backward compatibility (tests use it)
from ecu_can_daq.legacy_a2l import (
    A2LParseError,
    load_measurements,
    resolve_measurements,
)

# Try to import new facade
try:
    from ecu_can_daq.a2l.facade import (  # noqa: F401
        A2LProject,
        load_a2l_project,
    )
    HAS_FACADE = True
except Exception:  # pragma: no cover
    HAS_FACADE = False

__all__ = [
    "A2LParseError",
    "load_measurements",
    "resolve_measurements",
    "HAS_FACADE",
]
if HAS_FACADE:
    __all__.extend(["A2LProject", "load_a2l_project"])
