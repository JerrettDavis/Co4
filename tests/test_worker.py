from pathlib import Path
import json
import os
import sys
import time
import pytest
from sqlalchemy import select
from co4.adapters import UsageMeter, aggregate, invocation
from co4.gitops import GitError, Repository
from co4.models import Event, Lease, Work
from co4.process import ExecutionStopped, run_process
from co4.worker import Worker, WorkerError, harness_environment, init_demo_source
from conftest import device_client, poll


def configured(app,clients,tmp_path,monkeypatch):
    client,info=device_client(app,clients['contributor'])
    source=tmp_path/'source';init_demo_source(source)
    cfg={'server':'http://testserver','root':str(tmp_path/'worker'),'demo':True,'token_env':'CO4_DEVICE_TOKEN',
         'acknowledge_code_execution':True,'allow_checkpoint_push':True,'harnesses':['mock'],'pick_next':False,
         'repositories':{'co4-demo/tiny-library':{'push_repository':'co4-demo/tiny-library','local_source':str(source),
             'test_command':[sys.executable,'-m','unittest','discover','-v'],'test_globs':['test_*.py'],'test_profile':'default'}}}
    monkeypatch.setenv('CO4_DEVICE_TOKEN',info['token'])
    return Worker(cfg,client=client),client


def test_complete_offline_vertical_slice_uses_real_git_and_tests(app,clients,tmp_path,monkeypatch):
    worker,c=configured(app,clients,tmp_path,monkeypatch)
    job=poll(c);worker.execute(job)
    detail=clients['contributor'].get('/api/work/'+job['work']['id']).json()
    l=detail['leases'][-1]
    assert l['state']=='awaiting_review' and l['pr_url']==''
    assert l['evidence']['baseline_exit']==0 and l['evidence']['red_exit']==1
    assert l['evidence']['green_exit']==0 and l['evidence']['verify_exit']==0
    assert l['usage']['source']=='fixture'
    assert 'return sum(values) / len(values) if values else 0' in l['diff']
    events=clients['contributor'].get(f"/api/leases/{l['id']}/events").json()
    assert len([x for x in events if x['kind']=='prompt'])==3
    assert any('ZeroDivisionError' in x['text'] for x in events)
    assert clients['maintainer'].get(f"/api/leases/{l['id']}/events").status_code==403
    assert clients['backup'].get(f"/api/leases/{l['id']}/events").status_code==403
    wrong={'sha':'a'*40,'review_digest':l['review_digest'],'confirm_reviewed':True}
    assert clients['contributor'].post(f"/api/leases/{l['id']}/approve",json=wrong).status_code==409
    wrong['sha']=l['checkpoint']['sha'];wrong['review_digest']='b'*64
    assert clients['contributor'].post(f"/api/leases/{l['id']}/approve",json=wrong).status_code==409
    approved={'sha':l['checkpoint']['sha'],'review_digest':l['review_digest'],'confirm_reviewed':True}
    assert clients['contributor'].post(f"/api/leases/{l['id']}/approve",json=approved).json()['state']=='publishing'
    app.state.dispatcher.tick()
    completed=clients['contributor'].get('/api/work/'+job['work']['id']).json()['leases'][-1]
    assert completed['state']=='submitted'
    assert completed['pr_url'].startswith('/demo/')
    assert app.state.github.publications[0]['sha']==l['checkpoint']['sha']
    assert 'prompt' not in json.loads(app.state.github.publications[0]['body'].split('```json\n')[1].split('\n```')[0])
    before=len(app.state.github.publications);app.state.dispatcher.tick();assert len(app.state.github.publications)==before


def test_separate_maintainer_approval_required(app,clients,tmp_path,monkeypatch):
    from co4.models import Project
    with app.state.db.transaction() as s:
        p=s.scalar(select(Project));p.policy={**p.policy,'require_maintainer_approval':True}
    worker,c=configured(app,clients,tmp_path,monkeypatch);job=poll(c);worker.execute(job)
    l=clients['contributor'].get('/api/work/'+job['work']['id']).json()['leases'][-1]
    data={'sha':l['checkpoint']['sha'],'review_digest':l['review_digest'],'confirm_reviewed':True}
    assert clients['contributor'].post(f"/api/leases/{l['id']}/approve",json=data).json()['state']=='awaiting_review'
    app.state.dispatcher.tick();assert not app.state.github.publications
    assert clients['maintainer'].post(f"/api/leases/{l['id']}/approve",json=data).json()['state']=='publishing'
    app.state.dispatcher.tick();assert len(app.state.github.publications)==1


