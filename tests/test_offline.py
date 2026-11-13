import io
import time
import zipfile
import pytest

from fastapi.testclient import TestClient
from workbench.api import create_app


def test_offline_package_path_traversal_rejected(tmp_path):
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as z:z.writestr('../escape.whl',b'x')
    with TestClient(create_app(tmp_path)) as client:
        response=client.post('/api/environments/import',files=[('files',('bad.zip',data.getvalue(),'application/zip'))])
        assert response.status_code==400 and '路径' in response.json()['detail']
        assert not (tmp_path/'escape.whl').exists()


def test_missing_offline_dependency_fails_without_activating(tmp_path):
    # 人工构造的测试包，不是真实 SDK；只验证离线缺依赖和旧环境不变。
    wheel=io.BytesIO()
    with zipfile.ZipFile(wheel,'w') as z:
        z.writestr('pymycobot/__init__.py','')
        z.writestr('pymycobot-0.0.0.dist-info/METADATA','Metadata-Version: 2.1\nName: pymycobot\nVersion: 0.0.0\nRequires-Dist: deliberately-absent-test-dependency==0.0.0\n')
        z.writestr('pymycobot-0.0.0.dist-info/WHEEL','Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        z.writestr('pymycobot-0.0.0.dist-info/RECORD','')
    with TestClient(create_app(tmp_path)) as client:
        r=client.post('/api/environments/import',files=[('files',('pymycobot-0.0.0-py3-none-any.whl',wheel.getvalue(),'application/octet-stream'))])
        assert r.status_code==202,r.text
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            rows=client.get('/api/environments').json()
            if rows[0]['state']!='preparing':break
            time.sleep(.1)
        assert rows[0]['state']=='failed'
        assert not rows[0]['active']
        assert rows[0]['details']['error_code']=='missing_offline_distribution'
        assert rows[0]['details']['requirement']=='deliberately-absent-test-dependency==0.0.0'
        assert 'deliberately-absent-test-dependency' in rows[0]['details']['error']
        assert '重新上传' in rows[0]['details']['error']
        assert 'no-index' not in rows[0]['details'].get('error','') or rows[0]['details']['source']=='offline-wheel'
        assert client.post('/api/environments/'+rows[0]['id']+'/activate').status_code==400


def test_complete_offline_test_wheel_can_prepare_and_activate(tmp_path):
    # 此包仅为安装器测试夹具；没有实机接口，也不用于伪造硬件测试。
    wheel=io.BytesIO()
    with zipfile.ZipFile(wheel,'w') as z:
        z.writestr('pymycobot/__init__.py','TEST_FIXTURE_ONLY = True\n')
        z.writestr('pymycobot-0.0.1.dist-info/METADATA','Metadata-Version: 2.1\nName: pymycobot\nVersion: 0.0.1\nRequires-Dist: pyserial\n')
        z.writestr('pymycobot-0.0.1.dist-info/WHEEL','Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        z.writestr('pymycobot-0.0.1.dist-info/RECORD','')
    with TestClient(create_app(tmp_path)) as client:
        response=client.post('/api/environments/import',files=[('files',('pymycobot-0.0.1-py3-none-any.whl',wheel.getvalue(),'application/octet-stream'))])
        assert response.status_code==202,response.text
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            row=client.get('/api/environments').json()[0]
            if row['state']!='preparing':break
            time.sleep(.1)
        assert row['state']=='ready',row
        assert row['details']['sdk_version']=='0.0.1' and row['details']['source']=='offline-wheel'
        assert row['details']['dependencies']['pyserial']=='3.5'
        assert row['details']['base_requirements']==['pyserial>=3.5,<4','numpy>=1.24,<3']
        assert row['details']['dependencies']['numpy']
        assert 'pyserial-3.5-py2.py3-none-any.whl' in row['details']['bundled_hashes']
        assert set(row['details']['hashes'])=={'pymycobot-0.0.1-py3-none-any.whl'}
        assert client.post('/api/environments/'+row['id']+'/activate').status_code==200
        assert client.get('/api/environments').json()[0]['active']


@pytest.mark.parametrize('log,code,expected',[
    ('ERROR: Could not find a version that satisfies the requirement pyserial (from pymycobot) (from versions: none)','missing_base_dependency','pyserial'),
    ('ERROR: sample-1.0-cp312-cp312-linux_x86_64.whl is not a supported wheel on this platform.','incompatible_wheel','sample-1.0'),
    ('ERROR: Cannot install conflicting versions\nERROR: ResolutionImpossible','dependency_conflict','ResolutionImpossible'),
])
def test_pip_failure_reports_concrete_cause(log,code,expected):
    from workbench.environments import install_failure
    result=install_failure(log)
    assert result['error_code']==code
    assert expected in result['error']


def test_existing_failed_import_also_displays_missing_package(tmp_path):
    from workbench.storage import Store,Environment
    from workbench.environments import EnvironmentManager
    store=Store(tmp_path)
    original={'error':'旧笼统提示','log':'ERROR: Could not find a version that satisfies the requirement pyserial (from pymycobot) (from versions: none)'}
    with store.session() as session:
        session.add(Environment(id='failed-import',name='失败记录',state='failed',details=original))
    result=EnvironmentManager(store).list()[0]
    assert result['details']['requirement']=='pyserial'
    with store.session() as session:
        assert session.get(Environment,'failed-import').details==original
