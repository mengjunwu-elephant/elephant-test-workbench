import copy
import json
from pathlib import Path
import subprocess

import pytest
from fastapi import HTTPException

from tests.support import seed_cases
from workbench.case_repository import CaseRepository
from workbench.models import CaseDocument
from workbench.storage import Store, Case
from workbench.tasks import TaskManager


def repository(tmp_path):
    checkout=tmp_path/'checkout'
    checkout.mkdir()
    subprocess.run(['git','init',str(checkout)],check=True,capture_output=True)
    subprocess.run(['git','-C',str(checkout),'config','user.name','Repository Test'],check=True)
    subprocess.run(['git','-C',str(checkout),'config','user.email','test@example.invalid'],check=True)
    library=checkout/'case_library'
    folder=library/'cases'/'local'
    folder.mkdir(parents=True)
    doc=CaseDocument.model_validate(seed_cases()[0]).model_dump()
    path=folder/(doc['id']+'.json')
    path.write_text(json.dumps(doc),encoding='utf-8')
    store=Store(tmp_path/'data')
    return CaseRepository(store,TaskManager(store),library,checkout),path,doc


def test_repository_roundtrip_conflict_and_source_deletion(tmp_path):
    repo,path,doc=repository(tmp_path)
    assert repo.load()['loaded']==1
    local=copy.deepcopy(doc); local.update(name='本机编辑',revision=2)
    with repo.store.session() as s:
        s.get(Case,doc['id']).document=local
    disk={**doc,'name':'同事文件修改'}
    path.write_text(json.dumps(disk),encoding='utf-8')
    with pytest.raises(HTTPException,match='409'):
        repo.load()
    with pytest.raises(HTTPException,match='409'):
        repo.save()
    with repo.store.session() as s:
        assert s.get(Case,doc['id']).document['name']=='本机编辑'
    preserved=repo.load(preserve_local=True)
    assert preserved['preserved_local_conflicts']==[doc['id']]
    with repo.store.session() as s:
        assert s.get(Case,doc['id']).document['name']=='本机编辑'
    path.write_text(json.dumps(doc),encoding='utf-8')
    assert repo.save()['saved']==1
    assert json.loads(path.read_text(encoding='utf-8'))['name']=='本机编辑'
    path.unlink()
    with pytest.raises(HTTPException,match='409'):
        repo.save()
    assert repo.load()['deleted']==1


def test_git_commit_limits_files_and_push_pull_uses_existing_remote(tmp_path):
    repo,path,doc=repository(tmp_path)
    repo.load()
    (repo.checkout/'private.txt').write_text('不应被用例提交带入',encoding='utf-8')
    repo.action('commit','迁移用例')
    files=repo.git('show','--pretty=format:','--name-only','HEAD')
    assert 'case_library/' in files and 'private.txt' not in files
    assert repo.status()['has_remote'] is False
    remote=tmp_path/'remote.git'
    subprocess.run(['git','init','--bare',str(remote)],check=True,capture_output=True)
    repo.git('remote','add','origin',str(remote))
    repo.git('push','-u','origin','HEAD')
    repo.action('push')
    with pytest.raises(HTTPException,match='409'):
        repo.action('pull')  # unrelated untracked file also prevents automatic pull
    (repo.checkout/'private.txt').unlink()
    repo.action('pull')
    assert repo.status()['has_remote'] is True


def test_migrated_assets_are_complete_and_review_steps_cannot_be_enabled():
    library=Path(__file__).resolve().parents[1]/'src/workbench/case_library'
    manifest=json.loads((library/'migration-manifest.json').read_text(encoding='utf-8'))
    docs=[CaseDocument.model_validate_json(p.read_text(encoding='utf-8')) for p in library.glob('cases/*/*.json')]
    originals=[d for d in docs if d.id.startswith('migrated-')]
    assert len(originals)==manifest['imported_documents']==456
    ledger=json.loads((library/'remediation-ledger.json').read_text(encoding='utf-8'))
    assert len(docs)==456+ledger['summary']['generated_scenarios']
    assert sum(len(d.datasets) for d in originals)==manifest['data_row_count']==3232
    assert sum(d.enabled for d in docs)==manifest['executable_documents']==14
    assert len({d.id for d in docs})==len(docs)
    for doc in docs:
        assert len(doc.datasets)==len(doc.migration['rows'])
        for ref in doc.migration['source_files']:
            assert (library/ref).is_file()
        if doc.enabled:
            assert all(s.kind in ('read','assert') for s in doc.steps)
        elif any(step.kind=='review' for step in doc.steps):
            with pytest.raises(ValueError,match='替换'):
                CaseDocument.model_validate({**doc.model_dump(),'enabled':True})


def test_unreviewed_case_is_blocked_even_when_submitted_inline(tmp_path):
    from workbench.models import RunRequest
    store=Store(tmp_path)
    manager=TaskManager(store)
    manager.resolve_environment=lambda request: ('unused',{'sdk_version':'0.0.0'})
    doc={**seed_cases()[0],'enabled':False}
    with pytest.raises(HTTPException,match='409'):
        manager.start(RunRequest(kind='cases',connection={'address':'TEST-ONLY'},hardware_confirmed=True,inline_cases=[doc]))


def test_migrated_version_case_accepts_original_float_response_for_integer_expected():
    from tests.support import device
    from workbench.engine import Engine
    path=Path(__file__).resolve().parents[1]/'src/workbench/case_library/cases/ultraarm_p1/migrated-2dc49b411f3929abba14decf.json'
    doc=CaseDocument.model_validate_json(path.read_text(encoding='utf-8')).model_dump()
    assert type(doc['datasets'][0]['expect_data']) is int
    adapter=device()
    adapter.device.get_system_version=lambda:1.0
    adapter.allowed.add('get_system_version')
    result=Engine(adapter,lambda _:None).run_cases({'kind':'cases'},[doc])
    assert result['counts']['passed']==1 and not result['blocked']
