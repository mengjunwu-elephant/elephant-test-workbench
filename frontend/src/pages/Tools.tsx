import { useEffect, useState } from 'react';
import { App, Button, Checkbox, Empty, Input, InputNumber, Popconfirm, Select, Space, Table, Tag } from 'antd';
import { api, post, put } from '../api';
import { Argument, Field, JsonDetails, ModeTag, Panel, StateTag, ValueInput } from '../components';
import { CaseDoc, Connection, Model, Task, newCase, newStep, terminal } from '../types';
import { TimingCharts, useTask } from './Execution';

export function DevicePage({ onRun, onDisconnect, session, connection, setConnection, models, environments, locks, refresh }: { onRun: (body: any) => void; onDisconnect: () => void; session?: Task; connection: Connection; setConnection: (v: Connection) => void; models: Model[]; environments: any[]; locks: any[]; refresh: () => void }) {
  const { message } = App.useApp();
  const profile = models.find(m => m.id === connection.model);
  const info = session?.summary?.device_info;
  const save = async () => { try { await put('/settings/connection', connection); message.success('本机连接配置已保存'); refresh(); } catch (error) { message.error(String(error)); } };
  return <div className="two-columns"><Panel title="机型与连接">
    <Field label="所选机型"><Input readOnly value={profile?.name || connection.model} /></Field>
    <Field label={profile?.connection === 'ip' ? '控制器 IP' : profile?.connection === 'dual_serial' ? '左臂串口' : '串口地址'}><Input value={connection.address} onChange={e => setConnection({ ...connection, address: e.target.value })} /></Field>
    {profile?.connection === 'dual_serial' && <><Field label="右臂串口"><Input value={connection.second_address} onChange={e => setConnection({ ...connection, second_address: e.target.value })} /></Field><Field label="当前操作侧"><Select value={connection.arm || 'left'} options={[{ value: 'left', label: '左臂' }, { value: 'right', label: '右臂' }]} onChange={arm => setConnection({ ...connection, arm })} /></Field></>}
    {profile?.connection === 'ip' ? <Field label="TCP 端口"><InputNumber aria-label="TCP 端口" min={1} max={65535} value={connection.netport || 4500} onChange={value => setConnection({ ...connection, netport: value || 4500 })} /></Field> : <Field label="波特率"><InputNumber aria-label="波特率" min={1} value={connection.baudrate} onChange={value => setConnection({ ...connection, baudrate: value || 115200 })} /></Field>}
    <Field label="连接超时（秒）"><InputNumber aria-label="连接超时（秒）" min={1} max={120} value={connection.connect_timeout || 8} onChange={value => setConnection({ ...connection, connect_timeout: value || 8 })} /></Field>
    <Field label="SDK 环境"><Select value={connection.environment_id || ''} options={[{ value: '', label: '使用当前启用的环境' }, ...environments.filter(x => x.state === 'ready').map(x => ({ value: x.id, label: x.name + ' · ' + (x.details.sdk_version || '') }))]} onChange={environment_id => setConnection({ ...connection, environment_id })} /></Field>
    <Space wrap><Button onClick={save}>保存本机连接配置</Button><Button type="primary" disabled={!session && !connection.address.trim()} onClick={() => session ? onDisconnect() : onRun({ kind: 'connection' })}>{session ? '断开设备' : '连接设备'}</Button></Space>
    <p className="muted small section">连接在各页面间持续保留。调用结束不会断开；通信失效后可直接重新连接，系统重新校验实际状态。断开通信不表示设备停止。</p>
  </Panel><div className="stack"><Panel title="设备版本信息">
    {info ? <><p className="muted small">SDK {info.sdk_version} · {new Date(info.read_at).toLocaleString()} · 当前连接读取</p>{Object.entries(info.fields).map(([key, value]: [string, any]) => <div className="kv" key={key}><span>{value.label}</span><span title={value.error || value.api}>{value.status === 'ok' ? JSON.stringify(value.value) : value.status === 'unsupported' ? '当前机型 / SDK 不支持' : '读取失败：' + value.error}</span></div>)}</> : <p className="muted">连接成功后读取主控、末端和屏幕等当前机型支持的版本信息。</p>}
  </Panel><Panel title="连接状态与占用">{session ? <><StateTag state={session.state} /><p>当前会话：{session.id.slice(0, 8)}</p></> : <p>当前未连接。可直接点击连接建立新会话。</p>}{locks.map(lock => <p className="muted small" key={lock.resource}>{lock.resource} · {lock.quarantined ? '上次连接已失效，重新连接将重新校验' : '本机连接已占用'}</p>)}<p className="muted small">不同电脑访问同一控制器仍需指定唯一控制端。本机禁止重复占用。</p></Panel></div></div>;
}

