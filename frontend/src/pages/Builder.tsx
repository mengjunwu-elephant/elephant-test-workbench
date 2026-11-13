import { useState } from 'react';
import { App, Button, Checkbox, Input, InputNumber, Select, Space, Tag } from 'antd';
import { api, post, put } from '../api';
import { Argument, Field, Panel, ValueInput } from '../components';
import { CaseDoc, Step, phases, kinds, categoryNames, newStep } from '../types';

type Props = { document: CaseDoc; setDocument: (value: CaseDoc) => void; cases: CaseDoc[]; capabilities: any; methods: any[]; onSaved: () => void; onRun: (body: any) => void };
export function Builder({ document: doc, setDocument, cases, capabilities, methods, onSaved, onRun }: Props) {
  const { message } = App.useApp();
  const [active, setActive] = useState(0);
  const [persistedId,setPersistedId]=useState('');
  const [saving, setSaving] = useState(false);
  const index = Math.min(active, doc.steps.length - 1);
  const selected = doc.steps[index];
  const readonly = doc.status === 'shared';
  const change = (patch: Partial<CaseDoc>) => { if (!readonly) setDocument({ ...doc, ...patch }); };
  const stepChange = (patch: Partial<Step>) => change({ steps: doc.steps.map((step, i) => i === index ? { ...step, ...patch } : step) });
  const add = (kind: string) => { change({ steps: [...doc.steps, newStep(kind)] }); setActive(doc.steps.length); };
  const move = (direction: number) => { const next = [...doc.steps]; const target = index + direction; if (target < 0 || target >= next.length) return; [next[index], next[target]] = [next[target], next[index]]; change({ steps: next }); setActive(target); };
  const setApi = (name: string) => {
    const entry = capabilities.apis?.find((item: any) => item.name === name);
    stepChange({ api: name, args: (entry?.parameters || []).filter((p: any) => ['POSITIONAL_ONLY', 'POSITIONAL_OR_KEYWORD'].includes(p.kind)).map((p: any) => p.default ?? null), kwargs: {} });
  };
  const save = async () => {
    setSaving(true);
    try {
      const exists = cases.some(item => item.id === doc.id) || persistedId===doc.id;
      const result = exists ? await put<CaseDoc>('/cases/' + doc.id, doc) : await post<CaseDoc>('/cases', doc);
      setPersistedId(result.id); setDocument(result); onSaved(); message.success('草稿已保存在本机'); return result;
    } catch (error) { message.error(String(error)); } finally { setSaving(false); }
  };
  const clone = async () => {
    try { const result = await post<CaseDoc>('/cases/' + doc.id + '/fork'); setPersistedId(result.id); setDocument(result); onSaved(); message.success('已复制为可编辑草稿'); } catch (error) { message.error(String(error)); }
  };
  const chooseTemplate = (value: string) => {
    if (value === 'parameter') {
      change({ name: '系统参数设置与恢复', method_versions: { set_read_verify: '1.0' }, steps: [
        { ...newStep('read'), phase: 'setup', name: '保存参数原值', api: '', save_as: 'original_value' },
        { ...newStep('method'), name: '设置后读回验证', method: 'set_read_verify', parameters: { set_api: '', set_args: [], get_api: '', get_args: [], expected: null, tolerance: 0 } },
        { ...newStep('call'), phase: 'restore', name: '恢复原值', api: '', args: [{ $ref: 'original_value' }] },
        { ...newStep('read'), phase: 'restore', name: '读回恢复结果', api: '', save_as: 'restored_value' },
        { ...newStep('assert'), phase: 'restore', name: '验证恢复成功', actual: { $ref: 'restored_value' }, expected: { $ref: 'original_value' }, assertion: 'eq' },
      ] });
    } else {
      const p1 = doc.model === 'ultraarm_p1';
      const readApi = p1 ? 'get_angles_info' : 'get_angles';
      change({ name: '单关节运动与恢复', description: '按产品规范填写关节、目标、速度及容差；接口以当前 SDK 为准。', parameters: { joint: null, angle: null, speed: null }, method_versions: { wait_motion: '1.0', restore_saved: '1.0' }, steps: [
        { ...newStep('read'), phase: 'setup', name: '保存初始位置', api: readApi, save_as: 'initial_angles' },
        { ...newStep('call'), name: '运动到目标', api: p1 ? 'set_angle' : 'send_angle', args: [{ $ref: 'params.joint' }, { $ref: 'params.angle' }, { $ref: 'params.speed' }] },
        { ...newStep('wait'), name: '等待运动完成' },
        { ...newStep('read'), phase: 'verify', name: '读取实际位置', api: readApi, save_as: 'actual_angles' },
        { ...newStep('assert'), phase: 'verify', name: '填写目标分量与容差', actual: { $ref: 'actual_angles.0' }, expected: { $ref: 'params.angle' }, assertion: 'approx', tolerance: 0.1 },
        { ...newStep('method'), phase: 'restore', name: '恢复初始位置并验证', method: 'restore_saved', parameters: { api: p1 ? 'set_angles' : 'send_angles', value: { $ref: 'initial_angles' }, extra_args: [{ $ref: 'params.speed' }], verify_api: readApi, tolerance: 0.1 } },
      ] });
    }
    setActive(0);
  };
  return <>
    <div className="toolbar"><Input aria-label="用例名称" value={doc.name} onChange={e => change({ name: e.target.value })} style={{ width: 270 }} disabled={readonly} /><Select aria-label="使用流程模板" placeholder="使用流程模板" style={{ width: 160 }} onChange={chooseTemplate} disabled={readonly} options={[{ value: 'motion', label: '运动与恢复模板' }, { value: 'parameter', label: '设置后读回模板' }]} /><Tag>v{doc.revision} · {readonly ? '共享正式版' : '个人草稿'}</Tag><Space className="toolbar-right">{readonly ? <Button onClick={clone}>复制为草稿</Button> : <Button loading={saving} onClick={save}>保存草稿</Button>}<Button type="primary" disabled={doc.enabled === false} onClick={async () => { const saved = readonly ? doc : await save(); if (saved) onRun({ kind:'cases', case_ids:[saved.id] }); }}>保存并准备试跑</Button></Space></div>
    {doc.migration?.workbook && <Panel title="迁移审核"><p>{doc.migration.workbook} / {doc.migration.sheet} · {doc.datasets.length} 组原始参数</p><p>{(doc.migration.review_reasons || []).join('；')}</p><p className="muted small">参考用例库中的迁移来源，将待完善步骤替换为实际前置、执行、验证和恢复。原始字符串未自动 eval，需按接口预期转换类型。</p></Panel>}
    <div className="section"><Checkbox disabled={readonly || doc.steps.some(s => s.kind === 'review')} checked={doc.enabled !== false} onChange={e => change({ enabled:e.target.checked })}>启用用例（已核对参数、前置、断言与恢复）</Checkbox></div>
    <div className="builder-grid"><section className="step-library"><h3>步骤库</h3><p className="muted">选择后加入流程</p>{Object.entries(kinds).filter(([key]) => key !== 'review').map(([key, label]) => <Button key={key} block disabled={readonly} onClick={() => add(key)}>＋ {label}</Button>)}<p className="muted small">复杂逻辑通过公共方法复用。前序结果可通过“引用”传给后续步骤。</p></section>
      <section><div className="section-heading"><h3>测试步骤</h3><span className="muted">{doc.steps.length} 步</span></div>{doc.steps.map((step, i) => <button key={step.id} className={'step-row ' + (i === index ? 'selected' : '')} onClick={() => setActive(i)}><span className="step-index">{String(i + 1).padStart(2, '0')}</span><span className="step-content"><strong>{step.name}</strong><small>{kinds[step.kind as keyof typeof kinds]}</small></span><span className="phase">{phases[step.phase as keyof typeof phases]}</span></button>)}<Space wrap><Button disabled={readonly || index === 0} onClick={() => move(-1)}>上移</Button><Button disabled={readonly || index === doc.steps.length - 1} onClick={() => move(1)}>下移</Button><Button disabled={readonly || doc.steps.length < 2} onClick={() => { change({ steps: doc.steps.filter((_, i) => i !== index) }); setActive(Math.max(0, index - 1)); }}>删除步骤</Button></Space></section>
      <Panel title="步骤配置" className="step-properties"><fieldset disabled={readonly} inert={readonly}>
        {selected.kind === 'review' && <p className="error">{selected.message}。请添加实际步骤，再删除本条待完善记录。</p>}<Field label="步骤名称"><Input value={selected.name} onChange={e => stepChange({ name: e.target.value })} /></Field>
        <Field label="执行阶段"><Select value={selected.phase} onChange={phase => stepChange({ phase })} options={Object.entries(phases).map(([value, label]) => ({ value, label }))} /></Field>
        {['call', 'read'].includes(selected.kind) && <><Field label="SDK 接口"><Select showSearch value={selected.api || undefined} placeholder="选择当前环境中的接口" onChange={setApi} options={(capabilities.apis || []).map((x: any) => ({ value: x.name, label: x.name }))} /></Field>{(selected.args || []).map((value, i) => <Field key={i} label={capabilities.apis?.find((x: any) => x.name === selected.api)?.parameters?.[i]?.name || '位置参数 ' + (i + 1)}><Argument value={value} onChange={v => stepChange({ args: (selected.args || []).map((a, j) => j === i ? v : a) })} /></Field>)}<Button size="small" onClick={() => stepChange({ args: [...(selected.args || []), null] })}>增加位置参数</Button><Field label="关键字参数（JSON 对象）"><ValueInput value={selected.kwargs || {}} onChange={kwargs => stepChange({ kwargs })} /></Field><Field label="保存结果变量"><Input value={selected.save_as} onChange={e => stepChange({ save_as: e.target.value })} placeholder="result / initial_angles" /></Field><Field label="结果取值路径（JSON 数组）"><ValueInput value={selected.selector || []} onChange={selector => stepChange({ selector })} /></Field><Field label="预期异常类型（留空表示正常返回）"><Input value={selected.expected_exception} onChange={e => stepChange({ expected_exception: e.target.value })} placeholder="ValueError / 产品 SDK 异常类名" /></Field></>}
        {selected.kind === 'assert' && <><Field label="实际结果"><Argument value={selected.actual} onChange={actual => stepChange({ actual })} /></Field><Field label="比较方式"><Select value={selected.assertion} options={[{ value: 'eq', label: '严格相等（包含类型）' }, { value: 'value_eq', label: '值相等（原 Python 比较语义）' }, { value: 'instance', label: '属于类型（原 isinstance 语义）' }, { value: 'approx', label: '绝对容差' }, { value: 'range', label: '区间包含' }, { value: 'type', label: '类型名称' }, { value: 'truthy', label: '真值' }]} onChange={assertion => stepChange({ assertion })} /></Field><Field label="预期结果"><Argument value={selected.expected} onChange={expected => stepChange({ expected })} /></Field><Field label="绝对容差"><InputNumber min={0} value={selected.tolerance} onChange={value => stepChange({ tolerance: value ?? 0 })} /></Field></>}
        {selected.kind === 'confirm' && <Field label="现场确认内容"><Input.TextArea value={selected.message} onChange={e => stepChange({ message: e.target.value })} /></Field>}
        {selected.kind === 'method' && <><Field label="公共方法"><Select value={selected.method} options={methods.map(m => ({ value: m.id, label: m.name + ' · ' + m.version }))} onChange={method => { const info = methods.find(m => m.id === method); change({ method_versions: { ...doc.method_versions, [method]: info.version }, steps: doc.steps.map((s, i) => i === index ? { ...s, method, parameters: structuredClone(info.parameters) } : s) }); }} /></Field>{Object.entries(selected.parameters || {}).map(([key, value]) => <Field key={key} label={key}><Argument value={value} onChange={v => stepChange({ parameters: { ...selected.parameters, [key]: v } })} /></Field>)}</>}
        {['wait', 'method', 'confirm'].includes(selected.kind) && <Field label="超时（秒）"><InputNumber min={0.1} max={3600} value={selected.timeout} onChange={value => stepChange({ timeout: value ?? 15 })} /></Field>}
      </fieldset></Panel>
    </div>
    <details className="section"><summary>用例参数、数据组与适用条件</summary><div className="two-columns section"><Panel title="用例元数据"><Field label="场景类型"><Select value={doc.category} onChange={category => change({ category })} options={Object.entries(categoryNames).map(([value, label]) => ({ value, label }))} /></Field><Field label="适用 SDK 版本条件"><Input value={doc.sdk_requirement} placeholder=">=4.0,<5" onChange={e => change({ sdk_requirement: e.target.value })} /></Field><Field label="说明"><Input.TextArea value={doc.description} onChange={e => change({ description: e.target.value })} /></Field></Panel><Panel title="可复用参数与参数化数据"><Field label="默认参数（以 params.name 引用）"><ValueInput value={doc.parameters} multiline onChange={parameters => change({ parameters })} /></Field><Field label="数据组（每组覆盖默认参数，复用相同步骤）"><ValueInput value={doc.datasets} multiline onChange={datasets => change({ datasets })} /></Field><p className="muted small">示例：默认 angle 为 30；数据组可填 [{`{"angle":10}`}, {`{"angle":35}`} ]。超限、类型错误等输入保留原始 JSON 类型。</p></Panel></div></details>
    <div className="policy">普通失败后执行恢复；恢复成功继续下一条，恢复失败或状态不明时停止该设备后续任务。</div>
  </>;
}
