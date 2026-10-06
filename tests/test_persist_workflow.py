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
        workflow=yaml.load((ROOT/'.github/workflows/persist_cache.yml').read_text(),Loader=yaml.BaseLoader)
        step=next(s for s in workflow['jobs']['persist']['steps'] if s.get('id')=='persist_cache')
        self.script=step['run']
        self.output=self.root/'outputs';self.output.touch()

    def run_step(self,data):
        cs.atomic_json(self.download/'articles_cache.json',data)
        env=os.environ.copy();env.update(RUNNER_TEMP=str(self.download),GITHUB_OUTPUT=str(self.output),
            PATH=str(Path(sys.executable).parent)+os.pathsep+env['PATH'],GIT_TERMINAL_PROMPT='0')
        return subprocess.run(['bash','--noprofile','--norc','-e','-o','pipefail','-c',self.script],cwd=self.runner,env=env,capture_output=True,text=True)

    def persisted(self):
        return json.loads(git(self.bare,'show','main:cache/articles_cache.json'))

    def test_fetch_latest_then_merge_and_push_only_cache(self):
        # Main advances after checkout, exactly the race the workflow must handle.
        manual=candidate(title='Correction already on main',_fetched_at='2026-10-02T12:00:00Z')
        cs.atomic_json(self.seed/'cache/articles_cache.json',cache(manual))
        git(self.seed,'add','.');git(self.seed,'commit','-m','manual correction');git(self.seed,'push','origin','main')
        incoming=cache(VALID,candidate(url=VALID['url']+'-new'))
        result=self.run_step(incoming)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        persisted=self.persisted()
        self.assertEqual(persisted['items'][VALID['url']],manual)
        self.assertIn(VALID['url']+'-new',persisted['items'])
        self.assertIn('updated=true',self.output.read_text())
        self.assertEqual(git(self.bare,'diff-tree','--no-commit-id','--name-only','-r','main'),'cache/articles_cache.json')

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

    def test_pwa_trigger_and_dispatch_schedule_are_preserved(self):
        data=yaml.load((ROOT/'.github/workflows/persist_cache.yml').read_text(),Loader=yaml.BaseLoader)
        self.assertEqual(set(data['on']),{'workflow_dispatch'})
        step=data['jobs']['persist']['steps'][-1]
        self.assertIn("updated == 'true'",step['if'])
        self.assertIn('artbooms/artbooms-pwa-memory/actions/workflows/update-memory.yml/dispatches',step['run'])
        self.assertNotIn('--force',self.script)
        syntax=subprocess.run(['bash','-n'],input=self.script,text=True,capture_output=True)
        self.assertEqual(syntax.returncode,0,syntax.stderr)


if __name__=='__main__':unittest.main(verbosity=2)
