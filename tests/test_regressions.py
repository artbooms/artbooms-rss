import copy
import importlib.util
import json
import logging
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import xml.etree.ElementTree as ET

import feedparser
import requests
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import cache_safety as cs
import article_processor as processor
import rss_generator as generator
import editorial_taxonomy as taxonomy
import news_sitemap as news
import app

logging.disable(logging.CRITICAL)
VALID = dict(url="https://www.artbooms.com/blog/test", title="Arte — ARTBOOMS",
             description="Descrizione", author="natasha barbieri", image="https://example.org/a.jpg",
             published="2026-10-01T10:00:00+02:00", modified="2026-10-01T10:00:00+02:00",
             _fetched_at="2026-10-02T08:00:00+00:00")
VALID["_hash"] = cs.article_hash(VALID)


def cache(*items):
    return dict(items={i["url"]: copy.deepcopy(i) for i in (items or (VALID,))}, cursor=7,
                last_scan="2026-10-02T08:00:00+00:00", links_hash="same-links")


def candidate(**kwargs):
    item = dict(VALID, **kwargs)
    item["_hash"] = cs.article_hash(item)
    return item


def lock_increment(path, count):
    for _ in range(count):
        with cs.cache_transaction(path):
            value = int(Path(path).read_text())
            time.sleep(.002)
            cs.atomic_write(path, str(value + 1).encode())


class ProcessorTests(unittest.TestCase):
    def test_real_parser_503_preserves_record(self):
        r = requests.Response(); r.status_code = 503; r.url = VALID["url"]
        old = copy.deepcopy(VALID)
        with patch.object(requests.Session, "get", return_value=r):
            item, changed = processor._process_one(old["url"], old)
        self.assertIs(item, old); self.assertFalse(changed); self.assertEqual(old, VALID)

    def test_real_parser_timeout_preserves_record(self):
        old = copy.deepcopy(VALID)
        with patch.object(requests.Session, "get", side_effect=requests.Timeout("timeout")):
            item, changed = processor._process_one(old["url"], old)
        self.assertIs(item, old); self.assertFalse(changed)

    def test_new_failure_remains_retryable(self):
        with patch.object(processor, "parse_article", return_value=dict(url=VALID["url"], title=None)):
            self.assertEqual(processor._process_one(VALID["url"]), (None, False))
        with patch.object(processor, "parse_article", return_value=copy.deepcopy(VALID)):
            item, changed = processor._process_one(VALID["url"])
        self.assertTrue(changed); self.assertTrue(cs.valid_article(item))

    def test_missing_author_preserves_existing(self):
        raw = dict(VALID, author=None, title="Titolo nuovo")
        with patch.object(processor, "parse_article", return_value=raw):
            item, changed = processor._process_one(VALID["url"], VALID)
        self.assertTrue(changed); self.assertEqual(item["author"], VALID["author"])

    def test_new_without_author_is_rejected(self):
        with patch.object(processor, "parse_article", return_value=dict(VALID, author=None)):
            self.assertEqual(processor._process_one(VALID["url"]), (None, False))

    def test_partial_optional_fields_and_hash(self):
        raw = dict(VALID, title="Nuovo", image=None, description="", modified=None)
        with patch.object(processor, "parse_article", return_value=raw):
            item, changed = processor._process_one(VALID["url"], VALID)
        self.assertTrue(changed)
        for key in ("description", "image", "modified"): self.assertEqual(item[key], VALID[key])
        self.assertEqual(item["_hash"], cs.article_hash(item))

    def test_malformed_previous_record_can_recover(self):
        with patch.object(processor, "parse_article", return_value=VALID):
            item, changed = processor._process_one(VALID["url"], "broken")
        self.assertTrue(changed); self.assertTrue(cs.valid_article(item))

    def test_unchanged_does_not_refresh_dates(self):
        with patch.object(processor, "parse_article", return_value=VALID):
            item, changed = processor._process_one(VALID["url"], VALID)
        self.assertIs(item, VALID); self.assertFalse(changed)

    def test_manual_edit_is_not_overwritten(self):
        edited = dict(VALID, title="Correzione manuale")
        with patch.object(processor, "parse_article", return_value=VALID):
            item, changed = processor._process_one(VALID["url"], edited)
        self.assertIs(item, edited); self.assertFalse(changed)

    def test_invalid_dates_urls_and_author(self):
        for changes in ({"published":"bad"}, {"url":"javascript:alert(1)"}, {"author":""},
                        {"title":"\x01"}, {"url":"https://example.org/a b"}):
            with self.subTest(changes=changes): self.assertFalse(cs.valid_article(dict(VALID, **changes)))


