from fastapi.testclient import TestClient
from workbench.hub import create_hub, add_user
from tests.support import seed_cases


def test_roles_immutable_releases_and_publish_conflict(tmp_path):
    app=create_hub(tmp_path)
    add_user(app.state.store,'publisher','only-test-password-1','maintainer')
    add_user(app.state.store,'tester','only-test-password-2','tester')
    with TestClient(app) as client:
        def login(user,password):
            token=client.post('/auth/login',json={'username':user,'password':password}).json()['token']
            return {'Authorization':'Bearer '+token}
        publisher=login('publisher','only-test-password-1')
        tester=login('tester','only-test-password-2')
        body={'document':seed_cases()[0],'base_revision':0}
        assert client.post('/releases',json=body,headers=tester).status_code==403
        result=client.post('/releases',json=body,headers=publisher)
        assert result.status_code==201,result.text
        assert client.post('/releases',json=body,headers=publisher).status_code==409
        old=client.get('/releases/demo-read/1',headers=tester).json()
        body['document']['name']='第二版';body['base_revision']=1
        assert client.post('/releases',json=body,headers=publisher).json()['revision']==2
        assert client.get('/releases/demo-read/1',headers=tester).json()==old
        assert client.get('/releases',headers=tester).json()[0]['revision']==2
        assert client.post('/results',json={}).status_code==404
        assert client.post('/auth/logout',headers=tester).status_code==200
        assert client.get('/releases',headers=tester).status_code==401