def test_green_cannot_change_the_red_tests(app,clients,tmp_path,monkeypatch):
    worker,c=configured(app,clients,tmp_path,monkeypatch);original=worker.mock
    def tamper(phase):
        original(phase)
        if phase=='green':
            (worker.repository.path/'test_average.py').write_text('import unittest\n')
    worker.mock=tamper
    job=poll(c)
    with pytest.raises(WorkerError,match='red-phase tests'):worker.execute(job)
    with app.state.db.read() as s:
        l=s.get(Lease,job['lease']['id']);assert l.state=='blocked' and not l.review_digest


def test_failing_baseline_does_not_start_harness(app,clients,tmp_path,monkeypatch):
    worker,c=configured(app,clients,tmp_path,monkeypatch)
    worker.repo_config=None
    worker.config['repositories']['co4-demo/tiny-library']['test_command']=[sys.executable,'-c','raise SystemExit(2)']
    job=poll(c)
    with pytest.raises(WorkerError,match='baseline is already failing'):worker.execute(job)
    with app.state.db.read() as s:
        assert not list(s.scalars(select(Event).where(Event.kind=='prompt')))


def test_local_transcript_encrypted_and_replay_idempotent(app,clients,tmp_path,monkeypatch):
    worker,c=configured(app,clients,tmp_path,monkeypatch);job=poll(c);worker.execute(job)
    content=worker.transcript.read_text()
    assert 'ZeroDivisionError' not in content and 'prompt' not in content
    entries=[worker.vault.open(line) for line in content.splitlines()]
    assert any(x['kind']=='prompt' for x in entries)


def test_process_idle_timeout_kills_process_group(tmp_path):
    ticks=[];start=time.monotonic()
    with pytest.raises(ExecutionStopped,match='idle watchdog'):
        run_process([sys.executable,'-c','import time;time.sleep(10)'],tmp_path,idle_seconds=.3,
                    timeout_seconds=2,tick_seconds=.05,on_tick=lambda:ticks.append(1))
    assert time.monotonic()-start<3 and ticks


def test_process_cancellation_on_heartbeat_failure(tmp_path):
    def denied():raise WorkerError('lease fenced')
    with pytest.raises(WorkerError,match='lease fenced'):
        run_process([sys.executable,'-c','import time;time.sleep(10)'],tmp_path,on_tick=denied,tick_seconds=.1)


def test_process_captures_stdout_stderr_and_exit(tmp_path):
    text=[]
    result=run_process([sys.executable,'-c','import sys;print("hello");print("error",file=sys.stderr);sys.exit(3)'],tmp_path,on_output=lambda c,t:text.append((c,t)))
    assert result.exit_code==3 and {x[0] for x in text}=={'stdout','stderr'}

@pytest.mark.parametrize('name',['claude','codex','copilot'])
def test_adapter_contracts_do_not_bypass_permissions(name):
    call=invocation(name,'safe test prompt')
    joined=' '.join(call.argv)
    assert 'bypass' not in joined and '--allow-all' not in joined and 'danger-full-access' not in joined
    assert call.argv[0]==name
    if name=='claude':assert '--output-format' in call.argv and 'stream-json' in call.argv and call.stdin
    if name=='codex':assert 'workspace-write' in call.argv and call.argv[-1]=='-'
    if name=='copilot':assert '--no-ask-user' in call.argv and '--deny-tool' in call.argv


def test_claude_usage_caches_are_not_lost_or_double_counted():
    meter=UsageMeter('claude')
    line=json.dumps({'type':'result','session_id':'session-1','usage':{'input_tokens':100,'output_tokens':20,'cache_read_input_tokens':300,'cache_creation_input_tokens':50},'total_cost_usd':.02})
    meter.feed(line);meter.feed(line)
    assert meter.input_tokens==450 and meter.output_tokens==20 and meter.cost_usd==.02
    assert meter.cached_input_tokens==300 and meter.complete
    assert meter.session_ids=={'session-1'}