export function DebugPage({ capabilities, onRun, onDraft, model, tasks, session, connection, connected, onConnect, onDisconnect }: { capabilities: any; onRun: (body: any, stay?: boolean) => void; onDraft: (doc: CaseDoc) => void; model: string; tasks: Task[]; session?: Task; connection: Connection; connected: boolean; onConnect: () => void; onDisconnect: () => void }) {
  const [name, setName] = useState('get_angles_info');
  const [args, setArgs] = useState<any[]>([]);
  const [kwargs, setKwargs] = useState<Record<string, any>>({});
  const [selectedId, setSelectedId] = useState('');
  const selected = capabilities.apis?.find((x: any) => x.name === name);
  const history = tasks.filter(t => t.kind === 'debug' && t.request.connection.model === model && t.request.connection.address === connection.address && (t.request.connection.netport || 4500) === (connection.netport || 4500) && (t.request.connection.arm || 'left') === (connection.arm || 'left'));
  const currentId = history.some(t => t.id === selectedId) ? selectedId : history[0]?.id || '';
  const { task, events } = useTask(currentId);
  const live = useTask(session?.id || '');
  const choose = (apiName: string) => {
    const entry = capabilities.apis?.find((x: any) => x.name === apiName);
    setName(apiName);
    setArgs((entry?.parameters || []).filter((p: any) => ['POSITIONAL_ONLY', 'POSITIONAL_OR_KEYWORD'].includes(p.kind)).map((p: any) => p.default ?? null));
    setKwargs(Object.fromEntries((entry?.parameters || []).filter((p: any) => p.kind === 'KEYWORD_ONLY').map((p: any) => [p.name, p.default ?? null])));
  };
  useEffect(() => { if (capabilities.apis?.length) choose(capabilities.apis.some((x: any) => x.name === name) ? name : capabilities.apis[0].name); }, [capabilities]);
  const draft = () => { const doc = newCase(model); doc.name = name + ' 调试用例'; doc.steps = [{ ...newStep('call'), api: name, args, kwargs, name: '调用 ' + name, save_as: 'result' }]; onDraft(doc); };
  return <><div className="toolbar"><Tag color={connected ? 'success' : undefined}>{connected ? '已连接 · 连续调试' : session ? '连接保留 · 等待有效读回' : '已断开'}</Tag><Button onClick={() => session ? onDisconnect() : onConnect()}>{session ? '断开设备' : '连接设备'}</Button><span className="muted small">切换页面、接口返回或参数异常不会主动断开连接</span></div>
    <div className="two-columns"><Panel title="接口与参数"><Field label="SDK 接口"><Select aria-label="SDK 接口" showSearch value={name} onChange={choose} options={(capabilities.apis || []).map((x: any) => ({ value: x.name, label: x.name }))} /></Field><p className="muted small">{capabilities.warning || '接口来源：当前 SDK ' + (capabilities.sdk_version || '未就绪')}</p>
      {args.map((value, i) => <Field key={i} label={selected?.parameters?.filter((p: any) => ['POSITIONAL_ONLY', 'POSITIONAL_OR_KEYWORD'].includes(p.kind))[i]?.name || '位置参数 ' + (i + 1)}><Argument value={value} onChange={v => setArgs(old => old.map((a, j) => j === i ? v : a))} /></Field>)}
      <Field label="位置参数（JSON 数组，支持可变参数）"><ValueInput value={args} onChange={v => { if (Array.isArray(v)) setArgs(v); }} /></Field><Field label="关键字参数（JSON 对象）"><ValueInput value={kwargs} onChange={v => { if (v && !Array.isArray(v) && typeof v === 'object') setKwargs(v); }} /></Field>
      <div className="code">device.{name}({args.map(a => JSON.stringify(a)).concat(Object.entries(kwargs).map(([k,v]) => k + '=' + JSON.stringify(v))).join(', ')})</div><Space wrap className="section"><Button type="primary" disabled={!selected || !session || !['connected','degraded'].includes(session.state) || history.some(t => !terminal.has(t.state))} onClick={() => { setSelectedId(''); onRun({ kind: 'debug', api: name, args, kwargs }, true); }}>调用接口</Button><Button onClick={draft}>转为用例草稿</Button></Space><p className="muted small">仅调用所选 SDK 方法；不自动断言、等待运动完成或恢复姿态。等待、停止等操作由你显式选择接口。状态读回异常时保留调试通道，但不会显示有效运动状态。</p>
    </Panel><Panel title="返回值与调用历史"><p>{selected?.description || '以所选 SDK 和产品规范为准。'}</p><div className="code">{selected?.signature || '未读取到接口签名'}</div><Field label="调用历史"><Select aria-label="调试调用历史" value={currentId || undefined} placeholder="首次调用后显示返回" onChange={setSelectedId} options={history.map(t => ({ value: t.id, label: new Date(t.created_at).toLocaleTimeString() + ' · ' + t.request.api + ' · ' + t.state }))} /></Field>{task && <><StateTag state={task.state} /><pre className="debug-result">{JSON.stringify(task.summary, null, 2)}</pre><Button onClick={() => { setName(task.request.api); setArgs(task.request.args || []); setKwargs(task.request.kwargs || {}); }}>复用这次参数</Button></>}</Panel></div>
    <div className="section"><Panel title="调试日志"><div className="event-log">{events.slice(-200).map(e => <div className="event-line" key={e.seq}><span>{new Date(e.time).toLocaleTimeString()}</span><span>{e.level || 'INFO'}</span><div><strong>{e.message || e.type}</strong>{['call','return'].includes(e.type) && <JsonDetails title="参数 / 返回 / 异常 / 耗时" value={e} />}</div></div>)}</div>{live.events.filter(e => e.type === 'state' || e.level === 'ERROR').slice(-3).map(e => <p className="muted small" key={e.seq}>{new Date(e.time).toLocaleTimeString()} · {e.message}</p>)}<p className="muted small">本页显示所选调用的最近 200 条事件；调用记录仍保存到本机，可在报告中心导出。</p></Panel></div>
  </>;
}

