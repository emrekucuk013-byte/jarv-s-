"""Tool registry: the one thing that gets extended forever.

Adding a capability means writing one self-contained tool module and
registering it in ``default_registry``. The core loop never changes.
"""

from .registry import Tool, ToolRegistry, ToolResult, default_registry

__all__ = ["Tool", "ToolRegistry", "ToolResult", "default_registry"]
