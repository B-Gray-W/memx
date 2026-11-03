"""memx — Hermes external memory provider (index-first, file-based).

Install as a directory provider:  $HERMES_HOME/plugins/memx/
Enable with:                     hermes config set memory.provider memx
Store:                           $MEMX_STORE, or <hermes_home>/memx/

Design:
  - system_prompt_block(): injects only the index (bounded) plus the write-floor
    rules, so the agent knows when a memory is worth storing.
  - prefetch(query): BM25 over topics, returns bodies of the best hits (budgeted).
  - tools: memx_search / memx_add / memx_update / memx_archive / memx_restore /
    memx_remove. remove requires confirm=true (owner consent).
  - Built-in MEMORY.md/USER.md keep working; memx is additive.

Env:
  MEMX_SRC    — path to the installed memx package (default: import normally)
  MEMX_STORE  — path to the store (default <hermes_home>/memx)

Pointing several profiles at one MEMX_STORE makes the memory fleet-wide; the
store serialises writes with an flock, so concurrent bots are safe.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

_SRC = os.environ.get("MEMX_SRC")
if _SRC and _SRC not in sys.path:
    sys.path.insert(0, _SRC)

try:
    from memx.core import Store, WRITE_FLOOR_RULES  # type: ignore
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "memx is not importable. Install it (pip install memx-agent-memory) "
        "or set MEMX_SRC to the package directory."
    ) from exc

try:  # Hermes ABC; fall back to object so the module still imports elsewhere
    from agent.memory_provider import MemoryProvider as _Base
except Exception:  # pragma: no cover
    _Base = object

PREFETCH_BUDGET = 4000  # chars; the host allows far more before spilling
PREFETCH_K = 3


def default_store_path(hermes_home: str = "") -> Path:
    """The store directory. Shared when several profiles point at the same path."""
    env = os.environ.get("MEMX_STORE")
    if env:
        return Path(env).expanduser()
    home = Path(hermes_home).expanduser() if hermes_home else Path("~/.hermes").expanduser()
    return home / "memx"


class MemxMemoryProvider(_Base):
    """Implements the Hermes MemoryProvider contract."""

    def __init__(self) -> None:
        self._store: Store | None = None
        self._session_id = ""

    # ---- identity / availability ----
    @property
    def name(self) -> str:
        return "memx"

    def is_available(self) -> bool:
        # no network, no keys: just check the core module is importable
        return Path(_SRC, "memx", "core.py").is_file()

    def unavailable_reason(self) -> str:
        return f"memx core not found at {_SRC} (set MEMX_SRC)"

    # ---- lifecycle ----
    def initialize(self, session_id: str, **kwargs: Any) -> None:
        # Point several profiles at one MEMX_STORE to share memory across them;
        # the store serialises writes with an flock, so concurrent bots are safe.
        self._store = Store(default_store_path(kwargs.get("hermes_home", "")))
        self._session_id = session_id

    def system_prompt_block(self) -> str:
        if self._store is None:
            return ""
        ctx = self._store.context().strip()
        if not ctx:
            return ""
        return (
            "# memx shared long-term memory (index)\n"
            "This index is shared by every bot in the fleet. Use memx_search to load "
            "a topic's full body when a hook matches the current task.\n"
            "When storing something new, follow the write floor: " + WRITE_FLOOR_RULES + "\n\n"
            + ctx
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if self._store is None or not query.strip():
            return ""
        return self._store.recall(query, k=PREFETCH_K, budget=PREFETCH_BUDGET)

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        return None  # synchronous and cheap; nothing to warm

    def sync_turn(self, user: str, assistant: str, *, session_id: str = "", messages=None) -> None:
        return None  # no automatic writes: the agent decides what to store

    def on_memory_write(self, action, target, content, metadata=None) -> None:
        return None  # built-in writes are not mirrored; memx stays explicit

    def shutdown(self) -> None:
        return None

    # ---- config (no secrets) ----
    def get_config_schema(self) -> List[Dict[str, Any]]:
        return []

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        return None

    # ---- tools ----
    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "memx_search",
                "description": "Search long-term memory topics; returns bodies of the best matches.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "k": {"type": "integer", "default": 3},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "memx_add",
                "description": (
                    "Store a durable memory topic in the shared store (preference, constraint, "
                    "fact, lesson). Only store it if it changes what a future session does AND "
                    "it cannot be re-derived elsewhere. Provide a one-line 'hook' saying WHEN to "
                    "use it — the hook is what the index shows, so never paste the detail into it."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "body": {"type": "string"},
                        "hook": {"type": "string", "description": "one-line when-to-use summary"},
                        "keywords": {"type": "array", "items": {"type": "string"}},
                        "type": {"type": "string", "enum": ["user", "feedback", "project", "reference", "note"]},
                    },
                    "required": ["title", "body", "hook"],
                },
            },
            {
                "name": "memx_update",
                "description": "Update an existing memory topic by slug.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "slug": {"type": "string"},
                        "body": {"type": "string"},
                        "hook": {"type": "string"},
                        "keywords": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["slug"],
                },
            },
            {
                "name": "memx_archive",
                "description": "Move a topic out of the index into archive/ (reversible). "
                "Use instead of deleting when pruning duplicates.",
                "parameters": {
                    "type": "object",
                    "properties": {"slug": {"type": "string"}},
                    "required": ["slug"],
                },
            },
            {
                "name": "memx_restore",
                "description": "Bring an archived topic back into the index.",
                "parameters": {
                    "type": "object",
                    "properties": {"slug": {"type": "string"}},
                    "required": ["slug"],
                },
            },
            {
                "name": "memx_remove",
                "description": "Delete a memory topic permanently. Only after the owner has agreed; "
                "requires confirm=true. Prefer memx_archive.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "slug": {"type": "string"},
                        "confirm": {"type": "boolean"},
                    },
                    "required": ["slug", "confirm"],
                },
            },
        ]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs: Any) -> str:
        if self._store is None:
            return json.dumps({"error": "memx not initialized"}, ensure_ascii=False)
        try:
            if tool_name == "memx_search":
                hits = self._store.search(args.get("query", ""), k=int(args.get("k", 3)))
                return json.dumps(
                    [{"slug": h.entry.slug, "hook": h.entry.hook, "score": round(h.score, 2),
                      "body": h.entry.body} for h in hits],
                    ensure_ascii=False,
                )
            if tool_name == "memx_add":
                e = self._store.add(
                    args["title"], args["body"], hook=args.get("hook"),
                    type=args.get("type", "note"), keywords=args.get("keywords", []),
                )
                return json.dumps({"added": e.slug}, ensure_ascii=False)
            if tool_name == "memx_update":
                changes = {k: args[k] for k in ("body", "hook", "keywords") if k in args}
                e = self._store.update(args["slug"], **changes)
                return json.dumps({"updated": e.slug}, ensure_ascii=False)
            if tool_name == "memx_archive":
                dest = self._store.archive(args["slug"])
                return json.dumps({"archived": args["slug"], "path": str(dest)}, ensure_ascii=False)
            if tool_name == "memx_restore":
                e = self._store.restore(args["slug"])
                return json.dumps({"restored": e.slug}, ensure_ascii=False)
            if tool_name == "memx_remove":
                if args.get("confirm") is not True:
                    return json.dumps({"error": "refused: confirm=true required (owner consent)"}, ensure_ascii=False)
                self._store.remove(args["slug"])
                return json.dumps({"removed": args["slug"]}, ensure_ascii=False)
            return json.dumps({"error": f"unknown tool {tool_name}"}, ensure_ascii=False)
        except Exception as exc:  # return error to the model, never crash the loop
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)


def register(ctx) -> None:
    """Hermes plugin entry point."""
    ctx.register_memory_provider(MemxMemoryProvider())