export function LatencyPage({ capabilities, onRun, taskId, onExecution }: { capabilities: any; onRun: (body: any, stay?: boolean) => void; taskId: string; onExecution: () => void }) {
  const { task, events } = useTask(taskId);
  const [name, setName] = useState('get_angles_info');
  const [args, setArgs] = useState<any[]>([]);
  const [count, setCount] = useState(10);
  const [interval, setIntervalMs] = useState(100);
  const [timeout, setTimeoutSeconds] = useState(15);
  const [motion, setMotion] = useState(false);
  const info = capabilities.apis?.find((x: any) => x.name === name);
  const choose = (value: string) => { const entry = capabilities.apis.find((x: any) => x.name === value); setName(value); setArgs((entry?.parameters || []).filter((p: any) => !p.kind.startsWith('VAR_')).map((p: any) => p.default ?? null)); };
  const samples = task?.kind === 'latency' ? task.summary?.samples || events.filter(e => e.type === 'sample') : [];
  const metric = (title: string, stats: any, failed: number) => <Panel title={title}><div className="stats-row">{[['有效样本', stats?.count ?? '—'], ['均值（ms）', stats?.mean_ms?.toFixed(3) ?? '—'], ['最大值（ms）', stats?.max_ms?.toFixed(3) ?? '—'], ['失败 / 超时', failed ?? '—']].map(([label, value]) => <div key={String(label)}><small>{label}</small><strong>{value}</strong></div>)}</div></Panel>;
  return <><div className="two-columns"><Panel title="采样配置"><Field label="被测接口"><Select aria-label="响应时间接口" showSearch value={name} onChange={choose} options={(capabilities.apis || []).map((x: any) => ({ value: x.name, label: x.name }))} /></Field>{args.map((value, i) => <Field key={i} label={info?.parameters?.[i]?.name || '参数 ' + (i + 1)}><Argument value={value} onChange={v => setArgs(args.map((a, j) => j === i ? v : a))} /></Field>)}<div className="three-columns"><Field label="采样次数"><InputNumber aria-label="采样次数" min={1} max={1000000} value={count} onChange={v => setCount(v || 1)} /></Field><Field label="间隔（ms）"><InputNumber min={0} value={interval} onChange={v => setIntervalMs(v || 0)} /></Field><Field label="超时（秒）"><InputNumber min={0.1} value={timeout} onChange={v => setTimeoutSeconds(v || 15)} /></Field></div><Checkbox checked={motion} onChange={e => setMotion(e.target.checked)}>同时统计运动完成耗时（仅运动接口）</Checkbox><div className="section"><Button type="primary" onClick={() => onRun({ kind: 'latency', api: name, args, repeat: count, interval_ms: interval, timeout, measure_motion: motion }, true)}>开始响应时间测试</Button></div></Panel><Panel title="两个独立口径"><h3>SDK 调用耗时</h3><p>执行端调用开始到返回或异常；失败样本单独记录。</p><h3>运动完成耗时</h3><p>发出运动请求到完成条件满足，包含 SDK 调用和等待；不与 SDK 耗时混算。</p><p className="muted small">采样之间协调同一设备连接。运动接口测试结束后恢复初始姿态，恢复失败进入隔离。重复相同目标可能并未产生新的位移，报告保留具体参数。</p></Panel></div>{task?.kind === 'latency' && <><div className="toolbar section"><StateTag state={task.state} /><ModeTag /><Button onClick={onExecution}>查看实时日志 / 停止</Button></div><div className="two-columns">{metric('SDK 调用统计', task.summary?.sdk, task.summary?.sdk_failures)}{metric('运动完成统计', task.summary?.motion, task.summary?.motion_failures)}</div><div className="section"><TimingCharts samples={samples} /></div><p className="muted small">图表显示最近接收的样本；最终统计由执行端使用完整有效样本计算。原始记录包含失败与超时。</p></>}</>;
}

