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
        self.assertEqual(items["urn:doi:10.1234/a"]["description"],
                         rss.description("Public abstract & text", "", "https://doi.org/10.1234/a"))

    def test_abstract_paragraphs_headings_inline_markup_and_footers(self):
        abstract = ('<jats:sec><jats:title>Summary</jats:title>'
                    '<jats:p>First <jats:italic>plant</jats:italic> &amp; gene.</jats:p>'
                    '<jats:p>Second paragraph.<br/>Next line.</jats:p></jats:sec>')
        body = rss.description(abstract, "The Plant Journal", "https://doi.org/10.1234/a")
        self.assertIn('<h3>Summary</h3><p>First <em>plant</em> &amp; gene.</p>', body)
        self.assertIn('<p>Second paragraph.<br>Next line.</p>', body)
        self.assertTrue(body.endswith('<p>Journal: The Plant Journal</p>'
                        '<p>Original article: <a href="https://doi.org/10.1234/a">https://doi.org/10.1234/a</a></p></div>'))
        self.assertEqual(rss.abstract_html("Abstract\nFirst paragraph.\n\nSecond paragraph."),
                         "Abstract<br>First paragraph.<br><br>Second paragraph.")
        self.assertEqual(rss.abstract_html("Body without a heading."), "Body without a heading.")

    def test_abstract_only_keeps_safe_formatting(self):
        self.assertEqual(rss.abstract_html('<p onclick="bad()">A<script>bad()</script>'
                         '<img src="private"><a href="private">link</a> &lt; B</p>'),
                         '<p>Alink &lt; B</p>')
        self.assertEqual(rss.abstract_html('<ul><li>One</li><li><b>Two</b></li></ul>'),
                         '<ul><li>One</li><li><strong>Two</strong></li></ul>')

    def test_xml_indentation_keeps_italic_and_superscript_inline(self):
        raw = ('<jats:title>SUMMARY</jats:title>\n  <jats:p>\n'
               'A tomato (\n    <jats:italic>Solanum lycopersicum</jats:italic>\n'
               'L. Mill) limits Na\n    <jats:sup>+</jats:sup>\n'
               'accumulation.<jats:break/>An explicit line break.</jats:p>')
        rendered = rss.abstract_html(raw)
        self.assertEqual(rendered.count('<br>'), 1)
        self.assertIn('<h3>SUMMARY</h3>', rendered)
        self.assertIn('(<em>', rendered)
        self.assertIn('<em>Solanum lycopersicum</em> L. Mill', rendered)
        self.assertIn('Na<sup>+</sup> accumulation.', rendered)

    def test_jats_list_inside_paragraph_preserves_all_four_items_as_valid_html(self):
        raw = ('<jats:title>Summary</jats:title><jats:p><jats:list list-type="bullet">'
               '<jats:list-item><jats:p>Lodging severely reduces yield.</jats:p></jats:list-item>'
               '<jats:list-item><jats:p>Functional validation of <jats:italic>STRONG3</jats:italic>.</jats:p></jats:list-item>'
               '<jats:list-item><jats:p>Molecular analyses of <jats:italic>4CL4</jats:italic>'
               ' and STRONG3<jats:sup>G</jats:sup>.</jats:p></jats:list-item>'
               '<jats:list-item><jats:p>Overexpression enhanced resistance.</jats:p></jats:list-item>'
               '</jats:list></jats:p>')
        rendered = rss.abstract_html(raw)
        root = rss.ET.fromstring('<div>' + rendered + '</div>')
        self.assertEqual(len(root.findall('ul/li')), 4)
        self.assertFalse(root.findall('.//p//ul'))
        self.assertFalse(root.findall('.//p//p'))
        self.assertNotIn('<p></p>', rendered)
        self.assertEqual([''.join(li.itertext()) for li in root.findall('ul/li')],
                         ['Lodging severely reduces yield.', 'Functional validation of STRONG3.',
                          'Molecular analyses of 4CL4 and STRONG3G.', 'Overexpression enhanced resistance.'])
        self.assertIn('<em>4CL4</em>', rendered)
        self.assertIn('<sup>G</sup>', rendered)

    def test_formatted_history_upgrade_has_one_footer_even_without_public_metadata(self):
        for version, labels in [(2, ('期刊来源：', '原文链接：')), (3, ('Journal: ', 'Original article: '))]:
            old, _ = self.merge(final('a'))
            root = rss.ET.fromstring(old)
            root.find('channel/item/description').text = (
                f'<div class="zotwatch-rss-description-v{version}"><div><p><ul>'
                '<li><p>First</p></li><li><p>Last</p></li></ul></p></div>'
                f'<p>{labels[0]}Journal A</p><p>{labels[1]}'
                '<a href="https://doi.org/10.1234/a">https://doi.org/10.1234/a</a></p></div>')
            old = rss.ET.tostring(root)
            updated, n = rss.merge_feed(final('a'), old, URL, NOW,
                                        resolve=lambda _: {'abstract': '', 'journal': ''})
            desc = rss.read_feed(updated)['urn:doi:10.1234/a']['description']
            self.assertEqual(n, 0)
            self.assertEqual(desc.count('Journal: Journal A'), 1)
            self.assertEqual(desc.count('Original article:'), 1)
            self.assertEqual(desc.count('<li>'), 2)
            parsed = rss.ET.fromstring(desc)
            self.assertFalse(parsed.findall('.//p//ul'))
            self.assertEqual(rss.merge_feed(final('a'), updated, URL, NOW,
                             resolve=lambda _: self.fail('Repeat must reuse upgraded description')), (updated, 0))

    def test_old_descriptions_upgrade_once_without_new_items_or_new_dates(self):
        old, _ = self.merge(final("a", "b"))
        root = rss.ET.fromstring(old)
        for item in root.findall("channel/item"):
            item.find("description").text = "Previously flattened summary"
        old = rss.ET.tostring(root, encoding="utf-8", xml_declaration=True)
        before = rss.read_feed(old)
        fetched = []
        def resolve(guid):
            fetched.append(guid)
            return {"abstract": '<h2>Abstract</h2><p>Recovered paragraph.</p>', "journal": "Journal A"}
        upgraded, n = rss.merge_feed(final("a"), old, URL, NOW + timedelta(days=1), resolve)
        self.assertEqual(n, 0)
        self.assertEqual(set(fetched), set(before))
        after = rss.read_feed(upgraded)
        for guid in before:
            self.assertEqual({k: v for k, v in before[guid].items() if k != "description"},
                             {k: v for k, v in after[guid].items() if k != "description"})
            self.assertIn('<h3>Abstract</h3><p>Recovered paragraph.</p>', after[guid]['description'])
        self.assertEqual(rss.merge_feed(final("a"), upgraded, URL, NOW,
                         resolve=lambda _: self.fail("Upgrade must be idempotent")), (upgraded, 0))

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
