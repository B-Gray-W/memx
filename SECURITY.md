# Security

## Data handling

`memx` is a local file store. It has **no network code, no telemetry, and no
analytics**. Everything it reads and writes stays in the directory you point it at.

- No runtime dependencies, so there is no transitive supply-chain surface.
- No credentials, API keys, or tokens are read, requested, or required.
- `is_available()` on the Hermes provider performs no network calls.

## What the store may contain

The store holds whatever you put in it. If you use it for an agent that handles
personal data, that data is on disk in plain Markdown — readable by anything that can
read the directory, and included in any backup of it. Encrypt the volume or keep the
store out of shared locations if that matters.

**When publishing a repository that contains a memx store, treat the store as
private data.** The `examples/` directory in this repository contains only
fictional sample data; real stores belong outside version control. The `.gitignore`
excludes `archive/` and lock files, but a topic file is ordinary Markdown and *will*
be committed if you add it — review what you stage.

## Concurrency

Writes are serialised with an exclusive `flock` on `<store>/.memx.lock` and every
file is written atomically (temp file → `fsync` → `os.replace`). A reader never
observes a partially written file, and two agents writing at once cannot lose each
other's changes.

The lock is advisory and POSIX-only. On a filesystem that does not honour `flock`
(rare network mounts), cross-process safety is not guaranteed — point the store at a
local disk.

## Deletion

`memx remove` is permanent and requires explicit consent (`--yes` on the CLI,
`confirm=true` on the tool). `memx compact` **archives** rather than deletes, and
`memx restore` brings an archived topic back. Nothing is removed implicitly.

## Reporting a vulnerability

Open an issue on the tracker. Please do not include personal data or real store
contents in a report — a minimal reproduction with fictional data is enough.
