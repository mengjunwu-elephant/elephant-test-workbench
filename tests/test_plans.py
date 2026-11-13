import copy
import json
from pathlib import Path

from sqlalchemy import select

from tests.test_api import client, start, wait_task
from tests.support import device
from workbench.engine import Engine, Control
from workbench.case_repository import CaseRepository
from workbench.storage import Case, Plan, Store
from workbench.tasks import TaskManager


def make_plan(client):
    body={'id':'ordered-plan','name':'按序重复','model':'ultraarm_p1','items':[{'case_id':'demo-read'},{'case_id':'demo-angle','overrides':{'angle':12}},{'case_id':'demo-read'}]}
    result=client.post('/api/plans',json=body)
    assert result.status_code==201,result.text
    return result.json()


def test_plan_freezes_versions_order_duplicates_and_effective_parameters(client):
    plan=make_plan(client)
    assert len({item['item_id'] for item in plan['items']})==3
    original=next(c for c in client.get('/api/cases').json() if c['id']=='demo-read')
    old_name=original['name']
    original['name']='后续编辑不改变计划'
    assert client.put('/api/cases/demo-read',json=original).status_code==200
    preview=client.post('/api/tasks/preview',json={'kind':'plan','plan_id':plan['id'],'connection':{'model':'ultraarm_p1','address':'TEST-ONLY'}}).json()
    assert preview['ready'] and preview['instance_count']==3
    assert [c['id'] for c in preview['cases']]==['demo-read','demo-angle','demo-read']
    assert preview['cases'][0]['name']==old_name
    assert preview['cases'][1]['datasets'][0]['angle']==12
    assert client.get('/api/tasks').json()==[]
    tid=start(client,{'kind':'plan','plan_id':plan['id'],'plan_revision':plan['revision'],'expected_fingerprint':preview['fingerprint']}).json()['id']
    task=wait_task(client,tid)
    assert task['state']=='completed',task
    assert [r['case_id'] for r in task['summary']['results']]==['demo-read','demo-angle','demo-read']
    assert task['summary']['results'][0]['entry']['item_id']!=task['summary']['results'][2]['entry']['item_id']
    assert task['snapshot']['provenance']['source']=='local_draft'
    assert task['snapshot']['provenance']['asset_commit'] is None


def test_preflight_token_detects_changes_and_unavailable_current_reference(client):
    plan=make_plan(client)
    body={'kind':'plan','plan_id':plan['id'],'connection':{'model':'ultraarm_p1','address':'TEST-ONLY'}}
    preview=client.post('/api/tasks/preview',json=body).json()
    plan['name']='更新计划'
    newer=client.put('/api/plans/'+plan['id'],json=plan).json()
    assert client.put('/api/plans/'+plan['id'],json=plan).status_code==409
    changed=start(client,{'kind':'plan','plan_id':plan['id'],'expected_fingerprint':preview['fingerprint']})
    assert changed.status_code==409,changed.text
    doc=next(c for c in client.get('/api/cases').json() if c['id']=='demo-read')
    doc['enabled']=False
    assert client.put('/api/cases/demo-read',json=doc).status_code==200
    blocked=client.post('/api/tasks/preview',json=body).json()
    assert blocked['ready'] is False
    assert client.get('/api/tasks').json()==[]
    assert newer['revision']==2


def test_unsaved_inline_content_rejected_and_requested_case_order_preserved(client):
    doc=next(c for c in client.get('/api/cases').json() if c['id']=='demo-read')
    doc['name']='未保存'
    response=start(client,{'inline_cases':[doc]})
    assert response.status_code==409
    preview=client.post('/api/tasks/preview',json={'kind':'cases','case_ids':['demo-angle','demo-read','demo-angle'],'connection':{'address':'TEST-ONLY'}}).json()
    assert [c['id'] for c in preview['cases']]==['demo-angle','demo-read','demo-angle']
    assert client.get('/api/tasks').json()==[]


