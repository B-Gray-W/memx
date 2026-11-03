"""memx command line. Usage: python -m memx <command> ...  (run from the project dir)

Commands:
  context                      print the always-loaded index (what a session sees)
  recall "<query>" [-k N]      print bodies of the best-matching topics
  search "<query>"             list matching topics with scores
  add "<title>" --body TEXT | --file PATH --hook H [--keywords a,b] [--type T]
  update <slug> [--body ...] [--hook ...] [--keywords ...] [--type ...] [--title ...] [--new-slug ...]
  remove <slug> --yes          delete one topic permanently (requires --yes)
  archive <slug>               move a topic to archive/ (reversible)
  restore <slug>               bring an archived topic back
  archived                     list archived topics
  lint                         structural health check
  compact [--max-body N] [--apply --yes]   dry run by default; archives, never deletes
  list
Root dir: $MEMX_ROOT or ./memory-store
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .core import Store


def _store(args) -> Store:
    root = args.root or os.environ.get("MEMX_ROOT") or "memory-store"
    return Store(root)


def _kw(s):
    return [x for x in (s or "").split(",") if x.strip()]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="memx")
    p.add_argument("--root", help="store directory (default $MEMX_ROOT or ./memory-store)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("context")
    sub.add_parser("list")

    r = sub.add_parser("recall")
    r.add_argument("query")
    r.add_argument("-k", type=int, default=3)
    r.add_argument("--budget", type=int, default=6000)

    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("-k", type=int, default=5)

    a = sub.add_parser("add")
    a.add_argument("title")
    g = a.add_mutually_exclusive_group(required=True)
    g.add_argument("--body")
    g.add_argument("--file")
    a.add_argument("--slug")
    a.add_argument("--hook")
    a.add_argument("--keywords", default="")
    a.add_argument("--type", default="note")
    a.add_argument("--overwrite", action="store_true")

    u = sub.add_parser("update")
    u.add_argument("slug")
    u.add_argument("--title")
    u.add_argument("--body")
    u.add_argument("--hook")
    u.add_argument("--keywords")
    u.add_argument("--type")
    u.add_argument("--new-slug")

    rm = sub.add_parser("remove")
    rm.add_argument("slug")
    rm.add_argument("--yes", action="store_true")

    ar = sub.add_parser("archive")
    ar.add_argument("slug")

    rs = sub.add_parser("restore")
    rs.add_argument("slug")

    sub.add_parser("archived")
    sub.add_parser("lint")

    c = sub.add_parser("compact")
    c.add_argument("--max-body", type=int, default=4000)
    c.add_argument("--apply", action="store_true")
    c.add_argument("--yes", action="store_true")

    args = p.parse_args(argv)
    st = _store(args)

    if args.cmd == "context":
        print(st.context() or "(no memory index yet)")
    elif args.cmd == "list":
        for e in st.entries():
            print(f"{e.slug}\t{e.type}\t{e.hook}")
    elif args.cmd == "recall":
        out = st.recall(args.query, k=args.k, budget=args.budget)
        print(out or "(no match)")
    elif args.cmd == "search":
        hits = st.search(args.query, k=args.k)
        for h in hits:
            print(f"{h.score:.2f}\t{h.entry.slug}\t{h.entry.hook}")
        if not hits:
            print("(no match)")
    elif args.cmd == "add":
        body = args.body if args.body is not None else Path(args.file).expanduser().read_text(encoding="utf-8")
        e = st.add(args.title, body, slug=args.slug, hook=args.hook,
                   type=args.type, keywords=_kw(args.keywords), overwrite=args.overwrite)
        print(f"added {e.slug}")
    elif args.cmd == "update":
        changes = {}
        for f in ("title", "body", "hook", "type"):
            v = getattr(args, f)
            if v is not None:
                changes[f] = v
        if args.keywords is not None:
            changes["keywords"] = _kw(args.keywords)
        if args.new_slug is not None:
            changes["new_slug"] = args.new_slug
        e = st.update(args.slug, **changes)
        print(f"updated {e.slug}")
    elif args.cmd == "remove":
        if not args.yes:
            print("refused: remove needs --yes (owner consent)", file=sys.stderr)
            return 2
        st.remove(args.slug)
        print(f"removed {args.slug}")
    elif args.cmd == "archive":
        dest = st.archive(args.slug)
        print(f"archived {args.slug} -> {dest}")
    elif args.cmd == "restore":
        e = st.restore(args.slug)
        print(f"restored {e.slug}")
    elif args.cmd == "archived":
        for p in st.archived():
            print(p)
        if not st.archived():
            print("(nothing archived)")
    elif args.cmd == "lint":
        problems = st.lint()
        print(json.dumps(problems, ensure_ascii=False, indent=2))
        if any(problems.values()):
            return 1
    elif args.cmd == "compact":
        apply = args.apply
        if apply and not args.yes:
            print("refused: --apply needs --yes (owner consent)", file=sys.stderr)
            return 2
        rep = st.compact(max_body=args.max_body, apply=apply)
        print(f"applied={apply} duplicates={len(rep['duplicates'])} oversize={len(rep['oversize'])} archived={len(rep['archived'])}")
        for d in rep["duplicates"]:
            print(f"  dup keep={d['keep']} drop={d['drop']}")
        for a in rep["archived"]:
            print(f"  archived {a['slug']} -> {a['path']}")
        for o in rep["oversize"]:
            print(f"  oversize {o['slug']} chars={o['chars']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
