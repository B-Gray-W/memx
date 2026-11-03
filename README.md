# memx

[![tests](https://github.com/B-Gray-W/memx/actions/workflows/tests.yml/badge.svg)](https://github.com/B-Gray-W/memx/actions/workflows/tests.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](pyproject.toml)

**Index-first, file-based memory for AI agents.** Plain Markdown files, no database,
no embeddings, no network, standard library only.

A memory store that keeps one always-loaded index and loads topic bodies only when they
are relevant. Agents get a small constant prompt cost instead of a growing wall of facts.

```python
from memx import Store

store = Store("~/notes/memx")
store.add(
    "Proxy setup",
    "The proxy daemon listens on port 8080 and is started by systemd.",
    hook="when the proxy port or daemon is needed",
    keywords=["proxy", "port", "systemd"],
)
print(store.recall("which port does the proxy use?", k=1))
```

## Why

Long-lived agents accumulate facts. Three common approaches all have a cost:

- **One growing MEMORY.md** — every fact is in every prompt, forever, and the file hits a
  hard character limit that silently refuses new writes.
- **A vector database** — needs a server, an embedding model, and network access; the
  recall is approximate and the data stops being readable.
- **Per-agent stores** — each agent re-learns the same facts, and they drift apart.

memx takes a third path: an **index of one-line hooks** stays in the prompt, and the
**body of a topic** is fetched only when a hook matches the task. The index is the only
thing that costs tokens every turn.

## Install

```bash
pip install memx-agent-memory
```

Python 3.9+. No runtime dependencies.

## Store layout

```
<root>/
  MEMORY.md            index — one line per topic, always loaded
  topics/<slug>.md     one memory per file (frontmatter + body)
  archive/              compacted topics, restorable
  .memx.lock            lock file guarding writes
```

Every file is plain Markdown with YAML-ish frontmatter, so the store is greppable,
diffable, and reviewable in a pull request.

```markdown
---
title: Proxy setup
hook: when the proxy port or daemon is needed
type: reference
keywords: proxy, port, systemd
updated: 2026-11-03
---
The proxy daemon listens on port 8080 and is started by systemd.
```

## The index is a pointer, never the detail

The index line carries a **hook** — one sentence saying *when* this topic matters.
The body stays in its file. This matters: the index is injected into every prompt,
so a hook that repeats the body would ship the detail to every session. `memx`
enforces the separation and `lint` reports leaks.

```console
$ memx lint
{
  "missing_frontmatter": [],
  "hook_leaks_body": [],
  "dead_index_link": []
}
```

## The write floor

A memory earns its place only if **both** hold:

1. it changes what a future session does — a preference, a constraint, a correction,
   a decision with a *why* — not a log of what happened;
2. it cannot be re-derived from the repository, the docs, or one obvious command.

Recording everything performs far worse than recording a little and pruning. `memx`
checks the mechanical parts (too short, duplicate body, missing hook, hook that
leaks the body) and refuses writes that fail them; the judgment call stays with you
or with the agent using it.

## Concurrency safety

Several agents may share one store. Every write runs under an exclusive `flock`, and
every file is written atomically (temp file → `fsync` → `os.replace`), so a reader
never sees a half-written file.

This is not theoretical: with three processes rebuilding the index at once, an
unlocked implementation loses `MEMORY.md` entirely — the temporary files clobber
each other. `memx` serialises them.

## CLI

```console
$ memx context                       # the index, as a session would see it
$ memx recall "which port" -k 3      # bodies of the best-matching topics
$ memx search "proxy"                # list matching topics with scores
$ memx add "Title" --body "..." --hook "when to use" --keywords a,b
$ memx update <slug> --hook "new summary"
$ memx archive <slug>                # move out of the index (reversible)
$ memx restore <slug>                # bring it back
$ memx lint                          # structural health check
$ memx compact                       # preview duplicate/oversize findings
$ memx compact --apply --yes         # apply — archives, never deletes
$ memx remove <slug> --yes           # permanent, requires explicit consent
```

`MEMX_ROOT` sets the store directory.

## Integrations

- **[Hermes](integrations/hermes/)** — an external memory provider plugin. The index is
  injected into every session, bodies are recalled per turn, and the agent gets
  `memx_search` / `memx_add` / `memx_update` / `memx_archive` / `memx_restore` /
  `memx_remove` tools. Point several Hermes profiles at one `MEMX_STORE` to share
  memory across them.

## Design notes

The index-and-bodies model, the 200-line / 25KB index ceiling, the write floor, and
the "archive, don't delete" pruning rule were informed by reading these projects:

- **Claude Code auto-memory** — `MEMORY.md` as a resident index with topic files read
  on demand; a 200-line / 25KB ceiling on the index.
- **diegoos/agent-memory** (MIT) — the write-floor table, type layering, temp-file +
  rename atomic writes, lock discipline.
- **Letta MemFS** (Apache-2.0) — memory as a tree of Markdown files, root files
  resident, subdirectories entered on demand.
- **angad-kandhari/deliberate** — "index plus topics", "write selectively", "prune with
  care".

No code was copied from any of them.

## Limitations

- Retrieval is lexical (BM25), not semantic. Synonyms and cross-language phrasing
  recall worse than an embedding index would; write hooks and keywords accordingly.
- `compact` finds duplicates and oversize bodies. True summarisation needs a
  `summarize` callback — no model is bundled.
- The index ceiling is 200 lines or 25KB. Run `compact` as it fills.

## License

MIT — see [LICENSE](LICENSE).