class MergeTests(unittest.TestCase):
    def test_identical_cache_is_identical_including_order_cursor(self):
        original = cache()
        result, _ = cs.merge_cache(original, copy.deepcopy(original))
        self.assertEqual(result, original); self.assertEqual(list(result["items"]), list(original["items"]))

    def test_missing_urls_are_retained(self):
        extra = candidate(url=VALID["url"] + "-extra")
        original = cache(VALID, extra)
        result, _ = cs.merge_cache(original, cache())
        self.assertEqual(result, original)

    def test_invalid_candidate_preserves_valid_record(self):
        other = candidate(url=VALID["url"] + "-other")
        incoming = cache(dict(VALID, title=None), other)
        result, stats = cs.merge_cache(cache(), incoming)
        self.assertEqual(result["items"][VALID["url"]], VALID)
        self.assertIn(other["url"], result["items"]); self.assertEqual(stats["invalid"], 1)

    def test_newer_valid_update_is_accepted(self):
        item = candidate(title="Aggiornato", _fetched_at="2026-10-02T09:00:00+00:00")
        result, stats = cs.merge_cache(cache(), cache(item))
        self.assertEqual(result["items"][item["url"]], item); self.assertEqual(stats["updated"], 1)

    def test_older_candidate_is_rejected(self):
        item = candidate(title="Vecchio", _fetched_at="2026-10-01T09:00:00+00:00")
        result, stats = cs.merge_cache(cache(), cache(item))
        self.assertEqual(result, cache()); self.assertEqual(stats["stale"], 1)

    def test_equal_timestamp_conflict_is_rejected(self):
        result, stats = cs.merge_cache(cache(), cache(candidate(title="Ambiguo")))
        self.assertEqual(result, cache()); self.assertEqual(stats["conflict"], 1)

    def test_missing_timestamp_conflict_is_rejected(self):
        result, _ = cs.merge_cache(cache(), cache(candidate(title="Ambiguo", _fetched_at=None)))
        self.assertEqual(result, cache())

    def test_hash_of_manual_edit_protects_it(self):
        old = dict(VALID, title="Manuale")
        newer = candidate(title="Scraper", _fetched_at="2026-10-03T09:00:00+00:00")
        result, _ = cs.merge_cache(cache(old), cache(newer))
        self.assertEqual(result, cache(old))

    def test_null_record_repaired_without_reordering(self):
        old = cache(); old["items"][VALID["url"]] = None
        result, _ = cs.merge_cache(old, cache())
        self.assertEqual(result["items"][VALID["url"]], VALID)
        self.assertEqual(list(result["items"]), list(old["items"]))

    def test_older_metadata_is_not_applied(self):
        incoming = cache(); incoming.update(cursor=2, last_scan="2026-09-01T00:00:00Z")
        result, _ = cs.merge_cache(cache(), incoming)
        self.assertEqual(result, cache())

    def test_cursor_can_wrap_after_later_scan(self):
        incoming = cache(); incoming.update(cursor=0, last_scan="2026-10-02T09:00:00Z")
        result, _ = cs.merge_cache(cache(), incoming)
        self.assertEqual(result["cursor"], 0)

    def test_entirely_unusable_candidate_fails(self):
        for incoming in ({"items":{}}, {"items":{VALID["url"]:None}}):
            with self.subTest(incoming=incoming), self.assertRaises(ValueError): cs.merge_cache(cache(), incoming)

    def test_real_merge_command_preserves_current_on_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); a=p/'current.json'; b=p/'candidate.json'
            cs.atomic_json(a, cache()); before=a.read_bytes(); b.write_text('{"items":{}}')
            result = subprocess.run([sys.executable, str(ROOT/'.github/scripts/merge_cache.py'), str(a), str(b), str(a)], capture_output=True)
            self.assertNotEqual(result.returncode, 0); self.assertEqual(a.read_bytes(), before)


