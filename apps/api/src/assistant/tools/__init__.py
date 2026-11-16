from assistant.tools.builtin import default_tools
from assistant.tools.registry import (
    OutboxSpec,
    Planned,
    Tool,
    ToolContext,
    ToolRegistry,
    ToolRejected,
)


def build_tools(embedder=None, calendar=None) -> list[Tool]:
    """Every tool, wired to its adapters. Imported lazily to keep the registry import light."""
    from assistant.integrations import build_calendar
    from assistant.kb.embeddings import build_embedder
    from assistant.tools.public import public_tools

    return default_tools() + public_tools(
        embedder or build_embedder(), calendar or build_calendar()
    )


__all__ = [
    "OutboxSpec",
    "Planned",
    "Tool",
    "ToolContext",
    "ToolRegistry",
    "ToolRejected",
    "build_tools",
    "default_tools",
]
