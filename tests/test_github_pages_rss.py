"""Delivery invariants: privacy, history, deduplication, and fail-closed merge."""
import importlib.util
from pathlib import Path
from datetime import datetime, timedelta, timezone
import unittest


spec = importlib.util.spec_from_file_location("rss", Path(__file__).parents[1] / "scripts/github_pages_rss.py")
rss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rss)
URL = "https://example.github.io/rss/feed.xml"
NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)


def final(*ids, status="succeeded"):
    return dict(status=status, command="watch", evidence={"model_state": "PRIVATE"}, recommendations=[
        dict(work_key=f"10.1234/{i}", title=f"Paper {i} & <b>title</b>", url="https://private.example/?secret=PRIVATE",
             score=0.99, confirmed_profile_revision="PRIVATE", interest_profile="PRIVATE",
             abstract="PRIVATE", latent_recall={"model_state": "PRIVATE"}) for i in ids])


class RSSDelivery(unittest.TestCase):
    def merge(self, result, old=None, now=NOW):
        return rss.merge_feed(result, old, URL, now, resolve=lambda guid: "Public abstract & text")

    def test_closed_public_projection_and_duplicate_identity(self):
        data, count = self.merge(final("a", "a", "b"))
        self.assertEqual(count, 2)
        self.assertNotIn(b"PRIVATE", data)
        self.assertNotIn(b"score", data)
        items = rss.read_feed(data)
        self.assertEqual(len(items), 2)
        self.assertEqual(items["urn:doi:10.1234/a"]["link"], "https://doi.org/10.1234/a")
        self.assertEqual(items["urn:doi:10.1234/a"]["description"], "Public abstract & text")

    def test_first_date_repeat_bytes_and_latest_100(self):
        old, _ = self.merge(final(*map(str, range(100))))
        repeated, n = rss.merge_feed(final("1", "1"), old, URL, NOW + timedelta(days=1),
                                     resolve=lambda _: self.fail("Repeat must not fetch metadata"))
        self.assertEqual((repeated, n), (old, 0))
        updated, n = self.merge(final("new", "99"), old, NOW + timedelta(days=2))
        items = rss.read_feed(updated)
        self.assertEqual((len(items), n), (100, 1))
        self.assertIn("urn:doi:10.1234/new", items)
        self.assertEqual(items["urn:doi:10.1234/99"]["pubDate"], rss.read_feed(old)["urn:doi:10.1234/99"]["pubDate"])

    def test_non_success_and_empty_never_replace(self):
        old, _ = self.merge(final("a"))
        for status in ("failed", "degraded", "paused", "not_ready"):
            self.assertEqual(self.merge(final("b", status=status), old), (old, 0))
        self.assertEqual(self.merge(final(), old), (old, 0))
        self.assertEqual(self.merge(None, old), (old, 0))

    def test_invalid_old_or_public_identity_aborts(self):
        for old in (b"invalid", b'<!DOCTYPE rss [<!ENTITY x "secret">]><rss/>'):
            with self.assertRaises((ValueError, rss.ET.ParseError)):
                self.merge(final("a"), old)
        invalid = final("a")
        invalid["recommendations"][0]["work_key"] = "zotero:private-key"
        with self.assertRaises(ValueError):
            self.merge(invalid)


if __name__ == "__main__":
    unittest.main()