def test_codex_usage_and_unknown_copilot_usage():
    codex=UsageMeter('codex');codex.feed('{"type":"turn.completed","usage":{"input_tokens":24763,"cached_input_tokens":24448,"output_tokens":122}}')
    assert codex.input_tokens==24763 and codex.cached_input_tokens==24448 and codex.complete
    copilot=UsageMeter('copilot');copilot.feed('{"type":"assistant.message","data":{"content":"done"}}')
    assert copilot.input_tokens is None and not copilot.complete and copilot.cost_usd is None


def test_aggregation_retains_partial_lower_bounds_but_not_fictitious_costs():
    usage=aggregate([{'input_tokens':100,'output_tokens':10,'cost_usd':.1,'complete':True},{'input_tokens':None,'output_tokens':None,'complete':False}],2)
    assert usage['input_tokens']==100 and usage['output_tokens']==10 and not usage['complete'] and usage['cost_usd'] is None


def test_harness_environment_excludes_device_and_git_credentials(monkeypatch):
    monkeypatch.setenv('CO4_DEVICE_TOKEN','secret-device');monkeypatch.setenv('CO4_GIT_TOKEN','secret-git')
    monkeypatch.setenv('GITHUB_APP_PRIVATE_KEY','secret-app');monkeypatch.setenv('PATH','/bin')
    env=harness_environment({'harness_env':['CO4_DEVICE_TOKEN','CO4_GIT_TOKEN','GITHUB_APP_PRIVATE_KEY']})
    assert env['PATH']=='/bin' and not any('secret' in v for v in env.values())


def test_git_checkpoint_rejects_secret_files_and_symlinks(tmp_path):
    source=tmp_path/'source';init_demo_source(source)
    repo=Repository(tmp_path/'checkout',demo=True)
    repo.prepare('co4-demo/tiny-library','co4-demo/tiny-library','co4/work/1/test','main',local_source=str(source))
    (repo.path/'private.pem').write_text('sensitive')
    with pytest.raises(GitError,match='secret file'):repo.checkpoint('co4/work/1/test','test','co4-demo/tiny-library')
    (repo.path/'private.pem').unlink()
    (repo.path/'escape').symlink_to(source)
    with pytest.raises(GitError,match='symlinks'):repo.checkpoint('co4/work/1/test','test','co4-demo/tiny-library')

@pytest.mark.parametrize('phase',['spec','red'])
def test_spec_and_red_phases_cannot_implement_production_code(app,clients,tmp_path,monkeypatch,phase):
    worker,c=configured(app,clients,tmp_path,monkeypatch);original=worker.mock
    def tamper(name):
        original(name)
        if name==phase:(worker.repository.path/'average.py').write_text('def average(values): return 0\n')
    worker.mock=tamper
    with pytest.raises(WorkerError,match='outside its permitted scope'):worker.execute(poll(c))


def test_recursive_test_glob_allows_zero_directory_levels(app,clients,tmp_path,monkeypatch):
    worker,c=configured(app,clients,tmp_path,monkeypatch);original=worker.mock
    worker.config['repositories']['co4-demo/tiny-library']['test_globs'].append('tests/**/*.py')
    def add_matching_file(name):
        original(name)
        if name=='red':
            folder=worker.repository.path/'tests';folder.mkdir()
            (folder/'test_extra.py').write_text('# Additional regression artifact matched by the configured recursive glob.\n')
    worker.mock=add_matching_file
    worker.execute(poll(c))
    assert worker.state['submitted_for_review']


def test_receipt_contains_change_and_verification_metrics_but_no_private_fields():
    from co4.domain import public_receipt
    l=Lease(id='a'*32,generation=2,checkpoint={'sha':'b'*40},usage={'input_tokens':10,'output_tokens':3,'complete':True},
        evidence={'red_exit':1,'green_exit':0,'profile':'default','notes':'private notes must not leave'},
        diff='diff --git a/code.py b/code.py\n--- a/code.py\n+++ b/code.py\n-old\n+new\n+extra\n')
    receipt=public_receipt(l)
    assert receipt['change_metrics']['files_changed']==1
    assert receipt['change_metrics']['lines_added']==2 and receipt['change_metrics']['lines_deleted']==1
    assert receipt['verification']['red_exit']==1 and receipt['allocation_generation']==2
    assert 'private notes' not in json.dumps(receipt) and 'device_id' not in receipt and 'user_id' not in receipt
