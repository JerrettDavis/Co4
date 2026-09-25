from co4.models import Lease, Work
from conftest import device_client, poll

def setup_job(app,clients):
    worker,info=device_client(app,clients['contributor'])
    other,other_info=device_client(app,clients['backup'])
    job=poll(worker)
    now=2_000_000_000.0
    app.state.coordinator.clock=lambda:now
    with app.state.db.transaction() as s:
        l=s.get(Lease,job['lease']['id']);l.last_contact=now-43200
    return worker,other,other_info,job,now


def test_dibs_require_twelve_full_hours(app,clients):
    worker,other,info,job,now=setup_job(app,clients)
    with app.state.db.transaction() as s:
        s.get(Lease,job['lease']['id']).last_contact+=1
    url=f"/api/leases/{job['lease']['id']}/dib"
    assert clients['backup'].post(url,json={'device_id':info['id']}).status_code==409
    app.state.coordinator.clock=lambda:now+1
    response=clients['backup'].post(url,json={'device_id':info['id']})
    assert response.status_code==200
    assert response.json()['recovery_deadline']==now+1+43200


def test_heartbeat_cannot_silently_cancel_a_dib(app,clients):
    worker,other,info,job,now=setup_job(app,clients);l=job['lease']
    assert clients['backup'].post(f"/api/leases/{l['id']}/dib",json={'device_id':info['id']}).status_code==200
    assert worker.post(f"/api/worker/leases/{l['id']}/heartbeat",json={'generation':1,'phase':'baseline'}).status_code==409
    assert clients['contributor'].post(f"/api/leases/{l['id']}/recover",json={}).status_code==200
    with app.state.db.read() as s:
        row=s.get(Lease,l['id']);assert row.state=='running' and row.dib_user is None


def test_reassignment_at_deadline_fences_old_owner_and_keeps_checkpoint(app,clients):
    worker,other,info,job,now=setup_job(app,clients);l=job['lease']
    cp={'repository':'co4-demo/tiny-library','branch':'co4/work/41/prior','sha':'a'*40}
    with app.state.db.transaction() as s:
        s.get(Work,job['work']['id']).checkpoint=cp
    clients['backup'].post(f"/api/leases/{l['id']}/dib",json={'device_id':info['id']})
    app.state.coordinator.clock=lambda:now+43200
    assert clients['contributor'].post(f"/api/leases/{l['id']}/recover",json={}).status_code==409
    new=poll(other)
    assert new['lease']['generation']==2
    assert new['lease']['user_id']!=l['user_id']
    assert new['work']['checkpoint']==cp
    assert worker.post(f"/api/worker/leases/{l['id']}/heartbeat",json={'generation':1,'phase':'baseline'}).status_code==409
    with app.state.db.read() as s:
        assert s.get(Lease,l['id']).state=='reassigned'


def test_no_automatic_reassignment_without_dibs(app,clients):
    worker,other,info,job,now=setup_job(app,clients)
    app.state.coordinator.clock=lambda:now+10*86400
    with app.state.db.transaction() as s:app.state.coordinator.sweep(s)
    assert poll(other)['lease'] is None
    with app.state.db.read() as s:assert s.get(Lease,job['lease']['id']).state=='running'
