import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from test_regressions import ROOT, VALID, cache, candidate, cs, processor, generator, app
from article_parser import parse_article
from editorial_authors import ARTICLE_AUTHOR_OVERRIDES

URL='https://www.artbooms.com/blog/mimmo-rotella-e-il-cinema'
BYLINE='Martina e Mattia Stripartgallery'
NS={'dc':'http://purl.org/dc/elements/1.1/'}


class EditorialAuthorTests(unittest.TestCase):
    def test_parser_reads_public_metadata_without_backend_access(self):
        html='''<html><head><meta itemprop="name" content="Titolo">
<meta itemprop="author" content="Autrice Ospite">
<meta itemprop="datePublished" content="2026-10-01T10:00:00+02:00">
</head></html>'''
        with patch('requests.Session.request',side_effect=AssertionError('unexpected request')):
            item=parse_article(VALID['url']+'-new',html=html)
        self.assertEqual(item['author'],'Autrice Ospite')
        self.assertTrue(cs.valid_article(item))

    def test_future_author_requires_no_code_registration(self):
        item=candidate(url=VALID['url']+'-guest',author='Autrice Ospite')
        with patch.object(processor,'parse_article',return_value=item):
            parsed,changed=processor._process_one(item['url'])
        self.assertTrue(changed)
        xml=ET.fromstring(generator.build_rss([parsed],{}))
        self.assertEqual(xml.findtext('./channel/item/dc:creator',namespaces=NS),'Autrice Ospite')

    def test_verified_byline_overrides_cms_attribution(self):
        old=candidate(url=URL,author='Account tecnico')
        fixed=cs.with_editorial_author(old)
        self.assertEqual(fixed['author'],BYLINE)
        self.assertEqual(fixed['_hash'],cs.article_hash(fixed))
        self.assertEqual({k for k in old if old[k]!=fixed[k]},{'author','_hash'})
        self.assertEqual(old['author'],'Account tecnico')

    def test_feed_generator_applies_override_to_raw_cached_items(self):
        old=candidate(url=URL,author='Account tecnico')
        xml=ET.fromstring(generator.build_rss([old],{}))
        self.assertEqual(xml.findtext('./channel/item/dc:creator',namespaces=NS),BYLINE)

    def test_bad_fetch_is_not_made_valid_by_author_override(self):
        with patch.object(processor,'parse_article',return_value={'url':URL,'title':None,'published':None}):
            self.assertEqual(processor._process_one(URL),(None,False))

    def test_merge_does_not_restore_wrong_cms_author(self):
        old=candidate(url=URL,author=BYLINE)
        incoming=candidate(url=URL,author='Account tecnico',_fetched_at='2026-10-03T00:00:00Z')
        result,_=cs.merge_cache(cache(old),cache(incoming))
        self.assertEqual(result['items'][URL]['author'],BYLINE)
        self.assertEqual(result['items'][URL]['_hash'],cs.article_hash(result['items'][URL]))

    def test_unrelated_manual_edit_stays_protected(self):
        old=candidate(url=URL,author='Account tecnico')
        old['title']='Correzione manuale'
        fixed=cs.with_editorial_author(old)
        self.assertEqual(fixed['_hash'],old['_hash'])
        self.assertNotEqual(fixed['_hash'],cs.article_hash(fixed))
        self.assertEqual(fixed['title'],'Correzione manuale')

    def test_full_cache_correction_changes_only_one_author_and_hash(self):
        raw=json.loads((ROOT/'cache/articles_cache.json').read_text())
        before=copy.deepcopy(raw)
        fixed=cs.with_editorial_authors(raw)
        self.assertEqual(raw,before)
        self.assertEqual(list(raw['items']),list(fixed['items']))
        self.assertEqual({k:v for k,v in raw.items() if k!='items'},
                         {k:v for k,v in fixed.items() if k!='items'})
        changed=[u for u in raw['items'] if raw['items'][u]!=fixed['items'][u]]
        expected=[URL] if raw['items'][URL]['author']!=BYLINE else []
        self.assertEqual(changed,expected)
        if expected:
            self.assertEqual({k for k in raw['items'][URL] if raw['items'][URL][k]!=fixed['items'][URL][k]}, {'author','_hash'})
        self.assertEqual(fixed['items'][URL]['author'],BYLINE)
        self.assertEqual(len(cs.cache_entries(fixed)),len(raw['items']))

    def test_startup_corrects_local_cache_without_fetching_article(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);path=p/'cache.json';feed=p/'feed.xml'
            raw=cache(candidate(url=URL,author='Account tecnico'));cs.atomic_json(path,raw)
            with patch.object(app,'CACHE_PATH',str(path)),patch.object(app,'FEED_PATH',str(feed)),patch.dict(os.environ,{'CACHE_BOOTSTRAP_REMOTE':'0','POPULATOR_ENABLED':'0'}),patch('requests.Session.request',side_effect=AssertionError('network')):
                app._snapshot=app._source_token=app._source_digest=None
                app.start_worker()
                self.assertEqual(json.loads(path.read_text())['items'][URL]['author'],BYLINE)
                xml=ET.fromstring(app.app.test_client().get('/rss').data)
                self.assertEqual(xml.findtext('./channel/item/dc:creator',namespaces=NS),BYLINE)
            app._snapshot=app._source_token=app._source_digest=None


if __name__=='__main__':unittest.main(verbosity=2)
