"""Provider-boundary tests use HTTP mocks, not live GitHub credentials."""
import hashlib
import hmac
import json
from urllib.parse import parse_qs, urlparse
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from co4.github import GitHub, GitHubError
from co4.models import Delivery, Lease, OAuthState, Outbox, Project, Session, Work
from conftest import device_client, poll


def send_hook(app, data, kind='issues', delivery='delivery-1', signature=None):
    raw = json.dumps(data).encode()
    signature = signature or 'sha256=' + hmac.new(app.state.settings.webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
    with TestClient(app) as c:
        return c.post('/webhooks/github', content=raw, headers={'X-GitHub-Event':kind,
            'X-GitHub-Delivery':delivery,'X-Hub-Signature-256':signature,'Content-Type':'application/json'})


def issue_payload(number=90, action='opened', labels=None):
    return {'action':action,'installation':{'id':3001},'repository':{'id':2001},
            'sender':{'id':1001,'login':'maintainer'},
            'issue':{'number':number,'title':'A governed request','body':'Acceptance criteria','state':'open',
                     'labels':[{'name':x} for x in labels or []]}}


def test_webhook_signature_delivery_replay_and_validation_gate(app):
    data=issue_payload()
    assert send_hook(app,data,signature='sha256=invalid').status_code==401
    assert send_hook(app,data).status_code==200
    assert send_hook(app,data).json()=={'duplicate':True}
    with app.state.db.read() as s:
        assert len(list(s.scalars(select(Delivery))))==1
        assert s.scalar(select(Work).where(Work.number==90)).state=='validation_pending'


def test_webhook_checks_exact_installation_and_repository(app):
    data=issue_payload();data['installation']['id']=9999
    assert send_hook(app,data).json()['matched'] is False
    with app.state.db.read() as s:assert s.scalar(select(Work).where(Work.number==90)) is None


@pytest.mark.parametrize('action',['edited','closed','deleted'])
def test_changed_or_closed_issue_revokes_active_worker(app,clients,action):
    worker,_=device_client(app,clients['contributor']);job=poll(worker)
    data=issue_payload(41,action)
    if action=='closed':data['issue']['state']='closed'
    assert send_hook(app,data).status_code==200
    result=worker.post(f"/api/worker/leases/{job['lease']['id']}/heartbeat",json={'generation':1,'phase':'baseline'})
    assert result.status_code==409
    with app.state.db.read() as s:
        assert s.get(Lease,job['lease']['id']).state=='cancelled'
        assert s.get(Work,job['work']['id']).state==('validation_pending' if action=='edited' else 'closed')


def test_installation_removal_disables_project_and_execution(app,clients):
    worker,_=device_client(app,clients['contributor']);job=poll(worker)
    data={'action':'removed','installation':{'id':3001},'repositories_removed':[{'id':2001}]}
    assert send_hook(app,data,'installation_repositories').status_code==200
    with app.state.db.read() as s:
        assert s.scalar(select(Project)).active is False
        assert s.get(Lease,job['lease']['id']).state=='cancelled'


def test_github_comment_validation_requires_registered_triager(app):
    data=issue_payload(42)
    data['action']='created';data['comment']={'body':'/co4 validate'}
    data['sender']={'id':1002,'login':'contributor'}
    assert send_hook(app,data,'issue_comment','denied').status_code==200
    with app.state.db.read() as s:assert s.scalar(select(Work).where(Work.number==42)).state=='validation_pending'
    data['sender']={'id':1001,'login':'maintainer'}
    assert send_hook(app,data,'issue_comment','allowed').status_code==200
    with app.state.db.read() as s:assert s.scalar(select(Work).where(Work.number==42)).state=='queued'


def test_ready_label_requires_verified_github_permission(app,monkeypatch):
    app.state.settings.demo=False
    monkeypatch.setattr(app.state.github,'can_manage',lambda *args:False,raising=False)
    assert send_hook(app,issue_payload(labels=['co4:ready'])).status_code==200
    with app.state.db.read() as s:assert s.scalar(select(Work).where(Work.number==90)).state=='validation_pending'
    monkeypatch.setattr(app.state.github,'can_manage',lambda *args:True)
    assert send_hook(app,issue_payload(91,labels=['co4:ready']),delivery='trusted').status_code==200
    with app.state.db.read() as s:assert s.scalar(select(Work).where(Work.number==91)).state=='queued'


def test_oauth_state_cookie_single_use_and_encrypted_session(app,monkeypatch):
    app.state.settings.demo=False
    monkeypatch.setattr(app.state.github,'exchange',lambda code:'private-oauth-access',raising=False)
    monkeypatch.setattr(app.state.github,'user',lambda access:{'id':5555,'login':'signed-in-user'},raising=False)
    with TestClient(app,follow_redirects=False) as c:
        response=c.get('/auth/github');assert response.status_code==307
        state=parse_qs(urlparse(response.headers['location']).query)['state'][0]
        assert c.get('/auth/github/callback',params={'code':'valid','state':'wrong'}).status_code==400
        result=c.get('/auth/github/callback',params={'code':'valid','state':state})
        assert result.status_code==307 and c.cookies.get('co4_session')
        assert c.get('/api/bootstrap').json()['user']['login']=='signed-in-user'
        assert c.get('/auth/github/callback',params={'code':'valid','state':state}).status_code==400
    with app.state.db.read() as s:
        saved=s.scalar(select(Session));assert 'private-oauth-access' not in saved.github_token
        assert app.state.vault.open(saved.github_token)=='private-oauth-access'
        assert not list(s.scalars(select(OAuthState)))


def test_checkpoint_cannot_name_another_lease_branch(app,clients):
    worker,_=device_client(app,clients['contributor']);job=poll(worker)
    result=worker.post(f"/api/worker/leases/{job['lease']['id']}/checkpoint",json={'generation':1,
        'checkpoint':{'repository':'co4-demo/tiny-library','branch':'co4/work/41/not-this-lease','sha':'a'*40}})
    assert result.status_code==422


def test_outbox_retries_and_reconciles_without_claim_duplication(app,clients,monkeypatch):
    worker,_=device_client(app,clients['contributor']);poll(worker)
    def unavailable(*args):raise GitHubError('GitHub temporarily unavailable')
    monkeypatch.setattr(app.state.github,'status_comment',unavailable)
    app.state.dispatcher.tick()
    with app.state.db.transaction() as s:
        job=s.scalar(select(Outbox));assert job.attempts==1 and job.state=='pending'
        job.available=0
    monkeypatch.setattr(app.state.github,'status_comment',lambda *args:None)
    claim=app.state.dispatcher.claim();assert claim and app.state.dispatcher.claim() is None
    app.state.dispatcher.deliver(*claim)
    with app.state.db.read() as s:assert s.scalar(select(Outbox)).state=='done'


def gateway(app, handler, monkeypatch):
    gh=GitHub(app.state.settings,httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(gh,'app_token',lambda *args:'fake-installation-token')
    return gh


def test_github_publish_is_draft_sha_frozen_and_idempotent(app,monkeypatch):
    sha='a'*40;state={'ref':None,'pr':None,'created':0}
    def handler(req):
        path=req.url.path
        if path.endswith('/git/refs'):
            state['ref']={'object':{'sha':json.loads(req.content)['sha']}}
            return httpx.Response(201,json=state['ref'])
        if '/git/ref/heads/' in path:return httpx.Response(200 if state['ref'] else 404,json=state['ref'] or {})
        if path.endswith('/pulls') and req.method=='GET':return httpx.Response(200,json=[state['pr']] if state['pr'] else [])
        if path.endswith('/pulls') and req.method=='POST':
            data=json.loads(req.content);assert data['draft'] and data['head'].startswith('co4/submission/')
            assert data['maintainer_can_modify'] is False
            state['created']+=1;state['pr']={'html_url':'https://github.com/acme/repo/pull/1','head':{'sha':sha}}
            return httpx.Response(201,json=state['pr'])
        raise AssertionError(str(req.url))
    gh=gateway(app,handler,monkeypatch)
    project={'repository':'acme/repo','repository_id':1,'installation_id':2,'default_branch':'main'}
    result=gh.publish(project,'lease',sha,41,'Fix empty average','Evidence')
    assert gh.publish(project,'lease',sha,41,'Fix empty average','Evidence')==result and state['created']==1
    state['ref']['object']['sha']='b'*40
    with pytest.raises(GitHubError,match='drifted'):gh.publish(project,'lease',sha,41,'Fix','Evidence')


def test_github_checkpoint_is_verified_against_fork_network_and_remote_sha(app,monkeypatch):
    sha='a'*40;family={'id':10,'parent':{'id':1}}
    def handler(req):
        if req.url.path=='/repos/person/fork':return httpx.Response(200,json=family)
        if '/git/ref/' in req.url.path:return httpx.Response(200,json={'object':{'sha':sha}})
        if '/compare/' in req.url.path:
            assert req.headers['accept']=='application/vnd.github.diff'
            return httpx.Response(200,text='server-derived-diff')
        raise AssertionError(str(req.url))
    gh=gateway(app,handler,monkeypatch)
    project={'repository':'acme/repo','repository_id':1,'installation_id':2,'default_branch':'main'}
    cp={'repository':'person/fork','branch':'co4/work/41/lease','sha':sha}
    assert gh.inspect_checkpoint(project,cp,with_diff=True)=='server-derived-diff'
    with pytest.raises(GitHubError,match='moved'):gh.inspect_checkpoint(project,{**cp,'sha':'b'*40})
    family['parent']['id']=999
    with pytest.raises(GitHubError,match='fork network'):gh.inspect_checkpoint(project,cp)


def test_large_body_is_rejected_before_parsing(app):
    with TestClient(app) as c:
        assert c.post('/webhooks/github',content=b'x'*3_000_001).status_code==413
