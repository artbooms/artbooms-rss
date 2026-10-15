"""Execute the actual persistence step against temporary local Git repositories."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import yaml
from test_regressions import ROOT, VALID, cache, candidate, cs


def git(cwd,*args):
    result=subprocess.run(['git',*args],cwd=cwd,capture_output=True,text=True)
    if result.returncode:raise AssertionError(result.stderr)
    return result.stdout.strip()


class PersistWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.seed=self.root/'seed';self.seed.mkdir()
        self.bare=self.root/'origin.git';self.runner=self.root/'runner';self.download=self.root/'download';self.download.mkdir()
        git(self.root,'init','--bare',str(self.bare))
        git(self.seed,'init','-b','main');git(self.seed,'config','user.name','test');git(self.seed,'config','user.email','test@example.invalid')
        (self.seed/'.github/scripts').mkdir(parents=True);(self.seed/'cache').mkdir()
        shutil.copy(ROOT/'cache_safety.py',self.seed/'cache_safety.py')
        shutil.copy(ROOT/'editorial_authors.py',self.seed/'editorial_authors.py')
        shutil.copy(ROOT/'.github/scripts/merge_cache.py',self.seed/'.github/scripts/merge_cache.py')
        cs.atomic_json(self.seed/'cache/articles_cache.json',cache())
        git(self.seed,'add','.');git(self.seed,'commit','-m','initial fixture')
        git(self.seed,'remote','add','origin',str(self.bare));git(self.seed,'push','origin','main')
        git(self.root,'clone','-b','main',str(self.bare),str(self.runner))
        self.workflow=yaml.load((ROOT/'.github/workflows/persist_cache.yml').read_text(),Loader=yaml.BaseLoader)
        step=next(s for s in self.workflow['jobs']['persist']['steps'] if s.get('id')=='persist_cache')
        self.script=step['run']
        self.output=self.root/'outputs';self.output.touch()

    def run_step(self,data):
        cs.atomic_json(self.download/'articles_cache.json',data)
        env=os.environ.copy();env.update(RUNNER_TEMP=str(self.download),GITHUB_OUTPUT=str(self.output),
            PATH=str(Path(sys.executable).parent)+os.pathsep+env['PATH'],GIT_TERMINAL_PROMPT='0')
        return subprocess.run(['bash','--noprofile','--norc','-e','-o','pipefail','-c',self.script],cwd=self.runner,env=env,capture_output=True,text=True)

    def persisted(self):
        return json.loads(git(self.bare,'show','main:cache/articles_cache.json'))

    def assert_cache_commit(self):
        message=git(self.bare,'log','-1','--format=%B','main')
        self.assertRegex(message,r'^Persist cache \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z \[skip render\]$')
        # Render's marker must not suppress GitHub Actions or the PWA dispatch.
        for marker in ('[skip ci]','[ci skip]','[no ci]','[skip actions]','[actions skip]','skip-checks:'):
            self.assertNotIn(marker,message.lower())
        self.assertEqual(git(self.bare,'diff-tree','--no-commit-id','--name-only','-r','main'),'cache/articles_cache.json')

    def test_fetch_latest_then_merge_and_push_only_cache(self):
        # Main advances after checkout, exactly the race the workflow must handle.
        manual=dict(VALID,title='Correction already on main',description='Manual description',
                    image='https://example.org/manual.jpg',_editorial_note='Keep this correction')
        current=cache(manual)
        current.update(cursor=41,last_scan='2026-10-02T12:00:00Z',links_hash='manually-reviewed-links',
                       editorial_note='Retain cache metadata')
        cs.atomic_json(self.seed/'cache/articles_cache.json',current)
        git(self.seed,'add','.');git(self.seed,'commit','-m','manual correction');git(self.seed,'push','origin','main')
        incoming=cache(candidate(title='Newer scraper title',_fetched_at='2026-10-03T12:00:00Z'),
                       candidate(url=VALID['url']+'-new'))
        result=self.run_step(incoming)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        persisted=self.persisted()
        self.assertEqual(persisted['items'][VALID['url']],manual)
        self.assertIn(VALID['url']+'-new',persisted['items'])
        self.assertEqual({k:v for k,v in persisted.items() if k!='items'},
                         {k:v for k,v in current.items() if k!='items'})
        self.assertIn('updated=true',self.output.read_text())
        self.assert_cache_commit()

    def test_scan_metadata_only_change_is_persisted_and_triggers_dispatch(self):
        incoming=cache()
        incoming.update(cursor=0,last_scan='2026-10-02T09:00:00Z',links_hash='new-scan-links')
        result=self.run_step(incoming)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(self.persisted(),incoming)
        self.assertEqual(self.output.read_text().splitlines(),['updated=true'])
        self.assert_cache_commit()

    def test_repeated_candidate_does_not_create_another_commit_or_dispatch(self):
        incoming=cache(VALID,candidate(url=VALID['url']+'-new'))
        first=self.run_step(incoming)
        self.assertEqual(first.returncode,0,first.stdout+first.stderr)
        before=git(self.bare,'rev-parse','main')
        # Each real workflow run gets a fresh GITHUB_OUTPUT file.
        self.output.write_text('')
        second=self.run_step(copy.deepcopy(incoming))
        self.assertEqual(second.returncode,0,second.stdout+second.stderr)
        self.assertEqual(git(self.bare,'rev-parse','main'),before)
        self.assertEqual(self.output.read_text(),'')

    def test_normal_code_commit_keeps_its_message_and_is_preserved(self):
        (self.seed/'application_fixture.py').write_text('VALUE = 2\n')
        git(self.seed,'add','application_fixture.py');git(self.seed,'commit','-m','Fix application behavior')
        git(self.seed,'push','origin','main')
        code_commit=git(self.bare,'rev-parse','main')
        result=self.run_step(cache(VALID,candidate(url=VALID['url']+'-new')))
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(git(self.bare,'rev-parse','main^'),code_commit)
        self.assertEqual(git(self.bare,'show','main:application_fixture.py'),'VALUE = 2')
        self.assertEqual(git(self.bare,'log','-1','--format=%B',code_commit),'Fix application behavior')
        self.assert_cache_commit()

    def test_invalid_candidate_does_not_commit_or_push(self):
        before=git(self.bare,'rev-parse','main')
        result=self.run_step({'items':{VALID['url']:dict(VALID,title=None)}})
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(git(self.bare,'rev-parse','main'),before)
        self.assertNotIn('updated=true',self.output.read_text())

    def test_unchanged_candidate_does_not_create_commit(self):
        before=git(self.bare,'rev-parse','main')
        result=self.run_step(cache())
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(git(self.bare,'rev-parse','main'),before)
        self.assertNotIn('updated=true',self.output.read_text())

    def test_pwa_trigger_and_dispatch_schedule_are_preserved(self):
        self.assertEqual(set(self.workflow['on']),{'workflow_dispatch'})
        step=self.workflow['jobs']['persist']['steps'][-1]
        self.assertEqual(step['if'],"steps.persist_cache.outputs.updated == 'true'")
        self.assertEqual(step['env']['PWA_DISPATCH_TOKEN'],'${{ secrets.PWA_DISPATCH_TOKEN }}')
        # Execute the real dispatch shell with curl replaced by a local argument recorder.
        args_path=self.root/'dispatch-args'
        env=os.environ.copy();env.update(DISPATCH_ARGS=str(args_path),PWA_DISPATCH_TOKEN='offline-test-token')
        dispatch=subprocess.run(['bash','--noprofile','--norc','-e','-o','pipefail','-c',
            'curl() { printf \'%s\\0\' "$@" > "$DISPATCH_ARGS"; }\n'+step['run']],
            cwd=self.runner,env=env,capture_output=True,text=True)
        self.assertEqual(dispatch.returncode,0,dispatch.stdout+dispatch.stderr)
        args=args_path.read_bytes().decode().split('\0')[:-1]
        self.assertEqual(args[args.index('-X')+1],'POST')
        self.assertIn('https://api.github.com/repos/artbooms/artbooms-pwa-memory/actions/workflows/update-memory.yml/dispatches',args)
        self.assertEqual(json.loads(args[args.index('-d')+1]),{'ref':'main'})
        self.assertIn('Authorization: Bearer offline-test-token',args)
        self.assertNotIn('--force',self.script)
        syntax=subprocess.run(['bash','-n'],input=self.script,text=True,capture_output=True)
        self.assertEqual(syntax.returncode,0,syntax.stderr)


if __name__=='__main__':unittest.main(verbosity=2)
