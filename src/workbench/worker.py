from __future__ import annotations
import argparse
import json
import queue
import sys
import threading
import traceback
import importlib
from pathlib import Path
from .devices import Adapter, describe, TransportConnectionError
from .engine import Engine, Control, Cancelled

PREFIX = "@ELEPHANT_EVENT@"

def emit(payload):
    print(PREFIX + json.dumps(payload, ensure_ascii=False, default=str, allow_nan=False), flush=True)

def execute_request(adapter, payload, output, control):
    engine = Engine(adapter, output, control)
    request = payload['request']
    engine.telemetry(force=True)
    if request['kind'] == 'debug':
        try:
            value = engine.call(request['api'], request.get('args', []), request.get('kwargs', {}))
            summary = {'state':'completed','result':value,'result_type':type(value).__name__,'blocked':False}
        except Exception as exc:
            summary = {'state':'failed','error':f'{type(exc).__name__}: {exc}','blocked':False}
        # 调试只记录这次 SDK 调用；运动、等待和恢复由用户显式选择。
        # 会话自己的读回循环负责连接质量，不把一次调用当成完整测试。
        return summary
    if request['kind'] == 'latency':
        return engine.latency(request)
    return engine.run_cases(request,payload['cases'])

def run_session(adapter, output, mailbox, holder, closing):
    failures = 0
    def validate(initial=False):
        nonlocal failures
        invalid_readback = False
        try:
            positions,moving=adapter.position(),adapter.moving()
            if positions is None or moving is None:
                invalid_readback = True
                raise RuntimeError('实机位置或运动状态读回无效')
        except Exception as exc:
            failures += 1
            output({'type':'telemetry','positions':None,'moving':None,'quality':'error','source':'hardware','message':str(exc)})
            output({'type':'state','state':'degraded','message':f'状态读取失败，正在重试（第 {failures} 次）：{exc}'})
            # 已建立会话的空读回不等于 socket 已关闭；保留调试通道，
            # 例如可显式调用上电/状态接口。初次验证和真实通信异常有界失败。
            if failures >= 3 and (initial or not invalid_readback):
                raise RuntimeError(f'连接校验失败：连续 {failures} 次读回无效：{exc}') from exc
            return False
        failures = 0
        output({'type':'telemetry','positions':positions,'moving':moving,'quality':'ok','source':'hardware'})
        output({'type':'state','state':'connected','message':'连接已校验，保持同一执行端连接'})
        return True
    while not validate(initial=True):
        if closing.wait(.5):
            return {'state':'completed','blocked':False,'connected':False}
    output({'type':'device_info','info':adapter.versions(),'message':'设备版本信息已读取'})
    while not closing.is_set():
        try:
            command=mailbox.get(timeout=.5)
        except queue.Empty:
            validate()
            continue
        task_id=command['task_id']
        holder['task_id']=task_id
        holder['control']=holder['controls'][task_id]
        def task_output(event):
            output({**event,'task_id':task_id})
        task_output({'type':'state','state':'running','message':'复用已校验的实机连接'})
        try:
            summary=execute_request(adapter,command['payload'],task_output,holder['control'])
        except Cancelled as exc:
            unknown=adapter.moving() is not False
            summary={'state':'blocked' if unknown else 'stopped','blocked':unknown,'reason':str(exc)}
        except BaseException as exc:
            summary={'state':'blocked','blocked':True,'error':f'{type(exc).__name__}: {exc}'}
        task_output({'type':'job_complete','summary':summary})
        holder['task_id']=None
        holder['controls'].pop(task_id,None)
        if summary.get('blocked'):
            return {'state':'blocked','blocked':True,'error':summary.get('error',summary.get('reason','任务恢复或状态校验失败'))}
        # 下一次轮询再采样，避免 SDK 调用结束后立即密集读回。
    moving=adapter.moving()
    return {'state':'completed' if moving is False else 'blocked','blocked':moving is not False,'connected':False,'reason':'连接已关闭' if moving is False else '断开时无法确认静止状态'}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--payload')
    parser.add_argument('--describe')
    args=parser.parse_args()
    if args.describe:
        print(json.dumps(describe(args.describe),ensure_ascii=False,default=str))
        return
    payload=json.loads(Path(args.payload).read_text(encoding='utf-8'))
    session=payload['request']['kind']=='connection'
    mailbox=queue.Queue()
    closing=threading.Event()
    holder={'control':Control(),'task_id':None,'controls':{}}
    def reader():
        for line in sys.stdin:
            try:
                command=json.loads(line)
                if session and command.get('action')=='run':
                    holder['controls'][command['task_id']]=Control()
                    mailbox.put(command)
                elif session and command.get('action') in ('disconnect','stop') and not command.get('task_id'):
                    closing.set()
                    holder['control'].accept({'action':'stop'})
                elif command.get('task_id') in holder['controls']:
                    holder['controls'][command['task_id']].accept(command)
                elif not command.get('task_id'):
                    holder['control'].accept(command)
            except (ValueError,TypeError):
                continue
        closing.set()
        holder['control'].accept({'action':'stop'})
    adapter=None
    sdk_loaded=False
    stage='sdk_import'
    emit({'type':'state','state':'connecting','message':'正在建立实机连接并校验通信'})
    try:
        # On Windows, importing NumPy while another thread blocks in stdin can
        # stall DLL initialization. Load the SDK before starting the pipe reader.
        emit({'type':'connection_stage','stage':'sdk_import','message':'正在加载 SDK 与基础依赖，尚未开始设备连接'})
        importlib.import_module('pymycobot')
        sdk_loaded=True
        stage='sdk_initialize'
        emit({'type':'connection_stage','stage':'sdk_initialize','message':'SDK 已加载，正在初始化设备连接'})
        threading.Thread(target=reader,daemon=True).start()
        adapter=Adapter(payload['request']['connection'])
        stage='state_readback'
        emit({'type':'connection_stage','stage':'read_position','message':'设备客户端已创建，正在校验位置与运动状态'})
        if session:
            summary=run_session(adapter,emit,mailbox,holder,closing)
        else:
            stage='execute_request'
            emit({'type':'state','state':'running','message':'执行端已连接，开始任务','source':'hardware'})
            summary=execute_request(adapter,payload,emit,holder['control'])
        emit({'type':'complete','summary':summary,'source':'hardware'})
    except TransportConnectionError as exc:
        emit({'type':'complete','summary':{'state':'failed','blocked':False,'connection_established':False,'error':str(exc)}})
    except Cancelled as exc:
        unknown=not adapter or adapter.moving() is not False
        emit({'type':'complete','summary':{'state':'blocked' if unknown else 'stopped','blocked':unknown,'reason':str(exc)}})
    except BaseException as exc:
        emit({'type':'complete','summary':{'state':'blocked' if sdk_loaded else 'failed','blocked':sdk_loaded,'connection_stage':stage,'error':f'{type(exc).__name__}: {exc}','traceback':traceback.format_exc()}})
    finally:
        if adapter:
            adapter.close()

if __name__=='__main__':
    main()
