import copy
import time
import os
import sys
import subprocess
from tests.support import seed_cases

import pytest
from fastapi.testclient import TestClient

from workbench.api import create_app
from workbench.tasks import resource_keys


@pytest.fixture
def client(tmp_path, monkeypatch):
    # A test-only module injected into the spawned interpreter, never a product mode.
    sdk=tmp_path/'test-provider';sdk.mkdir()
    (sdk/'pymycobot.py').write_text('from tests.support import FakeRobot as UltraArmP1\n')
    from workbench.tasks import TaskManager
    original_popen=subprocess.Popen
    def popen(command, **kwargs):
        if 'workbench.worker' in command:
            kwargs['env']={**kwargs['env'], 'PYTHONPATH':os.pathsep.join([str(sdk),str(__import__('pathlib').Path(__file__).resolve().parents[1]),kwargs['env']['PYTHONPATH']])}
        return original_popen(command, **kwargs)
    monkeypatch.setattr(subprocess,'Popen',popen)
    monkeypatch.setattr(TaskManager,'resolve_environment',lambda self,request:(sys.executable,{'source':'test-double','sdk_version':'0.0.0'}))
    with TestClient(create_app(tmp_path/'app')) as c:
        for doc in seed_cases():
            assert c.post('/api/cases',json=doc).status_code==201
        yield c


def start(client, body):
    return client.post('/api/tasks',json={**body,'connection':{'mode':'hardware','model':'ultraarm_p1','address':'TEST-ONLY'},'hardware_confirmed':True})


def wait_task(client, task_id):
    end = time.monotonic()+10
    while time.monotonic()<end:
        task = client.get(f"/api/tasks/{task_id}").json()
        if task["state"] in {"completed", "failed", "stopped", "blocked"}:
            return task
        time.sleep(0.03)
    raise AssertionError("task timeout")


def test_real_worker_persists_events_and_frozen_case_snapshot(client):
    document = next(d for d in client.get('/api/cases').json() if d['id']=='demo-angle')
    response=start(client,{'case_ids':['demo-angle']})
    assert response.status_code==201,response.text
    task_id=response.json()['id']
    document['name']='用户后续修改的名字'
    assert client.put('/api/cases/demo-angle',json=document).status_code==200
    task=wait_task(client,task_id)
    assert task['state']=='completed', task
    assert task['snapshot']['cases'][0]['name'] != document['name']
    assert task['summary']['counts']['passed']==1
    events=client.get(f'/api/tasks/{task_id}/events').json()
    assert any(x['type']=='telemetry' for x in events)
    assert any(x['type']=='case_result' for x in events)
    assert client.get(f'/api/tasks/{task_id}/export').content[:2]==b'PK'


def test_device_busy_and_manual_confirmation(client):
    case=copy.deepcopy(client.get('/api/cases').json()[0])
    case.update(id='confirmation',name='等待操作',steps=[{'id':'confirm','name':'确认','kind':'confirm','message':'确认现场','timeout':5}])
    assert client.post('/api/cases',json=case).status_code==201
    tid=start(client,{'case_ids':['confirmation']}).json()['id']
    assert start(client,{'case_ids':['demo-read']}).status_code==409
    end=time.monotonic()+4
    while time.monotonic()<end:
        task=client.get('/api/tasks/'+tid).json()
        if task['state']=='waiting_confirmation':break
        time.sleep(.03)
    prompt=task['summary']['confirmation']
    assert client.post('/api/tasks/'+tid+'/control',json={'action':'confirm','id':'bad','answer':'continue'}).status_code==409
    assert client.post('/api/tasks/'+tid+'/control',json={'action':'confirm','id':prompt['id'],'answer':'continue'}).status_code==200
    assert wait_task(client,tid)['state']=='completed'


def test_model_does_not_bypass_same_port_and_dual_arm_overlap():
    a={'mode':'hardware','model':'ultraarm_p1','address':'COM3'}
    b={'mode':'hardware','model':'mercury','address':'com3','second_address':'COM4'}
    assert set(resource_keys(a)) & set(resource_keys(b))


def test_local_origin_protection_and_hardware_gate(client):
    assert client.post('/api/tasks',headers={'Origin':'https://unrelated.example'},json={'case_ids':['demo-read']}).status_code==403
    response=client.post('/api/tasks',json={'connection':{'mode':'hardware','model':'ultraarm_p1','address':'COM3'},'case_ids':['demo-read']})
    assert response.status_code==400
    assert '确认' in response.json()['detail']


def test_case_revision_conflict(client):
    document=client.get('/api/cases').json()[0]
    assert client.put('/api/cases/'+document['id'],json=document).status_code==200
    assert client.put('/api/cases/'+document['id'],json=document).status_code==409


