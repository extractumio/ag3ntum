"""
Execution Tracer for Ag3ntum.

This module is a thin re-export layer for backward compatibility.
All tracer implementations have been moved to the tracers/ package.

Import from src.core.tracers for new code, or continue importing
from src.core.tracer for backward compatibility.
"""
# Re-export everything from the tracers package
from .tracers import (  # noqa: F401
    BackendConsoleTracer,
    Color,
    EventingTracer,
    ExecutionTracer,
    NullTracer,
    QuietTracer,
    SpinnerState,
    Symbol,
    TracerBase,
)

__all__ = [
    "TracerBase",
    "SpinnerState",
    "ExecutionTracer",
    "QuietTracer",
    "BackendConsoleTracer",
    "EventingTracer",
    "NullTracer",
    "Color",
    "Symbol",
]
