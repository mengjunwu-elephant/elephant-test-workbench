import time

from tests.support import device
from workbench.worker import execute_request


def test_debug_returns_without_waiting_or_restoring_and_keeps_exception_local():
    from workbench.engine import Control
    adapter=device()
    adapter.moving=lambda: True
    events=[]
    summary=execute_request(adapter,{'request':{'kind':'debug','api':'set_angle','args':[1,30,20],'timeout':.01}},events.append,Control())
    assert summary['state']=='completed' and not summary['blocked']
    failed=execute_request(adapter,{'request':{'kind':'debug','api':'set_angle','args':['bad',30,20]}},events.append,Control())
    assert failed['state']=='failed' and failed['blocked'] is False
    again=execute_request(adapter,{'request':{'kind':'debug','api':'get_angles_info'}},events.append,Control())
    assert again['state']=='completed'
    assert not any(e.get('state')=='restoring' for e in events)


def test_version_fields_keep_unsupported_and_failure_distinct():
    adapter=device()
    adapter.device.get_system_version=lambda: 1.2
    adapter.device.get_atom_version=lambda: None
    adapter.allowed.update({'get_system_version','get_atom_version'})
    info=adapter.versions()
    assert info['fields']['controller']['value']==1.2
    assert info['fields']['tool']['status']=='error'
    assert info['fields']['screen']['status']=='unsupported'
    assert info['read_at']


from tests.test_api import client, start, wait_task


def connected(client, task_id):
    deadline=time.monotonic()+6
    while time.monotonic()<deadline:
        row=client.get('/api/tasks/'+task_id).json()
        if row['state']=='connected':
            return row
        time.sleep(.03)
    raise AssertionError(row)


def test_transient_invalid_readback_keeps_same_session_and_debug_exception_does_not_disconnect(client,tmp_path):
    (tmp_path/'test-provider'/'pymycobot.py').write_text('from tests.support import FakeRobot\nclass UltraArmP1(FakeRobot):\n reads=0\n def get_angles_info(self):\n  self.reads+=1\n  return None if self.reads==2 else super().get_angles_info()\n def get_system_version(self): return 1.2\n')
    tid=start(client,{'kind':'connection'}).json()['id']
    row=connected(client,tid)
    process=client.app.state.manager.processes[tid]
    deadline=time.monotonic()+4
    while time.monotonic()<deadline:
        events=client.get('/api/tasks/'+tid+'/events').json()
        if any(e.get('state')=='degraded' for e in events):
            break
        time.sleep(.05)
    assert any(e.get('state')=='degraded' for e in events)
    row=connected(client,tid)
    assert row['summary']['device_info']['fields']['controller']['value']==1.2
    bad=start(client,{'kind':'debug','api':'set_angle','args':['bad',10,20]}).json()['id']
    assert wait_task(client,bad)['state']=='failed'
    connected(client,tid)
    good=start(client,{'kind':'debug','api':'get_angles_info'}).json()['id']
    assert wait_task(client,good)['state']=='completed'
    assert client.app.state.manager.processes[tid] is process
    assert client.post('/api/tasks/'+tid+'/control',json={'action':'disconnect'}).status_code==200
    assert wait_task(client,tid)['state']=='completed'


def test_reconnect_revalidates_failed_session_without_manual_unlock(client,tmp_path):
    provider=tmp_path/'test-provider'/'pymycobot.py'
    provider.write_text('from tests.support import FakeRobot\nclass UltraArmP1(FakeRobot):\n def get_angles_info(self): return None\n')
    old=start(client,{'kind':'connection'}).json()['id']
    assert wait_task(client,old)['state']=='blocked'
    provider.write_text('from tests.support import FakeRobot as UltraArmP1\n')
    response=start(client,{'kind':'connection'})
    assert response.status_code==201,response.text
    new=response.json()['id']
    connected(client,new)
    assert client.get('/api/tasks/'+old).json()['state']=='blocked'
    assert any(e['type']=='reconnect_requested' for e in client.get('/api/tasks/'+old+'/events').json())
    assert client.post('/api/locks/acknowledge',json={'confirmed_safe':True}).status_code in (404,405)
    client.post('/api/tasks/'+new+'/control',json={'action':'disconnect'})
    assert wait_task(client,new)['state']=='completed'


def test_established_session_keeps_debug_channel_when_motion_readback_is_unavailable(client,tmp_path):
    (tmp_path/'test-provider'/'pymycobot.py').write_text('from tests.support import FakeRobot\nclass UltraArmP1(FakeRobot):\n reads=0\n def get_angles_info(self):\n  self.reads+=1\n  return super().get_angles_info() if self.reads==1 else None\n def get_system_version(self): return 2.3\n')
    tid=start(client,{'kind':'connection'}).json()['id']
    connected(client,tid)
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        events=client.get('/api/tasks/'+tid+'/events').json()
        if sum(e.get('state')=='degraded' for e in events)>=4:
            break
        time.sleep(.05)
    row=client.get('/api/tasks/'+tid).json()
    assert row['state']=='degraded' and row['telemetry']['quality']=='error'
    assert sum(e.get('state')=='degraded' for e in events)>=4
    response=start(client,{'kind':'debug','api':'get_system_version'})
    assert response.status_code==201,response.text
    assert wait_task(client,response.json()['id'])['summary']['result']==2.3
    client.post('/api/tasks/'+tid+'/control',json={'action':'disconnect'})
    assert wait_task(client,tid)['state']=='completed'