export function AgingPage({ cases, onRun, taskId, onExecution }: { cases: CaseDoc[]; onRun: (body: any, stay?: boolean) => void; taskId: string; onExecution: () => void }) {
  const [ids, setIds] = useState<string[]>([]);
  const [cycles, setCycles] = useState(100);
  const [seconds, setSeconds] = useState(60);
  const [interval, setIntervalMs] = useState(100);
  const [failures, setFailures] = useState(3);
  const { task, events } = useTask(taskId);
  const cycle = events.filter(e => e.type === 'cycle').slice(-1)[0];
  return <div className="two-columns"><Panel title="循环场景与运行边界"><Field label="选择循环执行的用例"><Select mode="multiple" value={ids} onChange={setIds} options={cases.filter(c => c.enabled !== false).map(c => ({ value: c.id, label: c.name }))} placeholder="选择已配置恢复流程的用例" /></Field><div className="two-columns"><Field label="最大循环次数"><InputNumber min={1} value={cycles} onChange={v => setCycles(v || 1)} /></Field><Field label="最长时长（秒）"><InputNumber min={1} value={seconds} onChange={v => setSeconds(v || 1)} /></Field><Field label="循环间隔（ms）"><InputNumber min={0} value={interval} onChange={v => setIntervalMs(v || 0)} /></Field><Field label="连续失败上限"><InputNumber min={1} value={failures} onChange={v => setFailures(v || 1)} /></Field></div><p className="muted small">时长和次数任一先达到即结束；普通失败恢复成功后继续，达到连续失败阈值或恢复失败则停止。</p><Button type="primary" disabled={!ids.length} onClick={() => onRun({ kind: 'aging', case_ids: ids, repeat: cycles, duration_seconds: seconds, interval_ms: interval, max_consecutive_failures: failures }, true)}>开始老化测试</Button></Panel><Panel title="运行观察">{task?.kind === 'aging' ? <><StateTag state={task.state} /><ModeTag /><div className="stats-row"><div><small>完成循环</small><strong>{task.summary?.cycles || cycle?.cycle || 0}</strong></div><div><small>已运行（秒）</small><strong>{cycle?.elapsed_seconds?.toFixed(1) || '—'}</strong></div></div><p>循环边界可暂停；停止请求交给执行器恢复。每轮结果和异常事件保存在本机文件。</p><Button onClick={onExecution}>打开运动状态与日志</Button></> : <Empty description="启动后显示实际任务进度，不预填虚构运行数字" />}</Panel></div>;
}

export function Reports({ tasks, onExecution }: { tasks: Task[]; onExecution: (id: string) => void }) {
  const [selected, setSelected] = useState('');
  const [detail, setDetail] = useState<Task | null>(null);
  const { message } = App.useApp();
  useEffect(() => { if (selected) api<Task>('/tasks/' + selected).then(setDetail).catch(error => message.error(String(error))); }, [selected, tasks]);
  return <><Table rowKey="id" size="small" dataSource={tasks} pagination={{ pageSize: 10 }} scroll={{ x: 700 }} columns={[{ title: '任务', dataIndex: 'id', render: value => value.slice(0, 10) }, { title: '类型', dataIndex: 'kind' }, { title: '状态', dataIndex: 'state', render: value => <StateTag state={value} /> }, { title: '连接目标', render: (_, row) => row.request.connection.address }, { title: '开始时间', dataIndex: 'created_at', render: value => new Date(value).toLocaleString() }, { title: '操作', render: (_, row) => <Space wrap><Button size="small" onClick={() => setSelected(row.id)}>查看证据</Button><Button size="small" onClick={() => onExecution(row.id)}>执行记录</Button><Button size="small" href={'/api/tasks/' + row.id + '/export'}>导出</Button></Space> }]} />{detail && <div className="two-columns section"><Panel title="结果与恢复"><StateTag state={detail.state} /><pre>{JSON.stringify(detail.summary, null, 2)}</pre></Panel><Panel title="执行时版本快照"><JsonDetails title="机型、SDK、用例与公共方法版本" value={detail.snapshot} /><p className="muted small">这些数据在启动时固定，不随当前用例或 SDK 更新改变。报告、日志和原始样本只保存在本机。</p><Button href={'/api/tasks/' + detail.id + '/export'}>下载完整证据包</Button></Panel></div>}</>;
}