class FeedTests(unittest.TestCase):
    def test_timezone_order_and_tie_are_deterministic(self):
        a=candidate(url=VALID["url"]+'a', published="2026-01-01T12:00:00+03:00")
        b=candidate(url=VALID["url"]+'b', published="2026-01-01T10:00:00Z")
        c=candidate(url=VALID["url"]+'c', published="2026-01-01T10:00:00+00:00")
        xml=ET.fromstring(generator.build_rss([c,a,b], {}))
        self.assertEqual([i.findtext('link') for i in xml.findall('./channel/item')], [b['url'],c['url'],a['url']])

    def test_bad_xml_characters_cdata_and_quotes(self):
        item=candidate(title="Arte &amp; museo\x01",description="A ]]> B\x00 C",image='https://example.org/a"b.jpg')
        xml=generator.build_rss([item],{}); root=ET.fromstring(xml)
        self.assertEqual(root.findtext('./channel/item/title'),'Arte & museo')
        self.assertEqual(root.findtext('./channel/item/description'),'A ]]> B C')
        self.assertEqual(feedparser.parse(xml).bozo,0)

    def test_categories_only_from_editorial_map(self):
        item=dict(VALID,categories=['Inferred'],tags=['Wrong'])
        with patch.object(taxonomy,'CATEGORY_VOCABULARY',frozenset({'Arte & cultura'})), patch.object(taxonomy,'ARTICLE_CATEGORIES',{item['url']:['Arte & cultura','Wrong',None,'Arte & cultura']}):
            xml=ET.fromstring(generator.build_rss([item],{}))
        self.assertEqual([x.text for x in xml.findall('./channel/item/category')],['Arte & cultura'])

    def test_missing_or_wrong_categories_do_not_block(self):
        for value in (None, 'Arte', {}, [None,{}]):
            with self.subTest(value=value), patch.object(taxonomy,'ARTICLE_CATEGORIES',{VALID['url']:value}):
                self.assertEqual(len(ET.fromstring(generator.build_rss([VALID],{})).findall('./channel/item')),1)

    def test_full_real_cache_feed(self):
        data=json.loads((ROOT/'cache/articles_cache.json').read_text())
        entries=cs.cache_entries(data); before=copy.deepcopy(data)
        xml=generator.build_rss(list(entries.values()),{})
        parsed=feedparser.parse(xml)
        self.assertEqual(parsed.bozo,0); self.assertEqual(len(parsed.entries),len(entries))
        self.assertEqual(len({i.id for i in parsed.entries}),len(entries))
        self.assertEqual(data,before)
        self.assertEqual({i.author for i in parsed.entries},{i['author'] for i in entries.values()})


