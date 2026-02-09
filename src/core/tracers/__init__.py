"""
Tracers package for Ag3ntum.

Provides execution tracers for different output contexts:
- ExecutionTracer: Rich CLI terminal output with spinners, colors
- QuietTracer: Minimal output (errors and completion only)
- BackendConsoleTracer: Non-interactive timestamped logging
- EventingTracer: Emits structured events for SSE/Web streaming
- NullTracer: No-op (completely disabled tracing)

All classes are re-exported here for backward compatibility.
"""
from .base import SpinnerState, TracerBase
from .backend import BackendConsoleTracer
from .cli import Color, ExecutionTracer, Symbol
from .eventing import EventingTracer
from .null import NullTracer
from .quiet import QuietTracer

__all__ = [
    # Base
    "TracerBase",
    "SpinnerState",
    # Implementations
    "ExecutionTracer",
    "QuietTracer",
    "BackendConsoleTracer",
    "EventingTracer",
    "NullTracer",
    # Backward compatibility aliases
    "Color",
    "Symbol",
]