def test_removed_product_paths_are_rejected(client):
    assert client.get('/api/legacy/cases').status_code==404
    assert client.get('/api/legacy/excel').status_code==404
    assert client.put('/api/settings/legacy',json={'root':'SHOULD-NOT-BE-READ'}).status_code==400
    assert client.post('/api/tasks',json={'kind':'legacy'}).status_code==422
    assert client.post('/api/tasks',json={'connection':{'mode':'simulation'}}).status_code==422
    assert client.put('/api/settings/connection',json={'mode':'simulation'}).status_code==422
    assert client.get('/api/apis?mode=simulation').status_code==422


def test_drafts_concurrent_edits_do_not_silently_overwrite(client):
    from concurrent.futures import ThreadPoolExecutor
    doc=client.get('/api/cases').json()[0]
    a,b=copy.deepcopy(doc),copy.deepcopy(doc)
    a['name']='编辑者 A';b['name']='编辑者 B'
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda d:client.put('/api/cases/'+d['id'],json=d),[a,b]))
    assert sorted(r.status_code for r in responses)==[200,409]


def test_aging_pause_at_cycle_boundary_then_stop_restores(client):
    tid=start(client,{'kind':'aging','case_ids':['demo-angle'],'repeat':100,'duration_seconds':60,'interval_ms':5}).json()['id']
    assert client.post('/api/tasks/'+tid+'/control',json={'action':'pause'}).status_code==200
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        task=client.get('/api/tasks/'+tid).json()
        if task['state']=='paused':break
        time.sleep(.03)
    assert task['state']=='paused'
    assert client.post('/api/tasks/'+tid+'/control',json={'action':'stop'}).status_code==200
    assert wait_task(client,tid)['state']=='stopped'
    assert client.get('/api/locks').json()==[]


def test_instance_lock_and_consistent_backup(client,tmp_path):
    with pytest.raises(RuntimeError,match='已有本机服务'):
        create_app(tmp_path/'app')
    result=client.post('/api/backups')
    assert result.status_code==200,result.text
    archive=client.get(result.json()['download'])
    import zipfile,io
    with zipfile.ZipFile(io.BytesIO(archive.content)) as z:
        assert 'workbench.sqlite3' in z.namelist() and 'manifest.json' in z.namelist()


