"""Tests for memx. Run: python3 -m unittest discover -s tests -v  (from project dir)"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memx.core import Store, tokenize, MAX_INDEX_LINES, atomic_write  # noqa: E402

PROJ = str(Path(__file__).resolve().parents[1])


class MemxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = Store(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_tokenize_cjk_bigrams(self):
        toks = tokenize("ada 考试 deploy")
        self.assertIn("ada", toks)
        self.assertIn("考试", toks)
        self.assertIn("deploy", toks)

    def test_add_writes_topic_and_index(self):
        self.st.add("Proxy setup", "proxy daemon listens on port 8080 under systemd.", hook="proxy daemon setup", keywords=["proxy", "proxy"])
        idx = (Path(self.tmp.name) / "MEMORY.md").read_text(encoding="utf-8")
        self.assertIn("(topics/proxy-setup.md)", idx)
        # index must point, not carry body detail
        self.assertNotIn("port 8080", idx)
        self.assertIn("proxy daemon setup", idx)
        e = self.st.get("proxy-setup")
        self.assertEqual(e.keywords, ["proxy", "proxy"])

    def test_add_refuses_overwrite_without_flag(self):
        self.st.add("A", "a body long enough to pass", hook="hook A")
        with self.assertRaises(FileExistsError):
            self.st.add("A", "another body long enough", hook="hook A")
        self.st.add("A", "another body long enough", hook="hook A", overwrite=True)
        self.assertEqual(self.st.get("a").body.strip(), "another body long enough")

    def test_bad_slug_rejected(self):
        with self.assertRaises(ValueError):
            self.st.add("x", "a body long enough here", slug="../evil", hook="h")

    def test_search_ranks_relevant_topic_first(self):
        self.st.add("Proxy setup", "proxy core mixed-port launchd proxy", hook="proxy daemon setup", keywords=["proxy"])
        self.st.add("Dinner", "pasta recipe with garlic and basil", hook="dinner recipes")
        self.st.add("Discord bots", "discord gateway mention trigger 403 channel", hook="discord bot troubleshooting")
        hits = self.st.search("proxy proxy", k=3)
        self.assertEqual(hits[0].entry.slug, "proxy-setup")
        self.assertTrue(all(h.entry.slug != "dinner" for h in hits))

    def test_search_no_match_returns_empty(self):
        self.st.add("Dinner", "pasta recipe with garlic", hook="dinner recipes")
        self.assertEqual(self.st.search("kubernetes"), [])

    def test_recall_respects_budget(self):
        self.st.add("Big", "proxy " * 5000, hook="big proxy topic")
        out = self.st.recall("proxy", k=1, budget=500)
        self.assertLessEqual(len(out), 600)
        self.assertIn("[truncated]", out)

    def test_context_is_index_only(self):
        self.st.add("Secret detail", "very specific body text xyzzy", hook="detail about a secret")
        ctx = self.st.context()
        self.assertIn("Secret detail", ctx)
        self.assertNotIn("xyzzy", ctx)

    def test_context_truncates_long_index(self):
        for i in range(MAX_INDEX_LINES + 20):
            self.st.add(f"Topic {i}", f"body text for topic {i}", hook=f"hook {i}")
        ctx = self.st.context()
        self.assertIn("truncated", ctx)

    def test_update_changes_index(self):
        self.st.add("Note", "a note body long enough", hook="old hook text")
        self.st.update("note", hook="new hook text")
        self.assertIn("new hook text", (Path(self.tmp.name) / "MEMORY.md").read_text(encoding="utf-8"))

    def test_update_can_rename_slug(self):
        self.st.add("Note", "a note body long enough", hook="note hook")
        self.st.update("note", new_slug="renamed-note")
        self.assertIsNone(self.st.get("note"))
        self.assertIsNotNone(self.st.get("renamed-note"))
        idx = (Path(self.tmp.name) / "MEMORY.md").read_text(encoding="utf-8")
        self.assertIn("topics/renamed-note.md", idx)
        self.assertNotIn("topics/note.md", idx)

    def test_remove_and_index_rebuild(self):
        self.st.add("Gone", "a body long enough to pass", hook="gone hook")
        self.st.remove("gone")
        self.assertIsNone(self.st.get("gone"))
        self.assertNotIn("Gone", (Path(self.tmp.name) / "MEMORY.md").read_text(encoding="utf-8"))

    def test_compact_dry_run_does_not_delete(self):
        self.st.add("Dup A", "same body here", hook="dup hook")
        self.st.add("Dup B", "same body here", hook="dup hook", enforce_floor=False)
        rep = self.st.compact(apply=False)
        self.assertEqual(len(rep["duplicates"]), 1)
        self.assertEqual(len(self.st.entries()), 2)

    def test_compact_apply_archives_duplicate_keeps_newest(self):
        self.st.add("Dup A", "same body here", hook="dup hook")
        self.st.add("Dup B", "same body here", hook="dup hook", enforce_floor=False)
        rep = self.st.compact(apply=True)
        self.assertEqual(len(rep["duplicates"]), 1)
        self.assertEqual(len(self.st.entries()), 1)
        self.assertEqual(len(rep["archived"]), 1)
        self.assertEqual(len(self.st.archived()), 1)

    def test_compact_never_deletes_content(self):
        self.st.add("Dup A", "same body here", hook="dup hook")
        self.st.add("Dup B", "same body here", hook="dup hook", enforce_floor=False)
        self.st.compact(apply=True)
        found = any("same body here" in p.read_text(encoding="utf-8") for p in self.st.archived())
        self.assertTrue(found)

    def test_restore_brings_archived_topic_back(self):
        self.st.add("Dup A", "same body here", hook="dup hook")
        self.st.add("Dup B", "same body here", hook="dup hook", enforce_floor=False)
        self.st.compact(apply=True)
        archived_slug = self.st.archived()[0].name.split(".")[0]
        self.st.restore(archived_slug)
        self.assertEqual(len(self.st.entries()), 2)

    def test_compact_oversize_reported_not_changed_without_summarizer(self):
        self.st.add("Huge", "x" * 5000, hook="huge hook")
        rep = self.st.compact(max_body=4000, apply=True)
        self.assertEqual(rep["oversize"][0]["slug"], "huge")
        self.assertEqual(len(self.st.get("huge").body.strip()), 5000)

    def test_write_floor_rejects_short_body(self):
        ok, reason, _ = self.st.write_floor_check("T", "short")
        self.assertFalse(ok)
        self.assertIn("too short", reason)

    def test_write_floor_rejects_duplicate_body(self):
        self.st.add("Dup", "the very same body content here", hook="dup hook")
        ok, reason, _ = self.st.write_floor_check("Dup2", "the very same body content here")
        self.assertFalse(ok)
        self.assertIn("identical", reason)

    def test_write_floor_warns_when_hook_leaks_body(self):
        body = "The code word for the blue ferry project is KIWI-HARBOR-42 and it must stay secret."
        ok, _r, warns = self.st.write_floor_check(
            "Code word", body, hook="The code word for the blue ferry project is KIWI-HARBOR-42")
        self.assertTrue(ok)
        self.assertTrue(any("verbatim" in w for w in warns))

    def test_write_floor_warns_without_keywords(self):
        _ok, _r, warns = self.st.write_floor_check("T", "a reasonably long body here", hook="a hook")
        self.assertTrue(any("keywords" in w for w in warns))

    def test_lint_detects_dead_index_link(self):
        self.st.add("Good", "a fine body of text", hook="good hook")
        idx = Path(self.tmp.name) / "MEMORY.md"
        idx.write_text(idx.read_text(encoding="utf-8") + "- [Ghost](topics/ghost.md) — ghost\n", encoding="utf-8")
        problems = self.st.lint()
        self.assertIn("ghost", problems["dead_index_link"])

    def test_lint_clean_store(self):
        self.st.add("Good", "a fine body of text", hook="when the good thing matters", keywords=["good"])
        problems = self.st.lint()
        for k in ("missing_frontmatter", "empty_body", "no_hook", "hook_leaks_body",
                  "orphan_topic", "dead_index_link", "oversize"):
            self.assertEqual(problems[k], [], f"{k} should be empty")

    def test_atomic_write_leaves_no_temp_file(self):
        p = Path(self.tmp.name) / "x.md"
        atomic_write(p, "hello\n")
        self.assertEqual(p.read_text(encoding="utf-8"), "hello\n")
        self.assertEqual([q.name for q in Path(self.tmp.name).glob(".memx-*")], [])


class ConcurrencyTests(unittest.TestCase):
    """Several processes writing one store must not lose writes."""

    def test_concurrent_writes_all_land(self):
        with tempfile.TemporaryDirectory() as d:
            src = (
                "import sys; sys.path.insert(0, {proj!r})\n"
                "from memx.core import Store\n"
                "s = Store({d!r})\n"
                "tag = {tag!r}\n"
                "for i in range(15):\n"
                "    s.add('Proc ' + tag + ' topic ' + str(i),\n"
                "          'body text for entry ' + tag + ' number ' + str(i),\n"
                "          slug=tag + '-t' + str(i), hook='hook ' + tag + ' ' + str(i))\n"
            )
            procs = []
            for tag in ("a", "b", "c"):
                procs.append(subprocess.Popen([sys.executable, "-c", src.format(proj=PROJ, d=d, tag=tag)]))
            for p in procs:
                self.assertEqual(p.wait(timeout=180), 0)
            st = Store(d)
            slugs = {e.slug for e in st.entries()}
            self.assertEqual(len(slugs), 45, f"lost writes: got {len(slugs)}")
            idx = (Path(d) / "MEMORY.md").read_text(encoding="utf-8")
            for s in slugs:
                self.assertIn(f"topics/{s}.md", idx)


class IndexLeakTests(unittest.TestCase):
    """Regression: a fact must not leak into the always-loaded index (found in York E2E)."""

    def test_default_hook_does_not_copy_body(self):
        with tempfile.TemporaryDirectory() as d:
            st = Store(d)
            st.add("code word", "The code word is KIWI-HARBOR-42.", hook="code word topic")
            idx = (Path(d) / "MEMORY.md").read_text(encoding="utf-8")
            self.assertNotIn("KIWI-HARBOR-42", idx)
            self.assertIn("KIWI-HARBOR-42", st.get("code-word").body)

    def test_explicit_hook_is_used(self):
        with tempfile.TemporaryDirectory() as d:
            st = Store(d)
            st.add("code word", "The code word is KIWI-HARBOR-42.", hook="blue ferry code word, see topic")
            idx = (Path(d) / "MEMORY.md").read_text(encoding="utf-8")
            self.assertIn("blue ferry code word", idx)
            self.assertNotIn("KIWI-HARBOR-42", idx)


if __name__ == "__main__":
    unittest.main()
