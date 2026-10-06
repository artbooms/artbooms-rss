"""Native Linux/Gunicorn checks. HTTP traffic is restricted to a local fixture."""
import copy
import html
import json
import os
from pathlib import Path
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

import requests
from test_regressions import ROOT, VALID, cache, candidate, cs


def wait_for(check, timeout=15):
    deadline=time.monotonic()+timeout
    last=None
    while time.monotonic()<deadline:
        try:
            result=check()
            if result:return result
        except (OSError,ValueError,requests.RequestException) as exc:last=exc
        time.sleep(.05)
    raise AssertionError(f'Condition not met: {last}')


class LocalSource:
    def __init__(self):
        self.items=[copy.deepcopy(VALID)]
        self.archive_hits=0
        self.active=0
        self.maximum_active=0
        self.delay=.05
        self.fetch_started=threading.Event()
        self.remote_cache=cache()
        self.lock=threading.Lock()
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                if self.path=='/remote-cache':
                    body=json.dumps(owner.remote_cache).encode()
                elif self.path=='/archive':
                    with owner.lock:
                        owner.archive_hits+=1;owner.active+=1
                        owner.maximum_active=max(owner.maximum_active,owner.active)
                    owner.fetch_started.set()
                    try:
                        time.sleep(owner.delay)
                        rows=[]
                        for item in reversed(owner.items):
                            rows.append('<li class="archive-item"><a href="'+item['url']+'">Article</a><span class="archive-item-date">Oct 1, 2026</span></li>')
                        body=('<ul>'+''.join(rows)+'</ul>').encode()
                    finally:
                        with owner.lock:owner.active-=1
                else:
                    item=next((i for i in owner.items if urlsplit(i['url']).path==self.path),None)
                    if item is None:self.send_error(404);return
                    mapping={'name':'title','url':'url','description':'description','author':'author','datePublished':'published','dateModified':'modified','thumbnailUrl':'image'}
                    body=('<html><head>'+''.join('<meta itemprop="'+k+'" content="'+html.escape(item.get(v) or '',quote=True)+'">' for k,v in mapping.items())+'</head></html>').encode()
                self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers()
                try:self.wfile.write(body)
                except (BrokenPipeError,ConnectionResetError):pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.port=self.server.server_port
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join(2)


