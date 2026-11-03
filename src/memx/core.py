"""memx core: index-first, file-based agent memory (shareable across profiles).

Layout (all plain Markdown, git-friendly):

    <root>/
      MEMORY.md            always-loaded index, one line per topic (hook + keywords)
      topics/<slug>.md     one memory per file, YAML-like frontmatter + body
      archive/<slug>.<ts>.md   compacted/pruned topics, kept for restore (never deleted)
      .memx.lock           flock file guarding writes

Flow:
  1. Each session injects MEMORY.md (bounded: MAX_INDEX_LINES / MAX_INDEX_BYTES).
  2. The agent sees hooks; when a hook looks relevant it calls `recall(query)` which
     scores topics with BM25 over title+keywords+hook+body and returns the bodies
     of the top-k hits (bounded by a character budget).
  3. Writes go through `add`/`update`; MEMORY.md is regenerated, never hand-edited.
  4. `compact()` finds duplicates and oversize bodies. It is a dry run unless
     apply=True. Pruned topics are MOVED TO archive/, never deleted; the owner can
     restore them. Permanent deletion requires an explicit, separate call.

Safety properties:
  - every write happens under an exclusive flock (cross-process safe)
  - every file write is atomic (temp file + fsync + os.replace)
  - removal is opt-in; compaction archives instead of deleting

Stdlib only. Python 3.9+.
"""
from __future__ import annotations

import errno
import hashlib
import math
import os
import re
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

try:  # POSIX advisory locking; Windows falls back to a no-op lock.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore

MAX_INDEX_LINES = 200
MAX_INDEX_BYTES = 25 * 1024
HOOK_MAX = 120
LOCK_TIMEOUT = 15.0
SLUG_RE = re.compile(r"^[a-z0-9一-鿿][a-z0-9_一-鿿-]{0,63}$")
_WORD_RE = re.compile(r"[a-z0-9_]+")
_CJK_RE = re.compile(r"[一-鿿]+")

INDEX_HEADER = (
    "# Memory Index\n"
    "<!-- memx: generated file, do not hand-edit. Use `memx add/update/remove`. -->\n"
)

# ---------------------------------------------------------------------------
# Write floor: a memory earns its place only if BOTH hold.
#   1. it changes what a future session does (preference, constraint,
#      correction, decision with a why) — not a narration of what happened;
#   2. it cannot be re-derived from the repo, docs, or one obvious command.
# Recording everything performs far worse than recording little and pruning.
# ---------------------------------------------------------------------------
WRITE_FLOOR_RULES = (
    "Write only if BOTH hold: (1) it changes what a future session does "
    "(preference, constraint, correction, decision with a why) — not a log of what happened; "
    "(2) it cannot be re-derived from the repo, docs, or one obvious command. "
    "Never record what the repo or git history already shows. One topic per file."
)


def tokenize(text: str) -> List[str]:
    """Lowercase ASCII words, plus CJK character bigrams (no word segmenter needed)."""
    text = text.lower()
    tokens = _WORD_RE.findall(text)
    for run in _CJK_RE.findall(text):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    return tokens


