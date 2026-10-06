"""Compatibility checks using the actual repository cache and old generator."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET

import feedparser
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import rss_generator
from cache_safety import cache_entries
from editorial_authors import author_for


class ProjectCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cache_path = ROOT / 'cache/articles_cache.json'
        cls.before = cls.cache_path.read_bytes()
        cls.data = json.loads(cls.before)
        cls.meta = {'title': 'Artbooms RSS Feed', 'description': 'Ultimi articoli da Artbooms', 'language': 'it-IT'}
        spec = importlib.util.spec_from_file_location('rss_baseline_fixture', ROOT / 'tests/fixtures/baseline_rss_generator.py')
        old = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(old)
        cls.old_xml = old.build_rss(list(cls.data['items'].values()), cls.meta)
        if isinstance(cls.old_xml, tuple):
            cls.old_xml = cls.old_xml[0]
        cls.new_xml = rss_generator.build_rss(list(cls.data['items'].values()), cls.meta)
        if isinstance(cls.new_xml, tuple):
            cls.new_xml = cls.new_xml[0]

    def test_all_real_articles_and_stable_identifiers_survive(self):
        old_items = ET.fromstring(self.old_xml).findall('./channel/item')
        new_items = ET.fromstring(self.new_xml).findall('./channel/item')
        old_guids = [item.findtext('guid') for item in old_items]
        new_guids = [item.findtext('guid') for item in new_items]
        self.assertGreater(len(new_guids), 1000)
        self.assertEqual(set(new_guids), set(old_guids))
        self.assertEqual(len(new_guids), len(set(new_guids)))
        self.assertEqual(set(new_guids), set(cache_entries(self.data)))

    def test_real_item_fields_only_change_for_explicit_editorial_author(self):
        def records(xml):
            return {item.findtext('guid'): item for item in ET.fromstring(xml).findall('./channel/item')}
        old, new = records(self.old_xml), records(self.new_xml)
        creator = '{http://purl.org/dc/elements/1.1/}creator'
        for guid, item in old.items():
            before = {child.tag: (child.text, dict(child.attrib)) for child in item}
            after = {child.tag: (child.text, dict(child.attrib)) for child in new[guid]}
            expected_author = author_for(guid, before[creator][0])
            before[creator] = (expected_author, before[creator][1])
            self.assertEqual(after, before, guid)

    def test_feedparser_reads_full_real_feed_without_errors(self):
        parsed = feedparser.parse(self.new_xml)
        self.assertFalse(parsed.bozo, getattr(parsed, 'bozo_exception', None))
        self.assertEqual(len(parsed.entries), len(self.data['items']))
        self.assertTrue(all(entry.get('title') and entry.get('link') and entry.get('published_parsed') for entry in parsed.entries))

    def test_generation_does_not_write_or_reorder_source_cache(self):
        self.assertEqual(self.cache_path.read_bytes(), self.before)

    def test_production_workflows_pin_os_and_preserve_dispatch_chain(self):
        for filename in ('persist_cache.yml', 'keepalive.yml'):
            data = yaml.load((ROOT / '.github/workflows' / filename).read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
            self.assertTrue(all(job['runs-on'] == 'ubuntu-24.04' for job in data['jobs'].values()))
        workflow = yaml.load((ROOT / '.github/workflows/persist_cache.yml').read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
        self.assertEqual(set(workflow['on']), {'workflow_dispatch'})
        trigger = workflow['jobs']['persist']['steps'][-1]
        self.assertIn('artbooms/artbooms-pwa-memory', trigger['run'])
        self.assertIn("updated == 'true'", trigger['if'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