class GunicornNativeTests(unittest.TestCase):
    def start_server(self,preload=False):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        work=Path(tmp.name); cache_path=work/'articles_cache.json';feed_path=work/'feed.xml'
        cs.atomic_json(cache_path,cache())
        fixture=LocalSource();self.addCleanup(fixture.close)
        # Test-only transport adapter: production sources can never be contacted.
        (work/'sitecustomize.py').write_text('''import os
from urllib.parse import urlsplit, urlunsplit
import requests
_original = requests.sessions.Session.request
def _local_only(self, method, url, *args, **kwargs):
    parts = urlsplit(url)
    if parts.hostname == "www.artbooms.com":
        url = urlunsplit(("http", "127.0.0.1:" + os.environ["FIXTURE_PORT"], parts.path, parts.query, ""))
    elif parts.hostname != "127.0.0.1":
        raise RuntimeError("Non-local test request blocked: " + str(url))
    if method.upper() not in ("GET", "HEAD"):
        raise RuntimeError("Non-read test request blocked")
    return _original(self, method, url, *args, **kwargs)
requests.sessions.Session.request = _local_only
''')
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        env=os.environ.copy();env.update(CACHE_PATH=str(cache_path),FEED_PATH=str(feed_path),
            POPULATE_INTERVAL='.25',REQUEST_DELAY='0',CACHE_BOOTSTRAP_REMOTE='1',POPULATOR_ENABLED='1',
            RAW_CACHE_URL=f'http://127.0.0.1:{fixture.port}/remote-cache',
            ARCHIVE_URL=f'http://127.0.0.1:{fixture.port}/archive',FIXTURE_PORT=str(fixture.port),
            PYTHONPATH=str(work)+os.pathsep+str(ROOT),PYTHONDONTWRITEBYTECODE='1')
        log=open(work/'gunicorn.log','w+');self.addCleanup(log.close)
        shutil.copy(ROOT/'gunicorn.conf.py', work/'gunicorn.conf.py')
        args=[sys.executable,'-m','gunicorn','--bind',f'127.0.0.1:{port}',
              '--workers','2','--threads','4','app:app']
        if preload:args.append('--preload')
        proc=subprocess.Popen(args,cwd=work,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        def cleanup():
            if proc.poll() is None:
                proc.terminate()
                try:proc.wait(8)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid,signal.SIGKILL);proc.wait(5)
        self.addCleanup(cleanup)
        url=f'http://127.0.0.1:{port}'
        def ready():
            if proc.poll() is not None:log.flush();raise AssertionError((work/'gunicorn.log').read_text())
            return requests.get(url+'/rss',timeout=2).status_code==200
        wait_for(ready)
        wait_for(lambda:fixture.archive_hits>=2)
        return work,fixture,proc,url,cache_path,feed_path

    def test_two_workers_http_refresh_and_no_blocking_during_fetch(self):
        work,fixture,proc,url,cache_path,feed_path=self.start_server()
        initial=requests.get(url+'/rss',timeout=3)
        for path in ('/rss','/rss.xml','/feed.xml'):
            r=requests.get(url+path,timeout=3);self.assertEqual(r.content,initial.content);self.assertEqual(r.headers['ETag'],initial.headers['ETag'])
            r=requests.get(url+path,headers={'If-None-Match':initial.headers['ETag']},timeout=3)
            self.assertEqual(r.status_code,304);self.assertEqual(r.content,b'')
            self.assertEqual(requests.head(url+path,timeout=3).content,b'')
        fixture.delay=1.5;fixture.fetch_started.clear();self.assertTrue(fixture.fetch_started.wait(5))
        begin=time.monotonic();requests.get(url+'/rss',timeout=3);elapsed=time.monotonic()-begin
        self.assertLess(elapsed,.75,'RSS waited for the remote scan')
        data=json.loads(cache_path.read_text());manual=candidate(url=VALID['url']+'-manual')
        data['items'][manual['url']]=manual;cs.atomic_json(cache_path,data)
        etags=set()
        for _ in range(12):
            r=requests.get(url+'/rss',timeout=3);etags.add(r.headers['ETag'])
            self.assertEqual(len(ET.fromstring(r.content).findall('./channel/item')),2)
        self.assertEqual(len(etags),1);self.assertEqual(fixture.maximum_active,1)

    def test_preload_leader_death_failover_and_new_article(self):
        work,fixture,proc,url,cache_path,feed_path=self.start_server(preload=True)
        leader_path=Path(str(cache_path)+'.populator.lock')
        old_pid=wait_for(lambda:int(leader_path.read_text()))
        # Kill only a child worker of this exact temporary Gunicorn master.
        status=Path(f'/proc/{old_pid}/status').read_text()
        self.assertIn(f'PPid:\t{proc.pid}\n',status)
        wait_for(lambda:fixture.active==0)
        os.kill(old_pid,signal.SIGKILL)
        new_pid=wait_for(lambda:int(leader_path.read_text()) if int(leader_path.read_text())!=old_pid else None)
        self.assertNotEqual(new_pid,old_pid)
        new=candidate(url=VALID['url']+'-new',title='New article')
        fixture.items.append(new)
        wait_for(lambda:len(ET.fromstring(requests.get(url+'/rss',timeout=3).content).findall('./channel/item'))==2)
        r=requests.get(url+'/rss',timeout=3)
        self.assertIn(b'New article',r.content)
        self.assertEqual(json.loads(cache_path.read_text())['items'][new['url']]['author'],VALID['author'])
        self.assertEqual(proc.poll(),None)

    def test_import_does_not_start_threads_or_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            script='''import threading, requests
from unittest.mock import patch
with patch.object(requests.Session,"request",side_effect=AssertionError("network")):
    import app
assert not any(t.name=="BackgroundPopulator" for t in threading.enumerate())
'''
            env=os.environ.copy();env.update(PYTHONPATH=str(ROOT),PYTHONDONTWRITEBYTECODE='1')
            result=subprocess.run([sys.executable,'-c',script],cwd=tmp,env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(list(Path(tmp).iterdir()),[])


if __name__=='__main__':unittest.main(verbosity=2)
