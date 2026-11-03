"""memx — index-first, file-based memory for AI agents.

A memory store that keeps an always-loaded index and loads topic bodies only
when they are relevant. Plain Markdown files, no database, no embeddings, no
network, standard library only.

    from memx import Store

    store = Store("~/notes/memx")
    store.add("Proxy setup",
              "The proxy daemon listens on port 8080 and is started by systemd.",
              hook="when the proxy port or daemon is needed",
              keywords=["proxy", "port", "systemd"])
    print(store.recall("which port does the proxy use?", k=1))

The store layout::

    <root>/
      MEMORY.md            index, one line per topic (always loaded)
      topics/<slug>.md     one memory per file
      archive/              compacted topics, restorable
      .memx.lock            lock file guarding writes
"""

from .core import (
    MAX_INDEX_BYTES,
    MAX_INDEX_LINES,
    WRITE_FLOOR_RULES,
    Entry,
    Hit,
    Store,
    atomic_write,
    make_hook,
    slugify,
    tokenize,
)

__version__ = "0.2.0"

__all__ = [
    "Store",
    "Entry",
    "Hit",
    "atomic_write",
    "make_hook",
    "slugify",
    "tokenize",
    "WRITE_FLOOR_RULES",
    "MAX_INDEX_LINES",
    "MAX_INDEX_BYTES",
    "__version__",
]
