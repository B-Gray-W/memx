# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Nothing yet.

## [0.2.0] — 2026-11-03

First public release.

### Added

- `Store` with an always-injected index (`MEMORY.md`) and one file per topic
  (`topics/<slug>.md`).
- BM25 retrieval over title, keywords, hook and body, with CJK character-bigram
  tokenisation so Chinese text is searchable without a segmenter.
- `recall(query, k, budget)` returning the best-matching topic bodies within a
  character budget.
- Write floor: a mechanical check that refuses bodies that are too short, duplicate
  an existing body, or arrive without a hook; warns when a hook repeats body text or
  keywords are missing.
- `lint` reporting missing frontmatter, empty bodies, missing hooks, hooks that leak
  the body, orphan topics, dead index links, and oversize bodies.
- `compact` that finds duplicate and oversize topics, **archiving** duplicates rather
  than deleting them, plus `archive` / `restore` / `archived` to manage that shelf.
- Concurrency safety: an exclusive `flock` around every write and atomic file
  replacement (temp file → `fsync` → `os.replace`) for every file.
- CLI (`memx`): `context`, `recall`, `search`, `add`, `update`, `archive`, `restore`,
  `archived`, `lint`, `compact`, `remove`, `list`.
- Optional Hermes integration: an external memory provider exposing
  `memx_search` / `memx_add` / `memx_update` / `memx_archive` / `memx_restore` /
  `memx_remove`, with the index injected into the session prompt.
- 27 unit tests, including a three-process concurrent-write test.
- GitHub Actions running the suite on Python 3.9–3.13 plus an install-and-CLI smoke
  test.

### Design notes

- The index is a **pointer**: it carries a one-line hook, never the body. A hook that
  repeats the body would ship that detail to every session, so it is reported by
  `lint` and warned about on write.
- Destructive operations require explicit consent (`--yes` / `confirm=true`).
  Compaction archives; only `remove` deletes.
- No runtime dependencies. Retrieval is lexical by design — no embedding model, no
  server, no network.

[Unreleased]: https://github.com/B-Gray-W/memx/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/B-Gray-W/memx/releases/tag/v0.2.0
