"""Standard links preserve the adopted document and offer the right domestic version."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest
from services.standard_source_links_service import standard_source_links


def source_records():
    return json.loads((ROOT / 'resources/quality_standards_2026.json').read_text())['sources']


class StandardSourceLinksTests(unittest.TestCase):
    def test_all_builtin_ws_versions_keep_the_original_and_add_a_domestic_record(self):
        sources = [source for source in source_records() if source['id'].startswith('wst')]
        self.assertEqual(8, len(sources))
        before = deepcopy(sources)
        for source in sources:
            with self.subTest(source=source['id']):
                links = standard_source_links(source)
                self.assertEqual(source['source_url'], links[0]['url'])
                self.assertEqual('查看全文PDF', links[0]['label'])
                self.assertEqual('国家卫生健康委', links[0]['provider'])
                self.assertEqual('国内标准信息', links[1]['label'])
                self.assertTrue(links[1]['url'].startswith('https://std.samr.gov.cn/hb/search/'))
        self.assertEqual(before, sources)

    def test_historical_version_is_not_replaced_by_the_current_document(self):
        sources = {row['id']: row for row in source_records()}
        old = standard_source_links(sources['wst641-2018'])
        current = standard_source_links(sources['wst641-2026'])
        self.assertNotEqual(old[0]['url'], current[0]['url'])
        self.assertNotEqual(old[1]['url'], current[1]['url'])
        self.assertIn('8B1827F149DCBB19E05397BE0A0AB44A', old[1]['url'])
        self.assertIn('58FED2B32E43886BE06397BE0A0A5BB3', current[1]['url'])

    def test_numeric_requirement_keeps_its_adopted_source_url(self):
        spec = json.loads((ROOT / 'resources/quality_targets_2024.json').read_text())['requirements'][0]
        links = standard_source_links(spec)
        self.assertEqual(spec['source_url'], links[0]['url'])
        self.assertEqual(2, len(links))

    def test_custom_and_non_wst_documents_do_not_gain_unrelated_official_claims(self):
        custom = dict(id='local-403', origin='custom', standard='WS/T 403—2024',
                      source_url='https://lab.example/sop.pdf')
        self.assertEqual(1, len(standard_source_links(custom)))
        for source in source_records():
            if source['id'].startswith('cdc-'):
                links = standard_source_links(source)
                self.assertEqual([source['source_url']], [row['url'] for row in links])
                self.assertNotEqual('国家卫生健康委', links[0]['provider'])

    def test_unsafe_or_missing_urls_are_not_rendered_as_links(self):
        for value in ('', None, 'javascript:alert(1)', 'file:///etc/passwd',
                      'https://', 'https://name:secret@nhc.gov.cn/example.pdf'):
            self.assertEqual([], standard_source_links(dict(source_url=value)))

    def test_ui_has_two_destinations_and_copyable_addresses_without_a_code_language(self):
        app = AppTest.from_string('''
import streamlit as st
from ui.standard_source_links import render_standard_source_links
with st.expander('查看参考标准原文'):
    render_standard_source_links(dict(id='wst494-2017', standard='WS/T 494—2017',
        source_url='https://www.nhc.gov.cn/wjw/s9492/201710/7faf1d5e1d13449d803c29e88c3d382e/files/1739781491810_39687.pdf'))
''', default_timeout=30).run()
        self.assertFalse(list(app.exception))
        buttons = app.get('link_button')
        self.assertEqual(['查看全文PDF', '国内标准信息'], [row.proto.label for row in buttons])
        self.assertEqual(2, len(app.code))
        self.assertEqual([row.proto.url for row in buttons], [row.value for row in app.code])
        self.assertTrue(all(row.proto.language == 'plaintext' for row in app.code))


if __name__ == '__main__':
    unittest.main(verbosity=2)