class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.cache=self.root/'cache.json'; self.feed=self.root/'feed.xml'
        self.patches=[patch.object(app,'CACHE_PATH',str(self.cache)),patch.object(app,'FEED_PATH',str(self.feed)),
                      patch.object(processor,'CACHE_PATH',str(self.cache)), patch.dict(os.environ,{'CACHE_BOOTSTRAP_REMOTE':'0','POPULATOR_ENABLED':'0'})]
        for p in self.patches:p.start()
        self.addCleanup(self.cleanup)
        app._snapshot=app._source_token=app._source_digest=None
        cs.atomic_json(self.cache,cache()); self.client=app.app.test_client()

    def cleanup(self):
        for p in reversed(self.patches):p.stop()
        app._snapshot=app._source_token=app._source_digest=None
        self.tmp.cleanup()

    def test_startup_creates_missing_feed(self):
        app.start_worker(); self.assertTrue(self.feed.exists()); app._validate_rss(self.feed.read_bytes())

    def test_partial_cache_boots(self):
        data=cache();data['items']['broken']=None;cs.atomic_json(self.cache,data)
        app.start_worker();self.assertEqual(self.client.get('/rss').status_code,200)
        self.assertEqual(json.loads(self.cache.read_text()),data)

    def test_bootstrap_remote_partial_without_local(self):
        self.cache.unlink();data=cache();data['items']['broken']=None
        r=requests.Response();r.status_code=200;r._content=json.dumps(data).encode()
        with patch.dict(os.environ,{'CACHE_BOOTSTRAP_REMOTE':'1'}),patch.object(requests,'get',return_value=r):app.start_worker()
        self.assertEqual(self.client.get('/rss').status_code,200)
        self.assertEqual(json.loads(self.cache.read_text()),data)

    def test_aliases_get_head_and_304(self):
        responses=[self.client.get(p) for p in ('/rss','/rss.xml','/feed.xml')]
        first=responses[0]
        for r in responses:
            self.assertEqual(r.status_code,200);self.assertEqual(r.data,first.data);self.assertEqual(r.headers['ETag'],first.headers['ETag'])
        self.assertEqual(ET.fromstring(first.data).find('./channel/{http://www.w3.org/2005/Atom}link').get('href'), app.FEED_SELF_URL)
        for path in ('/rss','/rss.xml','/feed.xml'):
            self.assertEqual(self.client.head(path).data,b'')
            r=self.client.get(path,headers={'If-None-Match':first.headers['ETag']})
            self.assertEqual(r.status_code,304);self.assertEqual(r.data,b'')
        self.assertIn('must-revalidate',first.headers['Cache-Control'])

    def test_repeated_requests_do_not_read_or_hash_xml(self):
        self.client.get('/rss')
        with patch.object(app,'_read_file',side_effect=AssertionError('unexpected file read')),patch.object(app.hashlib,'sha256',side_effect=AssertionError('unexpected hash')):
            for _ in range(5):self.assertEqual(self.client.get('/rss').status_code,200)

    def test_cursor_change_preserves_xml_and_etag(self):
        first=self.client.get('/rss');token=app._file_token(self.feed)
        data=cache();data['cursor']=99;cs.atomic_json(self.cache,data)
        with patch.object(app,'build_rss',side_effect=AssertionError('unnecessary rebuild')):
            second=self.client.get('/rss')
        self.assertEqual(first.data,second.data);self.assertEqual(app._file_token(self.feed),token)

    def test_manual_change_visible_on_next_request(self):
        self.client.get('/rss');data=cache(VALID,candidate(url=VALID['url']+'-new'))
        cs.atomic_json(self.cache,data)
        self.assertEqual(len(ET.fromstring(self.client.get('/rss').data).findall('./channel/item')),2)

    def test_failed_build_preserves_last_good_and_retries(self):
        first=self.client.get('/rss');cs.atomic_json(self.cache,cache(candidate(title='Changed')))
        with patch.object(app,'build_rss',return_value='<broken>'):
            bad=self.client.get('/rss')
        self.assertEqual(first.data,bad.data)
        self.assertNotEqual(self.client.get('/rss').data,first.data)

    def test_global_corrupt_cache_does_not_replace_feed(self):
        first=self.client.get('/rss');self.cache.write_text('{broken')
        self.assertEqual(self.client.get('/rss').data,first.data)
        with self.assertRaises(ValueError):processor._save_cache(cache())
        self.assertEqual(self.cache.read_text(),'{broken')

    def test_missing_feed_recovers_from_memory_and_cache(self):
        first=self.client.get('/rss');self.feed.unlink()
        self.assertEqual(self.client.get('/rss').data,first.data);self.assertTrue(self.feed.exists())

    def test_restart_keeps_lastbuilddate(self):
        first=self.client.get('/rss');app._snapshot=app._source_token=app._source_digest=None
        self.assertEqual(self.client.get('/rss').data,first.data)

    def test_restart_with_corrupt_cache_keeps_valid_xml(self):
        first=self.client.get('/rss');self.cache.write_text('{broken')
        app._snapshot=app._source_token=app._source_digest=None
        app.start_worker()
        self.assertEqual(self.client.get('/rss').data,first.data)

    def test_save_merges_with_intervening_manual_correction(self):
        incoming=cache(candidate(title='Automatic',_fetched_at='2026-10-03T00:00:00Z'))
        edited=cache(dict(VALID,title='Manual correction'));cs.atomic_json(self.cache,edited)
        processor._save_cache(incoming)
        self.assertEqual(json.loads(self.cache.read_text()),edited)

    def test_cycle_retries_new_article(self):
        new=candidate(url=VALID['url']+'-new')
        with patch.object(processor,'_scan_archive',return_value=[VALID['url'],new['url']]),patch.object(processor.time,'sleep'),patch.object(processor,'parse_article',side_effect=[dict(new,title=None),new]):
            processor.generate_items();self.assertNotIn(new['url'],json.loads(self.cache.read_text())['items'])
            processor.generate_items();self.assertIn(new['url'],json.loads(self.cache.read_text())['items'])