def atomic_write(path: Path, text: str) -> None:
    """Write text to path atomically: temp file in the same dir, fsync, replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".memx-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        try:
            dfd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@dataclass
class Entry:
    slug: str
    title: str
    hook: str
    body: str
    type: str = "note"
    keywords: List[str] = field(default_factory=list)
    updated: str = ""

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.body.strip().encode("utf-8")).hexdigest()[:16]

    def index_line(self) -> str:
        kw = f" [{', '.join(self.keywords)}]" if self.keywords else ""
        return f"- [{self.title}](topics/{self.slug}.md) — {self.hook}{kw} ({self.type}, {self.updated})"

    def to_file(self) -> str:
        front = [
            "---",
            f"title: {self.title}",
            f"hook: {self.hook}",
            f"type: {self.type}",
            f"keywords: {', '.join(self.keywords)}",
            f"updated: {self.updated}",
            "---",
            "",
        ]
        return "\n".join(front) + self.body.strip() + "\n"

    @staticmethod
    def from_file(slug: str, text: str) -> "Entry":
        meta: Dict[str, str] = {}
        body = text
        if text.startswith("---\n"):
            end = text.find("\n---", 4)
            if end != -1:
                for line in text[4:end].splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        meta[k.strip()] = v.strip()
                body = text[end + 4:].lstrip("\n")
        kws = [k.strip() for k in meta.get("keywords", "").split(",") if k.strip()]
        return Entry(
            slug=slug,
            title=meta.get("title", slug),
            hook=meta.get("hook", ""),
            body=body.rstrip() + "\n",
            type=meta.get("type", "note"),
            keywords=kws,
            updated=meta.get("updated", ""),
        )


@dataclass
class Hit:
    entry: Entry
    score: float


class Store:
    def __init__(self, root: Path | str):
        self.root = Path(root).expanduser()
        self.topics_dir = self.root / "topics"
        self.index_path = self.root / "MEMORY.md"
        self.archive_dir = self.root / "archive"
        self.lock_path = self.root / ".memx.lock"

    # ---------- locking ----------
    def _lock(self, timeout: float = LOCK_TIMEOUT):
        """Exclusive cross-process lock; a second writer waits instead of racing."""
        if fcntl is None:  # pragma: no cover
            class _NoLock:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            return _NoLock()

        self.root.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(self.lock_path, flags, 0o600)
        deadline = time.time() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    os.close(fd)
                    raise
                if time.time() >= deadline:
                    os.close(fd)
                    raise TimeoutError(f"memx store is locked by another writer: {self.lock_path}")
                time.sleep(0.05)
        try:
            os.fchmod(fd, 0o600)
        except OSError:
            pass

        class _Lock:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)
                return False

        return _Lock()

    # ---------- read ----------
    def entries(self) -> List[Entry]:
        if not self.topics_dir.is_dir():
            return []
        out = []
        for p in sorted(self.topics_dir.glob("*.md")):
            out.append(Entry.from_file(p.stem, p.read_text(encoding="utf-8")))
        return out

    def get(self, slug: str) -> Optional[Entry]:
        p = self.topics_dir / f"{slug}.md"
        if not p.is_file():
            return None
        return Entry.from_file(slug, p.read_text(encoding="utf-8"))

    def context(self) -> str:
        """Text to inject into every session. Bounded like Claude Code's MEMORY.md."""
        if not self.index_path.is_file():
            return ""
        lines = self.index_path.read_text(encoding="utf-8").splitlines()
        notes: List[str] = []
        if len(lines) > MAX_INDEX_LINES:
            lines = lines[:MAX_INDEX_LINES]
            notes.append(f"[memx] index truncated to {MAX_INDEX_LINES} lines; run `memx compact`.")
        text = "\n".join(lines) + "\n"
        if len(text.encode("utf-8")) > MAX_INDEX_BYTES:
            text = text.encode("utf-8")[:MAX_INDEX_BYTES].decode("utf-8", "ignore")
            notes.append(f"[memx] index truncated to {MAX_INDEX_BYTES} bytes; run `memx compact`.")
        return text + ("\n".join(notes) + "\n" if notes else "")

    def search(self, query: str, k: int = 5) -> List[Hit]:
        entries = self.entries()
        if not entries:
            return []
        docs = []
        for e in entries:
            # title, keywords and hook weigh more than body, by repetition
            fields = " ".join([e.title, e.title, " ".join(e.keywords)] * 2 + [e.hook, e.body])
            docs.append(Counter(tokenize(fields)))
        q_terms = set(tokenize(query))
        if not q_terms:
            return []
        n = len(docs)
        avg = sum(sum(d.values()) for d in docs) / n or 1.0
        df = Counter()
        for d in docs:
            for t in q_terms:
                if t in d:
                    df[t] += 1
        k1, b = 1.5, 0.75
        hits: List[Hit] = []
        for e, d in zip(entries, docs):
            dl = sum(d.values()) or 1
            score = 0.0
            for t in q_terms:
                if t not in d:
                    continue
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                tf = d[t]
                score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / avg))
            if score > 0:
                hits.append(Hit(e, score))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]

    def recall(self, query: str, k: int = 3, budget: int = 6000) -> str:
        """Bodies of the best topics for `query`, capped at `budget` characters."""
        parts: List[str] = []
        used = 0
        for h in self.search(query, k=k):
            block = f"## {h.entry.title} ({h.entry.slug}, score {h.score:.2f})\n{h.entry.body}"
            if used + len(block) > budget:
                remain = budget - used
                if remain < 200:
                    break
                block = block[:remain] + "\n...[truncated]"
            parts.append(block)
            used += len(block)
        return "\n\n".join(parts)

    # ---------- write ----------
    def write_floor_check(
        self,
        title: str,
        body: str,
        hook: Optional[str] = None,
        keywords: Iterable[str] = (),
    ) -> Tuple[bool, str, List[str]]:
        """Hard checks for a new memory. Returns (ok, reason, warnings)."""
        warnings: List[str] = []
        text = (body or "").strip()
        if len(text) < 10:
            return False, "body too short to be a useful memory", warnings
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        for e in self.entries():
            if e.digest == digest:
                return False, f"identical to existing topic '{e.slug}'", warnings
        if hook:
            h = hook.strip()
            if len(h) > HOOK_MAX:
                warnings.append(f"hook is {len(h)} chars; it will be truncated to {HOOK_MAX}")
            # a hook that copies a long verbatim chunk of the body leaks detail into the index
            if len(h) >= 30 and h in text:
                warnings.append("hook repeats body text verbatim — the index would carry the detail; shorten it")
            if h.lower() == title.strip().lower():
                warnings.append("hook duplicates the title; add a 'when to use' cue")
        if not keywords:
            warnings.append("no keywords: recall relies on title+hook+body words only")
        return True, "", warnings

    def add(
        self,
        title: str,
        body: str,
        *,
        slug: Optional[str] = None,
        hook: Optional[str] = None,
        type: str = "note",
        keywords: Iterable[str] = (),
        summarize: Optional[Callable[[str], str]] = None,
        overwrite: bool = False,
        enforce_floor: bool = True,
    ) -> Entry:
        slug = slug or slugify(title)
        if not SLUG_RE.match(slug):
            raise ValueError(f"bad slug: {slug!r}")
        with self._lock():
            path = self.topics_dir / f"{slug}.md"
            if path.exists() and not overwrite:
                raise FileExistsError(f"{slug} exists; use update() or overwrite=True")
            if enforce_floor:
                ok, reason, _warns = self.write_floor_check(title, body, hook, keywords)
                if not ok:
                    raise ValueError(f"write floor rejected: {reason}")
            if not hook and enforce_floor:
                raise ValueError("write floor rejected: an explicit hook is required "
                                 "(the index must point, not carry the detail)")
            e = Entry(
                slug=slug,
                title=title.strip(),
                hook=(hook or make_hook(body, summarize)),
                body=body,
                type=type,
                keywords=[k.strip().lower() for k in keywords if k.strip()],
                updated=date.today().isoformat(),
            )
            self._write_entry(e)
            self.rebuild_index()
            return e

    def update(self, slug: str, **changes) -> Entry:
        with self._lock():
            e = self.get(slug)
            if e is None:
                raise KeyError(slug)
            new_slug = changes.pop("new_slug", None)
            for k, v in changes.items():
                if k == "keywords":
                    v = [x.strip().lower() for x in v if x.strip()]
                if not hasattr(e, k):
                    raise ValueError(f"unknown field {k}")
                setattr(e, k, v)
            e.updated = date.today().isoformat()
            if new_slug and new_slug != e.slug:
                if not SLUG_RE.match(new_slug):
                    raise ValueError(f"bad slug: {new_slug!r}")
                old = self.topics_dir / f"{e.slug}.md"
                e.slug = new_slug
                if old.exists():
                    old.unlink()
            self._write_entry(e)
            self.rebuild_index()
            return e

    def remove(self, slug: str) -> None:
        """Delete one topic permanently. Call only after the owner has agreed."""
        with self._lock():
            p = self.topics_dir / f"{slug}.md"
            if not p.is_file():
                raise KeyError(slug)
            p.unlink()
            self.rebuild_index()

    def archive(self, slug: str) -> Path:
        """Move a topic to archive/ (reversible). Used by compact instead of deleting."""
        with self._lock():
            p = self.topics_dir / f"{slug}.md"
            if not p.is_file():
                raise KeyError(slug)
            self.archive_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            dest = self.archive_dir / f"{slug}.{stamp}.md"
            os.replace(p, dest)
            self.rebuild_index()
            return dest

    def restore(self, slug: str) -> Entry:
        """Bring an archived topic back."""
        with self._lock():
            cands = sorted(self.archive_dir.glob(f"{slug}.*.md")) if self.archive_dir.is_dir() else []
            if not cands:
                raise KeyError(slug)
            src = cands[-1]
            self.topics_dir.mkdir(parents=True, exist_ok=True)
            os.replace(src, self.topics_dir / f"{slug}.md")
            e = self.get(slug)
            assert e is not None
            self.rebuild_index()
            return e

    def archived(self) -> List[Path]:
        if not self.archive_dir.is_dir():
            return []
        return sorted(self.archive_dir.glob("*.md"))

    def _write_entry(self, e: Entry) -> None:
        self.topics_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(self.topics_dir / f"{e.slug}.md", e.to_file())

    def rebuild_index(self) -> None:
        lines = [INDEX_HEADER]
        for e in sorted(self.entries(), key=lambda x: (x.type, x.slug)):
            lines.append(e.index_line() + "\n")
        atomic_write(self.index_path, "".join(lines))

    # ---------- maintenance ----------
    def compact(
        self,
        max_body: int = 4000,
        summarize: Optional[Callable[[str], str]] = None,
        apply: bool = False,
        archive_duplicates: bool = True,
    ) -> Dict[str, list]:
        """Find duplicates (same body digest) and oversize bodies.

        Dry run by default. With apply=True:
          - duplicates: keep the most recently updated, ARCHIVE the rest (never delete)
          - oversize: rewrite body via `summarize` (if given); the original is
            reported so the owner can decide.
        """
        entries = self.entries()
        report: Dict[str, list] = {"duplicates": [], "oversize": [], "archived": [], "applied": apply}
        by_digest: Dict[str, List[Entry]] = {}
        for e in entries:
            by_digest.setdefault(e.digest, []).append(e)
        for group in by_digest.values():
            if len(group) > 1:
                group.sort(key=lambda x: (x.updated, x.slug), reverse=True)
                keep, drop = group[0], group[1:]
                report["duplicates"].append({"keep": keep.slug, "drop": [d.slug for d in drop]})
                if apply:
                    for d in drop:
                        if archive_duplicates:
                            dest = self.archive(d.slug)
                            report["archived"].append({"slug": d.slug, "path": str(dest)})
                        else:
                            (self.topics_dir / f"{d.slug}.md").unlink()
        for e in entries:
            if len(e.body) > max_body:
                report["oversize"].append({"slug": e.slug, "chars": len(e.body)})
                if apply and summarize is not None:
                    with self._lock():
                        cur = self.get(e.slug)
                        if cur is not None:
                            cur.body = summarize(cur.body)
                            cur.hook = make_hook(cur.body, None)
                            cur.updated = date.today().isoformat()
                            self._write_entry(cur)
        if apply:
            self.rebuild_index()
        return report

    def lint(self) -> Dict[str, list]:
        """Structural health: dead links, missing frontmatter, index drift, size."""
        problems: Dict[str, list] = {
            "missing_frontmatter": [],
            "empty_body": [],
            "no_hook": [],
            "hook_leaks_body": [],
            "orphan_topic": [],
            "dead_index_link": [],
            "oversize": [],
        }
        entries = self.entries()
        slugs = {e.slug for e in entries}
        for e in entries:
            raw = (self.topics_dir / f"{e.slug}.md").read_text(encoding="utf-8")
            if not raw.startswith("---\n"):
                problems["missing_frontmatter"].append(e.slug)
            if not e.body.strip():
                problems["empty_body"].append(e.slug)
            if not e.hook.strip():
                problems["no_hook"].append(e.slug)
            elif len(e.hook) >= 30 and e.hook in e.body:
                problems["hook_leaks_body"].append(e.slug)
            if len(e.body) > 4000:
                problems["oversize"].append({"slug": e.slug, "chars": len(e.body)})
        if self.index_path.is_file():
            idx = self.index_path.read_text(encoding="utf-8")
            for e in entries:
                if f"topics/{e.slug}.md" not in idx:
                    problems["orphan_topic"].append(e.slug)
            for m in re.finditer(r"\(topics/([^)]+)\.md\)", idx):
                if m.group(1) not in slugs:
                    problems["dead_index_link"].append(m.group(1))
        return problems


def slugify(title: str) -> str:
    s = re.sub(r"[^a-z0-9一-鿿]+", "-", title.lower()).strip("-")
    if not s:
        s = "memo-" + hashlib.sha1(title.encode("utf-8")).hexdigest()[:8]
    return s[:64]


def make_hook(body: str, summarize: Optional[Callable[[str], str]] = None) -> str:
    """One-line hook for the index.

    Index must POINT, not carry detail. Without a summarizer we do NOT copy
    body text (that leaked whole facts into the always-loaded index). Callers
    should pass an explicit hook; the fallback is a neutral placeholder.
    """
    if summarize is not None:
        hook = summarize(body).strip().splitlines()[0]
    else:
        hook = "(no hook: open this topic to read it)"
    hook = hook.replace("\n", " ")
    return hook[:HOOK_MAX]
