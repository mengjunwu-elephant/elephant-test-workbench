from fastapi.testclient import TestClient
from workbench.api import create_app
from workbench.hub import create_hub, add_user
from workbench.models import CaseDocument
from tests.support import seed_cases
import sys


def test_check_only_not_enable_and_enable_never_overwrites_drafts_or_tasks(tmp_path, monkeypatch):
    hub=create_hub(tmp_path/'hub')
    add_user(hub.state.store,'maintainer','test-only-password','maintainer')
    local=create_app(tmp_path/'local')
    with TestClient(hub) as remote, TestClient(local) as client:
        token=remote.post('/auth/login',json={'username':'maintainer','password':'test-only-password'}).json()['token']
        headers={'Authorization':'Bearer '+token}
        def request(method,path,**kwargs):
            response=remote.request(method,path,headers=headers,**kwargs)
            assert response.is_success,response.text
            return response.json()
        local.state.sharing.request=request
        assert client.post('/api/cases',json=seed_cases()[0]).status_code==201
        doc=client.get('/api/cases').json()[0]
        assert remote.post('/releases',headers=headers,json={'document':doc,'base_revision':0}).status_code==201
        before=client.get('/api/cases').json()
        assert client.post('/api/shared/check').status_code==200
        assert client.get('/api/cases').json()==before
        assert client.post('/api/shared/enable',json={'case_id':doc['id'],'revision':1}).status_code==200
        after=client.get('/api/cases').json()
        assert len(after)==len(before)+1
        assert next(c for c in after if c['id']==doc['id'])['status']=='draft'
        shared=next(c for c in after if c['status']=='shared')
        assert client.put('/api/cases/'+shared['id'],json=shared).status_code==403
        tampered={**shared,'name':'篡改的正式版'}
        monkeypatch.setattr(local.state.manager,'resolve_environment',lambda request:(sys.executable,{'sdk_version':'0.0.0'}))
        assert client.post('/api/tasks',json={'inline_cases':[tampered],'connection':{'address':'TEST-ONLY'},'hardware_confirmed':True}).status_code==409
        assert client.post('/api/cases/'+shared['id']+'/fork').json()['status']=='draft'
