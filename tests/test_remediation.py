import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

from workbench.devices import Adapter
from workbench.engine import Engine
from workbench.models import CaseDocument

LIBRARY=Path(__file__).resolve().parents[1]/'src/workbench/case_library'


class MyCobotPro450DataException(ValueError):
    pass


class ModeRobot:
    """Only in tests: independent mode/exception/readback fixture, never hardware."""
    def __init__(self,*args,**kwargs):
        self.fresh=0;self.debug=0;self.movement=1;self.calls=[];self.bad_restore=False
    def set_fresh_mode(self,mode):
        self.calls.append(('fresh',mode))
        if type(mode) is not int or mode not in (0,1):raise MyCobotPro450DataException('invalid mode')
        self.fresh=mode;return 1
    def get_fresh_mode(self):return self.fresh
    def set_debug_state(self,mode):self.debug=mode;return 1
    def get_debug_state(self):return None if self.bad_restore else self.debug
    def set_movement_type(self,mode):self.movement=mode;return 1
    def get_movement_type(self):return self.movement
    def get_servo_status(self):return [0]*6
    def get_angles(self):return [0.0]*6
    def is_moving(self):return 0
    def close(self):pass


def adapter():
    with patch.dict(sys.modules,{'pymycobot':SimpleNamespace(Pro450Client=ModeRobot)}):
        return Adapter({'mode':'hardware','model':'pro450','address':'TEST-ONLY'})


def scene(identifier):
    return CaseDocument.model_validate_json((LIBRARY/'cases/pro450'/(identifier+'.json')).read_text(encoding='utf-8')).model_dump()


def test_actual_remediated_mode_exception_and_literal_cases_restore_verified_state():
    robot=adapter();events=[]
    docs=[scene(key) for key in ['scenario-d7bd7d983369540ecd341f1d','scenario-62bd9fa6737015b20f4700d9','scenario-caeb52c118802788e09089cc']]
    assert all(doc['enabled'] is False for doc in docs)
    result=Engine(robot,events.append).run_cases({'kind':'cases'},docs)
    assert result['counts']['passed']==3 and result['blocked'] is False
    assert all(r['restored'] for r in result['results'])
    assert (robot.device.fresh,robot.device.debug,robot.device.movement)==(0,0,1)
    assert ('fresh',1) in robot.device.calls and ('fresh',2) in robot.device.calls
    assert any(e.get('exception')=='MyCobotPro450DataException' for e in events)


def test_remediated_restore_readback_failure_blocks_following_case():
    robot=adapter();robot.device.bad_restore=True
    first=scene('scenario-d7bd7d983369540ecd341f1d')
    next_case=scene('scenario-caeb52c118802788e09089cc')
    result=Engine(robot,lambda _:None).run_cases({'kind':'cases'},[first,next_case])
    assert result['blocked'] and len(result['results'])==1
    assert result['results'][0]['restore_error']
    assert result['not_executed']==1


def test_ledger_accounts_for_every_original_and_unmapped_source_without_false_hardware_pass():
    ledger=json.loads((LIBRARY/'remediation-ledger.json').read_text(encoding='utf-8'))
    assert len(ledger['entries'])==456
    assert len({row['original_id'] for row in ledger['entries']})==456
    assert len(ledger['unmapped_sources'])==46
    summary=ledger['summary']
    assert summary['fully_remediated_originals']+summary['blocked_originals']==442
    assert summary['hardware_verified']==0
    assert sum(row['data_row_count'] for row in ledger['entries'])==3232
    generated=[]
    for row in ledger['entries']:
        assert row['hardware_acceptance']=='not_verified'
        generated.extend(row['generated_case_ids'])
        if row['state']=='blocked':
            assert row['functions'] or row.get('unmapped_source_rows')
    assert len(set(generated))==77
    for key in set(generated):
        doc=scene(key)
        assert not doc['enabled'] and not doc['migration'].get('superseded_by')
        assert all(step['kind']!='review' for step in doc['steps'])
        assert doc['migration']['remediation']['hardware_acceptance']=='not_verified'