def test_plan_files_roundtrip_portable_snapshots_and_conflicts(client,tmp_path):
    plan=make_plan(client)
    repo=client.app.state.repository
    repo.library=tmp_path/'library';repo.checkout=tmp_path
    repo.save()
    assert (repo.library/'plans'/f"{plan['id']}.json").is_file()
    other_store=Store(tmp_path/'other-data')
    other=CaseRepository(other_store,TaskManager(other_store),repo.library,tmp_path)
    assert other.load()['plans_loaded']==1
    with other_store.session() as s:
        copied=s.get(Plan,plan['id']).document
        assert copied['definitions']==plan['definitions']
        assert len(list(s.scalars(select(Case))))==3
    path=repo.library/'plans'/f"{plan['id']}.json"
    remote=json.loads(path.read_text(encoding='utf-8'));remote['name']='磁盘变化'
    path.write_text(json.dumps(remote),encoding='utf-8')
    plan['name']='本机变化'
    assert client.put('/api/plans/'+plan['id'],json=plan).status_code==200
    import pytest
    from fastapi import HTTPException
    with pytest.raises(HTTPException,match='409'):repo.load()
    with pytest.raises(HTTPException,match='409'):repo.save()
    other_store.engine.dispose()


def test_delay_length_and_plan_failure_policy_preserve_recovery():
    import pytest
    events=[];adapter=device();engine=Engine(adapter,events.append)
    assert engine.method('value_length',{'value':[1,2]},1)==2
    with pytest.raises(ValueError):engine.method('delay',{'seconds':True},1)
    with pytest.raises(ValueError):engine.method('delay',{'seconds':2},1)
    from tests.support import seed_cases
    first=copy.deepcopy(seed_cases()[1]);first['steps'][4]['expected']=999
    result=engine.run_cases({'kind':'plan','failure_policy':'stop'},[first,seed_cases()[0]])
    assert len(result['results'])==1 and result['results'][0]['restored'] is True
    assert result['blocked'] is False


def test_plan_copy_keeps_fixed_versions_and_new_item_ids(client):
    plan=make_plan(client)
    doc=next(d for d in client.get('/api/cases').json() if d['id']=='demo-read')
    doc['name']='更新后的最新版本'
    client.put('/api/cases/demo-read',json=doc)
    copied=client.post('/api/plans/'+plan['id']+'/fork').json()
    assert copied['id']!=plan['id'] and copied['definitions']==plan['definitions']
    assert [i['content_hash'] for i in copied['items']]==[i['content_hash'] for i in plan['items']]
    assert not set(i['item_id'] for i in copied['items'])&set(i['item_id'] for i in plan['items'])


def test_0001_database_upgrades_to_plans_without_changing_existing_case(tmp_path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect
    from tests.support import seed_cases
    store=Store(tmp_path)
    with store.session() as s:s.add(Case(id='keep',document={**seed_cases()[0],'id':'keep'}))
    cfg=Config();cfg.set_main_option('script_location',str(Path(__file__).resolve().parents[1]/'src/workbench/migrations'));cfg.set_main_option('sqlalchemy.url',str(store.engine.url))
    command.downgrade(cfg,'0001')
    store.engine.dispose()
    reopened=Store(tmp_path)
    assert 'plans' in inspect(reopened.engine).get_table_names()
    with reopened.session() as s:assert s.get(Case,'keep').document['id']=='keep'
    reopened.engine.dispose()


def test_preflight_reports_occupied_device_without_creating_another_task(client):
    doc={'id':'hold-device','name':'等待确认占用','model':'ultraarm_p1','steps':[{'id':'confirm','name':'等待','kind':'confirm','timeout':5}]}
    assert client.post('/api/cases',json=doc).status_code==201
    tid=start(client,{'case_ids':['hold-device']}).json()['id']
    result=client.post('/api/tasks/preview',json={'kind':'cases','case_ids':['demo-read'],'connection':{'address':'TEST-ONLY'}}).json()
    assert result['ready'] is False
    assert len(client.get('/api/tasks').json())==1
    client.post('/api/tasks/'+tid+'/control',json={'action':'stop'})
    wait_task(client,tid)
