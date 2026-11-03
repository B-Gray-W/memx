# memx for Hermes

A [Hermes](https://github.com/NousResearch/hermes-agent) external memory provider.
The index is injected into every session; topic bodies are recalled per turn; the
agent gets tools to search and write.

## Install

```bash
pip install memx-agent-memory
```

Copy the provider into your Hermes profile:

```bash
mkdir -p ~/.hermes/plugins/memx
cp integrations/hermes/memx_provider/__init__.py ~/.hermes/plugins/memx/
cat > ~/.hermes/plugins/memx/plugin.yaml <<'YAML'
name: memx
version: 0.2.0
description: "memx — index-first file memory (BM25 recall)"
YAML
```

Enable it and restart the gateway:

```bash
hermes config set memory.provider memx
```

Roll back at any time — the built-in memory keeps working throughout, and the `.md`
files are untouched:

```bash
hermes memory off
```

## Sharing memory across profiles

Hermes runs each profile (the main agent plus its siblings) as a separate profile
directory with its own `MEMORY.md` and `USER.md`. To give several profiles one shared
memory, point them at the same store:

```bash
export MEMX_STORE="$HOME/.hermes/memx"     # same path for every profile
```

Writes are serialised with an `flock` and every file is written atomically, so
concurrent agents cannot lose each other's writes.

## What the agent sees

`system_prompt_block()` injects the index and the write-floor rules:

```
# memx shared long-term memory (index)
This index is shared by every bot in the fleet. Use memx_search to load a topic's full
body when a hook matches the current task. When storing something new, follow the
write floor: ...

# Memory Index
- [Proxy setup](topics/proxy-setup.md) — when the proxy port is needed [proxy, port] (reference, 2026-11-03)
```

`prefetch(query)` runs per turn and returns the bodies of the best-matching topics,
capped by a character budget. Nothing else is injected.

## Tools

| Tool | Purpose |
|---|---|
| `memx_search` | Return the bodies of the best-matching topics. |
| `memx_add` | Store a topic. `hook` is required — it is what the index shows. |
| `memx_update` | Update the body, hook, or keywords of a topic. |
| `memx_archive` | Move a topic into `archive/` (reversible). Prefer this over deleting. |
| `memx_restore` | Bring an archived topic back into the index. |
| `memx_remove` | Delete permanently. Requires `confirm=true`. |

## Notes

- The provider runs in Hermes' plugin host process. Editing the provider file requires
  a gateway restart to take effect — the host keeps running the old code until then.
- `is_available()` performs no network calls and needs no credentials.
- `MEMX_SRC` overrides how the `memx` package is located; normally a plain `pip
  install` is enough.