def test_fresh_app_has_no_seed_data_and_never_claims_connected(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assert client.get('/api/cases').json()==[]
        assert client.get('/api/tasks').json()==[]
        settings=client.get('/api/settings').json()
        assert settings['connection']['mode']=='hardware'
        assert settings['connection']['address']==''
        assert 'legacy' not in settings
        assert client.get('/api/apis').status_code==400
        assert client.post('/api/tasks',json={'kind':'connection','hardware_confirmed':True}).status_code==400
        response=client.post('/api/tasks',json={'kind':'connection','connection':{'address':'TEST-ONLY'},'hardware_confirmed':True})
        assert response.status_code==400 and 'SDK' in response.json()['detail']
        assert client.get('/api/tasks').json()==[]


def test_persistent_connection_validates_reuses_one_worker_and_disconnects(client):
    response=start(client,{'kind':'connection'})
    assert response.status_code==201,response.text
    connection_id=response.json()['id']
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        connection=client.get('/api/tasks/'+connection_id).json()
        if connection['state']=='connected':break
        time.sleep(.03)
    assert connection['state']=='connected',connection
    assert connection['telemetry']['quality']=='ok'
    manager=client.app.state.manager
    process=manager.processes[connection_id]
    # No second connection is created, and another connect request is rejected.
    assert start(client,{'kind':'connection'}).status_code==409
    first=start(client,{'kind':'debug','api':'get_angles_info'}).json()['id']
    assert manager.processes[first] is process
    assert wait_task(client,first)['state']=='completed'
    deadline=time.monotonic()+2
    while client.get('/api/tasks/'+connection_id).json()['state']!='connected' and time.monotonic()<deadline:time.sleep(.02)
    second=start(client,{'case_ids':['demo-angle']}).json()['id']
    assert manager.processes[second] is process
    assert wait_task(client,second)['summary']['counts']['passed']==1
    deadline=time.monotonic()+2
    while client.get('/api/tasks/'+connection_id).json()['state']!='connected' and time.monotonic()<deadline:time.sleep(.02)
    assert client.post('/api/tasks/'+connection_id+'/control',json={'action':'disconnect'}).status_code==200
    assert wait_task(client,connection_id)['state']=='completed'
    assert client.get('/api/locks').json()==[]


def test_obsolete_preview_is_archived_without_reading_other_project(tmp_path):
    from workbench.storage import Store,Case,Task,Setting
    store=Store(tmp_path)
    with store.session() as session:
        session.add(Case(id='demo-read',document=seed_cases()[0]))
        custom={**seed_cases()[1],'id':'personal','name':'自己的用例','revision':2}
        session.add(Case(id='personal',document=custom))
        session.add(Setting(key='legacy',value={'root':'NONEXISTENT-DO-NOT-READ'}))
        session.add(Setting(key='connection',value={'mode':'simulation','model':'ultraarm_p1','address':''}))
        session.add(Task(id='old-preview',kind='debug',resource='simulation:ultraarm_p1',request={'connection':{'mode':'simulation'}},snapshot={}))
    store.engine.dispose()
    with TestClient(create_app(tmp_path)) as client:
        assert [doc['id'] for doc in client.get('/api/cases').json()]==['personal']
        assert client.get('/api/tasks').json()==[]
        assert client.get('/api/settings').json()['connection']['mode']=='hardware'
        archive=__import__('json').loads((tmp_path/'archives/obsolete-preview-v1.json').read_text(encoding='utf-8'))
        assert archive['tasks'][0]['id']=='old-preview'
        assert archive['settings'][0]['value']['root']=='NONEXISTENT-DO-NOT-READ'
        response=client.post('/api/backups')
        import io,zipfile
        with zipfile.ZipFile(io.BytesIO(client.get(response.json()['download']).content)) as backup:
            assert 'archives/obsolete-preview-v1.json' in {name.replace('\\','/') for name in backup.namelist()}


def test_constructor_timeout_finishes_task_instead_of_leaking_worker(client,tmp_path):
    provider=tmp_path/'test-provider'/'pymycobot.py'
    provider.write_text('import time\nclass UltraArmP1:\n def __init__(self,*a,**kw): time.sleep(60)\n')
    response=client.post('/api/tasks',json={'kind':'connection','connection':{'address':'TEST-ONLY','connect_timeout':.1},'hardware_confirmed':True})
    tid=response.json()['id']
    task=wait_task(client,tid)
    assert task['state']=='blocked' and '超' in task['summary']['error']
    deadline=time.monotonic()+2
    while tid in client.app.state.manager.processes and time.monotonic()<deadline:time.sleep(.03)
    assert tid not in client.app.state.manager.processes
    assert client.get('/api/locks').json()[0]['quarantined']


def test_connection_never_reports_connected_when_readback_is_invalid(client,tmp_path):
    (tmp_path/'test-provider'/'pymycobot.py').write_text('from tests.support import FakeRobot\nclass UltraArmP1(FakeRobot):\n def get_angles_info(self): return None\n')
    tid=start(client,{'kind':'connection'}).json()['id']
    task=wait_task(client,tid)
    assert task['state']=='blocked'
    assert '校验失败' in task['summary']['error']
    events=client.get('/api/tasks/'+tid+'/events').json()
    assert not any(event.get('state')=='connected' for event in events)


def test_real_numpy_is_loaded_before_blocking_stdin_reader(client,tmp_path):
    # Real DLL initialization + real child pipes reproduce the Windows failure;
    # only the device itself is replaced, so no hardware connection is made.
    (tmp_path/'test-provider'/'pymycobot.py').write_text('import numpy\nfrom tests.support import FakeRobot as UltraArmP1\n')
    tid=start(client,{'kind':'connection'}).json()['id']
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        task=client.get('/api/tasks/'+tid).json()
        if task['state']=='connected':break
        time.sleep(.03)
    assert task['state']=='connected',task
    events=client.get('/api/tasks/'+tid+'/events').json()
    assert [e['stage'] for e in events if e['type']=='connection_stage'][:2]==['sdk_import','sdk_initialize']
    assert client.post('/api/tasks/'+tid+'/control',json={'action':'disconnect'}).status_code==200
    assert wait_task(client,tid)['state']=='completed'


def test_sdk_import_timeout_is_not_misreported_as_robot_state_unknown(client,tmp_path):
    (tmp_path/'test-provider'/'pymycobot.py').write_text('import time\ntime.sleep(60)\n')
    response=client.post('/api/tasks',json={'kind':'connection','connection':{'address':'TEST-ONLY','connect_timeout':.1},'hardware_confirmed':True})
    task=wait_task(client,response.json()['id'])
    assert task['state']=='failed' and task['summary']['blocked'] is False
    assert task['summary']['connection_stage']=='sdk_import'
    assert 'SDK' in task['summary']['error']
    assert client.get('/api/locks').json()==[]
