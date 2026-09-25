from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from sqlalchemy import select
from co4.models import Device, Lease, Member, Project, User, Work
from co4.domain import ACTIVE
from conftest import device_client, poll


def test_offline_auth_and_csrf(app, clients):
    anonymous = TestClient(app)
    assert anonymous.get('/api/dashboard').status_code == 401
    assert anonymous.post('/auth/demo', json={'login':'not-an-identity'}).status_code == 400
    c = clients['maintainer']
    p = c.get('/api/dashboard').json()['projects'][0]
    assert c.post(f"/api/projects/{p['id']}/watch", json={'enabled':True}, headers={'X-Co4-CSRF':''}).status_code == 403
    assert c.post(f"/api/projects/{p['id']}/watch", json={'enabled':True}, headers={'Origin':'https://evil.invalid'}).status_code == 403


def test_readiness_verified_access_and_label_rules(app, clients):
    c, info = device_client(app, clients['contributor'])
    job = poll(c)
    assert job['work']['number'] == 41  # Breaking change #44 is maintainer-only.
    assert job['lease']['state'] == 'running'
    with app.state.db.transaction() as s:
        m = s.get(Member, (job['project']['id'], job['lease']['user_id']))
        m.verified = False
        app.state.coordinator.release(s, s.get(Lease, job['lease']['id']))
    assert poll(c)['lease'] is None


def test_manual_consent_before_execution_and_no_device_approval(app, clients):
    worker, _ = device_client(app, clients['contributor'], autonomy='approve_each')
    job = poll(worker); l = job['lease']
    assert l['state'] == 'offered'
    assert worker.post(f"/api/worker/leases/{l['id']}/heartbeat",json={'generation':l['generation'],'phase':'baseline'}).status_code == 409
    assert worker.post(f"/api/leases/{l['id']}/accept",json={}).status_code == 401
    assert clients['contributor'].post(f"/api/leases/{l['id']}/accept",json={}).status_code == 200
    assert poll(worker)['lease']['state'] == 'running'
    assert worker.post(f"/api/leases/{l['id']}/approve",json={'sha':'a'*40,'review_digest':'b'*64,'confirm_reviewed':True}).status_code == 401


def test_declining_work_does_not_immediately_reallocate_it(app, clients):
    worker, _ = device_client(app, clients['contributor'])
    job = poll(worker)
    assert clients['backup'].post(f"/api/leases/{job['lease']['id']}/deny",json={}).status_code == 403
    assert clients['contributor'].post(f"/api/leases/{job['lease']['id']}/deny",json={}).status_code == 200
    assert poll(worker)['lease'] is None


def test_atomic_allocation_across_concurrent_workers(app, clients):
    # File-backed SQLite tests real DB locking across independent HTTP requests/connections.
    workers = [device_client(app, clients['contributor' if i % 2 else 'backup'])[0] for i in range(12)]
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(poll, workers))
    allocated = [x['lease'] for x in results if x['lease']]
    assert len(allocated) == 1
    with app.state.db.read() as s:
        assert len(list(s.scalars(select(Lease).where(Lease.state.in_(ACTIVE))))) == 1


def test_local_allowlist_and_preferred_labels(app, clients):
    worker, _ = device_client(app, clients['contributor'], labels=['documentation'])
    assert poll(worker)['lease'] is None  # Matching documentation is not validated.
    worker2, _ = device_client(app, clients['backup'])
    assert poll(worker2, ['unapproved/repository'])['lease'] is None


def test_one_active_allocation_per_person_across_devices(app, clients):
    w1, _ = device_client(app, clients['maintainer'])
    w2, _ = device_client(app, clients['maintainer'])
    first = poll(w1)
    assert first['lease'] is not None
    assert poll(w2)['lease'] is None
    assert poll(w1)['lease']['id'] == first['lease']['id']


def test_budget_reservation_and_unknown_usage_conservative_accounting(app, clients):
    with app.state.db.transaction() as s:
        p = s.scalar(select(Project)); p.budget_tokens = 100_000
    worker, _ = device_client(app, clients['contributor'])
    job = poll(worker)
    assert job['project']['reserved_tokens'] == 100_000
    worker2, _ = device_client(app, clients['maintainer'])
    assert poll(worker2)['lease'] is None
    clients['contributor'].post(f"/api/leases/{job['lease']['id']}/deny",json={})
    p = clients['maintainer'].get('/api/dashboard').json()['projects'][0]
    assert p['spent_tokens'] == 100_000 and p['reserved_tokens'] == 0
    assert poll(worker2)['lease'] is None


