import sys
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from workbench.devices import Adapter, TransportConnectionError
from workbench.tasks import resource_keys


def sdk_client():
    class Pro450Client:
        def __init__(self, ip, netport, debug):
            self.SERVER_IP,self.SERVER_PORT=ip,netport
            self.sock=self.connect_socket()
        def get_angles(self): return [0.0]*6
        def is_moving(self): return 0
        def close(self): self.sock.close()
    return Pro450Client


def test_450_connects_by_ip_port_without_baudrate_and_restores_socket_mode(monkeypatch):
    sock=Mock()
    monkeypatch.setattr('workbench.devices.socket.socket',lambda *args:sock)
    monkeypatch.setitem(sys.modules,'pymycobot',SimpleNamespace(Pro450Client=sdk_client()))
    adapter=Adapter({'model':'pro450','mode':'hardware','address':'TEST-ONLY','netport':4567,'connect_timeout':2})
    sock.connect.assert_called_once_with(('TEST-ONLY',4567))
    assert sock.settimeout.call_args_list[0].args==(2,)
    assert sock.settimeout.call_args_list[-1].args==(None,)
    assert type(adapter.device).__name__=='Pro450Client'
    assert adapter.position()==[0.0]*6 and adapter.moving() is False
    adapter.close()


def test_450_connect_timeout_closes_unestablished_transport(monkeypatch):
    sock=Mock()
    sock.connect.side_effect=TimeoutError('test timeout')
    monkeypatch.setattr('workbench.devices.socket.socket',lambda *args:sock)
    monkeypatch.setitem(sys.modules,'pymycobot',SimpleNamespace(Pro450Client=sdk_client()))
    with pytest.raises(TransportConnectionError,match='4500'):
        Adapter({'model':'pro450','mode':'hardware','address':'TEST-ONLY','connect_timeout':1})
    sock.close.assert_called_once()


def test_tcp_port_participates_in_connection_ownership():
    a={'model':'pro450','address':'TEST-ONLY','netport':4500}
    b={**a,'netport':4501}
    assert set(resource_keys(a)).isdisjoint(resource_keys(b))
