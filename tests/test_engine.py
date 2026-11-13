import copy
import threading
import time

import pytest

from tests.support import seed_cases, device
from workbench.engine import Engine, Control
from workbench.models import RunRequest, CaseDocument


def test_failure_restores_and_continues_but_restore_failure_blocks():
    first, second = copy.deepcopy(seed_cases()[1]), copy.deepcopy(seed_cases()[0])
    first["steps"][4]["expected"] = 99
    events = []
    engine = Engine(device(), events.append)
    result = engine.run_cases({"kind": "cases"}, [first, second])
    assert [x["state"] for x in result["results"]] == ["failed", "passed"]
    assert result["results"][0]["restored"] is True
    first["steps"][-1]["parameters"]["api"] = "unknown_restore"
    result = Engine(device(), events.append).run_cases({"kind": "cases"}, [first, second])
    assert result["blocked"] and len(result["results"]) == 1
    assert result["results"][0]["error"] and result["results"][0]["restore_error"]


def test_negative_input_type_is_not_coerced_and_exception_is_pass():
    doc = copy.deepcopy(seed_cases()[2])
    doc["steps"][0]["args"] = ["1", 30, 20]
    validated = CaseDocument.model_validate(doc).model_dump()
    assert validated["steps"][0]["args"][0] == "1"
    result = Engine(device(), lambda _: None).run_cases({"kind": "cases"}, [validated])
    assert result["counts"]["passed"] == 1


def test_motion_and_sdk_samples_separate_and_restore_initial_state():
    adapter = device()
    initial = adapter.position()
    request = RunRequest(kind="latency", api="set_angle", args=[1, 30, 20], repeat=2, measure_motion=True, interval_ms=0).model_dump()
    result = Engine(adapter, lambda _: None).latency(request)
    assert result["sdk"]["count"] == result["motion"]["count"] == 2
    assert result["samples"][0]["motion_ms"] > result["samples"][0]["sdk_ms"]
    assert adapter.position() == pytest.approx(initial)


def test_manual_confirmation_never_auto_continues():
    control, events = Control(), []
    engine = Engine(device(), events.append, control)
    completed = []
    thread = threading.Thread(target=lambda: (engine.confirm("人工确认", timeout=2), completed.append(True)))
    thread.start()
    deadline = time.monotonic()+1
    while not events and time.monotonic()<deadline:
        time.sleep(0.01)
    assert events[0]["type"] == "confirmation" and not completed
    control.accept({"action": "confirm", "id": events[0]["id"], "answer": "continue"})
    thread.join(1)
    assert completed


def test_parameter_datasets_reuse_same_steps():
    case = copy.deepcopy(seed_cases()[1])
    case["datasets"] = [{"angle": 10}, {"angle": 35}]
    result = Engine(device(), lambda _: None).run_cases({"kind": "cases"}, [case])
    assert result["counts"]["passed"] == 2
    assert [x["parameters"]["angle"] for x in result["results"]] == [10, 35]


def test_migration_preserves_python_equality_and_isinstance_without_coercion():
    from workbench.engine import compare
    compare(1.0,1,'value_eq',0)
    compare(1.0,'float','instance',0)
    compare(True,'int','instance',0)  # 原 isinstance(True, int) 语义
    with pytest.raises(AssertionError):
        compare(1.0,1,'eq',0)  # 新编排严格相等规则保持不变
    with pytest.raises(AssertionError):
        compare('1',1,'value_eq',0)