def test_unaccepted_offer_costs_no_consumption(app, clients):
    worker, _ = device_client(app, clients['contributor'], autonomy='approve_each')
    job=poll(worker)
    clients['contributor'].post(f"/api/leases/{job['lease']['id']}/deny",json={})
    p=clients['maintainer'].get('/api/dashboard').json()['projects'][0]
    assert p['spent_tokens']==0 and p['reserved_tokens']==0


def test_token_cap_blocks_process_and_usage_cannot_decrease(app, clients):
    worker,_=device_client(app,clients['contributor'],max_tokens=1000)
    job=poll(worker);l=job['lease'];url=f"/api/worker/leases/{l['id']}/heartbeat"
    payload={'generation':l['generation'],'phase':'baseline','usage':{'input_tokens':900,'output_tokens':1}}
    assert worker.post(url,json=payload).status_code==200
    payload['usage']['input_tokens']=800
    assert worker.post(url,json=payload).status_code==409
    payload['usage']['input_tokens']=999
    response=worker.post(url,json=payload)
    assert response.status_code==200 and response.json()['stop']


def test_phase_skips_and_cross_device_updates_rejected(app,clients):
    worker,_=device_client(app,clients['contributor']);job=poll(worker);l=job['lease']
    data={'generation':l['generation'],'phase':'ready'}
    assert worker.post(f"/api/worker/leases/{l['id']}/heartbeat",json=data).status_code==409
    other,_=device_client(app,clients['backup'])
    data['phase']='baseline'
    assert other.post(f"/api/worker/leases/{l['id']}/heartbeat",json=data).status_code==403


def test_policy_change_revokes_worker_and_revalidation_required(app,clients):
    worker,_=device_client(app,clients['contributor']);job=poll(worker);p=job['project'];l=job['lease']
    p['policy']['test_profile']='changed'
    payload={'policy':p['policy'],'budget_tokens':p['budget_tokens'],'active':True}
    assert clients['contributor'].put(f"/api/projects/{p['id']}",json=payload).status_code==403
    assert clients['maintainer'].put(f"/api/projects/{p['id']}",json=payload).status_code==200
    assert worker.post(f"/api/worker/leases/{l['id']}/heartbeat",json={'generation':1,'phase':'baseline'}).status_code==409
    state=clients['maintainer'].get('/api/dashboard').json()
    assert next(w for w in state['work'] if w['number']==41)['state']=='validation_pending'


def test_owner_role_cannot_be_demoted_and_contributor_cannot_self_verify(app,clients):
    pid=clients['maintainer'].get('/api/dashboard').json()['projects'][0]['id']
    payload={'login':'contributor','role':'maintainer','verified':True}
    assert clients['contributor'].put(f'/api/projects/{pid}/members',json=payload).status_code==403
    payload['login']='maintainer'
    assert clients['maintainer'].put(f'/api/projects/{pid}/members',json=payload).status_code==409


def test_private_project_is_not_visible_without_membership(app,clients):
    with app.state.db.transaction() as s:
        owner=s.scalar(select(User).where(User.login=='maintainer'))
        p=Project(repository='private-org/secret',repository_id=999,installation_id=3001,owner_id=owner.id,private=True,policy={})
        s.add(p);s.flush();s.add(Member(project_id=p.id,user_id=owner.id,role='owner',verified=True))
        w=Work(project_id=p.id,number=1,title='Secret request');s.add(w);s.flush();wid=w.id;pid=p.id
    assert len(clients['contributor'].get('/api/dashboard').json()['projects'])==1
    assert clients['contributor'].get(f'/api/work/{wid}').status_code==403
    assert clients['contributor'].post(f'/api/projects/{pid}/watch',json={'enabled':True}).status_code==403


def test_device_pause_and_revoke_fail_closed(app,clients):
    worker,info=device_client(app,clients['contributor']);job=poll(worker);l=job['lease']
    assert clients['backup'].put(f"/api/devices/{info['id']}",json={'enabled':False}).status_code==403
    assert clients['contributor'].put(f"/api/devices/{info['id']}",json={'enabled':False}).status_code==200
    assert worker.post(f"/api/worker/leases/{l['id']}/heartbeat",json={'generation':1,'phase':'baseline'}).status_code==409
    assert clients['contributor'].delete(f"/api/devices/{info['id']}").status_code==200
    assert worker.post('/api/worker/poll',json={'repositories':['co4-demo/tiny-library']}).status_code==401


def test_openapi_and_health_are_available(app):
    with TestClient(app) as c:
        assert c.get('/healthz').json()['status']=='ok'
        assert c.get('/api/openapi.json').status_code==200
        assert 'text/html' in c.get('/').headers['content-type']