class SitemapTests(unittest.TestCase):
    def render(self,data,local_first=False):
        with patch.object(news,'LOCAL_FIRST',local_first),patch.object(news,'_load_local_items',return_value=news._cache_items(data)),patch.object(news,'_load_remote_items',return_value=news._cache_items(data)):
            return news.news_sitemap_view()

    def test_bad_row_is_excluded_not_whole_sitemap(self):
        data=cache();data['items']['broken']='invalid'
        self.assertEqual(self.render(data).status_code,200)

    def test_local_first_does_not_call_github(self):
        with patch.object(news,'LOCAL_FIRST',True),patch.object(news,'_load_local_items',return_value=[VALID]),patch.object(news,'_load_remote_items',side_effect=AssertionError('network')):
            self.assertEqual(news.news_sitemap_view().status_code,200)

    def test_both_source_orders_and_fallback(self):
        for local_first in (True,False):
            first='_load_local_items' if local_first else '_load_remote_items'
            second='_load_remote_items' if local_first else '_load_local_items'
            with self.subTest(local_first=local_first),patch.object(news,'LOCAL_FIRST',local_first),patch.object(news,first,side_effect=ValueError('unavailable')),patch.object(news,second,return_value=[VALID]):
                self.assertEqual(news.news_sitemap_view().status_code,200)

    def test_both_sources_unusable_return_503(self):
        with patch.object(news,'_load_local_items',side_effect=ValueError()),patch.object(news,'_load_remote_items',side_effect=ValueError()):
            self.assertEqual(news.news_sitemap_view().status_code,503)

    def test_48h_future_and_title(self):
        now=datetime.now(timezone.utc)
        fresh=candidate(published=(now-timedelta(hours=2)).isoformat())
        old=candidate(url=VALID['url']+'old',published=(now-timedelta(hours=49)).isoformat())
        future=candidate(url=VALID['url']+'future',published=(now+timedelta(hours=1)).isoformat())
        response=self.render(cache(fresh,old,future))
        root=ET.fromstring(response.data);ns={'s':'http://www.sitemaps.org/schemas/sitemap/0.9','n':'http://www.google.com/schemas/sitemap-news/0.9'}
        self.assertEqual(len(root.findall('s:url',ns)),1)
        self.assertEqual(root.findtext('s:url/n:news/n:title',namespaces=ns),'Arte')
        self.assertEqual(root.findtext('s:url/n:news/n:publication_date',namespaces=ns),datetime.fromisoformat(fresh['published']).replace(microsecond=0).isoformat())

    def test_old_article_has_no_news_metadata(self):
        old=candidate(published='2020-01-01T00:00:00Z')
        root=ET.fromstring(self.render(cache(old)).data)
        self.assertEqual(len(root),1);self.assertNotIn('news}', ''.join(n.tag for n in root.iter()))

    def test_official_sitemap_and_news_xsd(self):
        recent=candidate(published=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat())
        schemas=ROOT/'tests/schemas'
        sitemap=etree.XMLSchema(etree.fromstring((
            '<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
            '<xs:import namespace="http://www.sitemaps.org/schemas/sitemap/0.9" schemaLocation="sitemap.xsd"/>'
            '<xs:import namespace="http://www.google.com/schemas/sitemap-news/0.9" schemaLocation="sitemap-news.xsd"/>'
            '</xs:schema>').encode(), base_url=str(schemas/'combined.xsd')))
        news_schema=etree.XMLSchema(etree.parse(str(schemas/'sitemap-news.xsd')))
        for local_first in (False,True):
            xml=etree.fromstring(self.render(cache(recent),local_first).data)
            # Strict sitemap wildcard resolved through the two official imported schemas.
            sitemap.assertValid(xml)
            for node in xml.findall('.//{http://www.google.com/schemas/sitemap-news/0.9}news'):news_schema.assertValid(node)


class NativeLockTests(unittest.TestCase):
    def test_multiprocess_atomic_increments(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=str(Path(tmp)/'counter');Path(path).write_text('0')
            ctx=multiprocessing.get_context('spawn')
            processes=[ctx.Process(target=lock_increment,args=(path,12)) for _ in range(4)]
            for p in processes:p.start()
            for p in processes:p.join(15);self.assertEqual(p.exitcode,0)
            self.assertEqual(Path(path).read_text(),'48')

    def test_nested_same_cache_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=str(Path(tmp)/'cache')
            with cs.cache_transaction(path):
                with cs.cache_transaction(path,blocking=False) as acquired:self.assertTrue(acquired)


if __name__=='__main__': unittest.main(verbosity=2)
