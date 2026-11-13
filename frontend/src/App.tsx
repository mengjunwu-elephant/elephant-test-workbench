import { useEffect, useState } from 'react';
import { App, Alert, Button, Checkbox, Empty, Modal, Select, Space, Tag } from 'antd';
import { api, post, put } from './api';
import { CaseDoc, Connection, Model, Task, newCase, terminal, TestPlan, newPlan } from './types';
import { ErrorBox, JsonDetails, ModeTag, Panel, StateTag } from './components';
import { Builder } from './pages/Builder';
import { Execution } from './pages/Execution';
import { Library } from './pages/Library';
import { Plans } from './pages/Plans';
import { RunSetup } from './pages/RunSetup';
import { Settings } from './pages/Settings';
import { DevicePage, DebugPage, LatencyPage, AgingPage, Reports } from './pages/Tools';

const pages = ['工作台', '设备管理', '接口调试', '用例库', '用例编排', '测试执行', '响应时间', '老化测试', '报告中心', '公共方法'];
const descriptions: Record<string, string> = { 工作台: '选择机型，开始测试，跟进当前任务与本机结果。', 设备管理: '机型能力、连接信息与实际连接目标的占用。', 接口调试: '选择当前环境中的 pymycobot 接口，填写参数并查看返回。', 用例库: '编写、核对和管理单条用例；版本与同步独立管理。', 用例编排: '测试计划：组合已有用例，固定版本、顺序、数据组与重复次数。', 测试执行: '配置本次运行、预检和启动，再观察过程、断言与恢复。', 响应时间: 'SDK 调用耗时与运动完成耗时分别计时、统计和展示。', 老化测试: '运行可重复场景，观察长期稳定性及异常恢复。', 报告中心: '本机保存的测试结果、原始证据与执行版本快照。', 公共方法: '复杂场景由开发人员补充方法，测试人员选择并填写参数。' };

export default function Workbench() {
  const { message, modal } = App.useApp();
  const [page, setPage] = useState(pages.includes(decodeURIComponent(location.hash.slice(1))) ? decodeURIComponent(location.hash.slice(1)) : '工作台');
  const [models, setModels] = useState<Model[]>([]);
  const [connection, setConnection] = useState<Connection>({ mode: 'hardware', model: 'ultraarm_p1', address: '', baudrate: 115200, environment_id: '', arm: 'left', netport: 4500, connect_timeout: 8 });
  const [cases, setCases] = useState<CaseDoc[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [methods, setMethods] = useState<any[]>([]);
  const [environments, setEnvironments] = useState<any[]>([]);
  const [locks, setLocks] = useState<any[]>([]);
  const [settings, setSettings] = useState<any>({});
  const [shared, setShared] = useState<any>({});
  const [capabilities, setCapabilities] = useState<any>({ apis: [] });
  const [error, setError] = useState('');
  const [capError, setCapError] = useState('');
  const [draft, setDraft] = useState<CaseDoc>(newCase('ultraarm_p1'));
  const [plans, setPlans] = useState<TestPlan[]>([]);
  const [planDraft,setPlanDraft] = useState<TestPlan>(newPlan('ultraarm_p1'));
  const [editing,setEditing] = useState(false);
  const [runSelection,setRunSelection] = useState<any>({kind:'cases',case_ids:[]});
  const [taskId, setTaskId] = useState('');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const id = setInterval(() => setNow(Date.now()), 500); return () => clearInterval(id); }, []);
  const navigate = (target: string) => { setPage(target); history.replaceState(null, '', '#' + encodeURIComponent(target)); window.scrollTo(0, 0); };
  const refresh = async () => {
    try {
      const [documents, runs, envs, config, updates, held, savedPlans] = await Promise.all([api<CaseDoc[]>('/cases'), api<Task[]>('/tasks'), api<any[]>('/environments'), api('/settings'), api('/shared/status'), api<any[]>('/locks'), api<TestPlan[]>('/plans')]);
      setPlans(savedPlans); setCases(documents); setTasks(runs); setEnvironments(envs); setSettings(config); setShared(updates); setLocks(held); setError('');
    } catch (error) { setError(String(error)); }
  };
  useEffect(() => {
    api<Model[]>('/models').then(setModels).catch(error => setError(String(error)));
    api<any[]>('/methods').then(setMethods).catch(error => setError(String(error)));
    api('/settings').then(config => { if (config.connection) setConnection(config.connection); }).catch(error => setError(String(error)));
    refresh(); const id = setInterval(refresh, 1000); return () => clearInterval(id);
  }, []);
  useEffect(() => {
    let abandoned = false;
    api('/apis?model=' + connection.model + '&mode=' + connection.mode + '&environment_id=' + (connection.environment_id || '')).then(data => { if (!abandoned) { setCapabilities(data); setCapError(''); } }).catch(error => { if (!abandoned) { setCapabilities({ apis: [] }); setCapError(String(error)); } });
    return () => { abandoned = true; };
  }, [connection.model, connection.mode, connection.environment_id, environments.filter(e => e.active).map(e => e.id).join(',')]);
  const visibleCases = cases.filter(c => c.model === connection.model);
  const selectedModel = models.find(m => m.id === connection.model);
  const edit = (doc: CaseDoc) => { setDraft(doc); setEditing(true); navigate('用例库'); };
  const prepareRun = (body:any) => { setRunSelection({...body,kind:body.kind||'cases'}); navigate('测试执行'); };
  const addToPlan = (ids:string[]) => { const base=planDraft.model===connection.model?planDraft:newPlan(connection.model); setPlanDraft({...base,items:[...base.items,...ids.map(case_id=>({item_id:crypto.randomUUID(),case_id,content_hash:'',dataset_indices:[],overrides:{},repeat:1}))]}); navigate('用例编排'); };
  const testTasks=tasks.filter(t=>!['connection','debug'].includes(t.kind));
  const selectedTest=testTasks.find(t=>t.id===taskId)?.id || testTasks[0]?.id || '';
  const launch = async (body:any) => {try{const result=await post('/tasks',{...body,connection,hardware_confirmed:true});setTaskId(result.id);await refresh();}catch(e){message.error(String(e));throw e;}};
  const session = tasks.find(t => t.kind === 'connection' && !terminal.has(t.state) && t.request.connection.model === connection.model && t.request.connection.address === connection.address && (t.request.connection.netport || 4500) === (connection.netport || 4500));
  const transport = session || tasks.find(t => t.kind !== 'connection' && !terminal.has(t.state) && t.request.connection.model === connection.model && t.request.connection.address === connection.address && (t.request.connection.netport || 4500) === (connection.netport || 4500));
  const checked = !!transport && (transport.kind === 'connection' ? transport.state === 'connected' : !['queued','connecting'].includes(transport.state)) && transport.telemetry?.quality === 'ok' && typeof transport.telemetry.moving === 'boolean' && now - Date.parse(transport.telemetry.time) < 4000;
  const connectionState = checked ? '已连接' : session?.state === 'connecting' || session?.state === 'queued' ? '连接校验中' : session?.state === 'disconnecting' ? '正在断开' : transport ? (session?.state === 'degraded' ? '状态读取重试中' : '连接保留 · 状态待更新') : '已断开';
  const disconnect = async () => { if (!session) { if (transport) { message.warning('请先在执行页停止任务并完成恢复，再断开'); setTaskId(transport.id); navigate('测试执行'); } return; } try { await post('/tasks/' + session.id + '/control', { action: 'disconnect' }); await refresh(); } catch (error) { message.error(String(error)); } };
  const start = async (body: any, stay = false) => {
    if (!connection.address.trim()) { message.warning('请先在设备管理配置实机连接'); navigate('设备管理'); return; }
    const occupied = tasks.find(t => !terminal.has(t.state) && t.request.connection.address === connection.address && (t.request.connection.netport || 4500) === (connection.netport || 4500) && (t.kind !== 'connection' || !['connected','degraded'].includes(t.state) || body.kind === 'connection'));
    if (occupied) { message.warning('此连接已有任务或正在校验，已打开对应执行记录'); setTaskId(occupied.id); navigate('测试执行'); return; }
    if (body.kind !== 'connection' && !(body.kind === 'debug' ? session && ['connected','degraded'].includes(session.state) : checked)) { message.warning('请先连接设备并完成状态校验'); navigate('设备管理'); return; }
    let includeManual = body.include_manual || false;
    const perform = async () => {
      try {
        const result = await post('/tasks', { ...body, connection, include_manual: includeManual, hardware_confirmed: true });
        setTaskId(result.id); await refresh(); if (!stay && body.kind !== 'connection') navigate('测试执行');
      } catch (error) { message.error(String(error)); throw error; }
    };
    if (body.kind === 'debug') { await perform(); return; }
    modal.confirm({ title: body.kind === 'connection' ? '确认连接实机' : '确认执行实机任务', content: <div><p>机型：{selectedModel?.name}</p><p>连接：{connection.address}{selectedModel?.connection === 'ip' ? ':' + (connection.netport || 4500) : ''}。{body.kind === 'connection' ? '建立连接并持续读取位置与运动状态。校验通过后保持连接，后续接口任务复用同一连接。' : '任务可能产生真实动作；确认连接、现场条件与停止手段可用后执行。'}</p>{body.all_cases && <Checkbox onChange={e => { includeManual = e.target.checked; }}>包含需要人工参与的用例（执行时等待明确确认）</Checkbox>}<p className="small muted">仅执行本项目中适用于当前机型的用例。</p></div>, okText: '确认开始', cancelText: '取消', onOk: perform });
  };
  const active = tasks.filter(t => !['connection','debug'].includes(t.kind) && !terminal.has(t.state));
  return <div className="workbench"><header className="app-header"><div className="brand">elephant <span>/</span> pytest</div><Tag>测试工作台 · 0.5.0</Tag><div className="header-right"><span className="muted small">当前机型</span><Select aria-label="当前机型" value={connection.model} style={{ minWidth: 185 }} options={models.map(m => ({ value: m.id, label: m.name }))} onChange={async model => { const next = { ...connection, model, address: '', second_address: '' }; setConnection(next); try { await put('/settings/connection', next); } catch (error) { message.error(String(error)); } }} /><Tag role="status" aria-label="设备连接状态" color={checked ? 'success' : session ? 'processing' : undefined}>{connectionState}</Tag><Button aria-label={transport ? '断开设备' : '连接设备'} disabled={session?.state === 'disconnecting'} onClick={() => transport ? disconnect() : start({ kind: 'connection' })}>{transport ? '断开' : '连接'}</Button><Button aria-label="设置" onClick={() => setSettingsOpen(true)}>设置</Button></div></header>
    <div className="app-shell"><aside className="sidebar"><div className="nav-caption">WORKSPACE / 测试工作台</div><nav>{pages.map((p, i) => <button key={p} aria-current={p === page ? 'page' : undefined} className={p === page ? 'active' : ''} onClick={() => navigate(p)}><span>{String(i + 1).padStart(2, '0')}</span>{p}</button>)}</nav><div className="sidebar-note">本机执行与结果保存<br />用例随源码 Git 同步<br />SDK 通过离线 wheel 更新</div></aside><main className="main-content"><div className="eyebrow">ELEPHANT TEST WORKSPACE</div><div className="page-heading"><h1>{page}</h1>{active.length > 0 && <Button size="small" onClick={() => { setTaskId(active[0].id); navigate('测试执行'); }}>{active.length} 个进行中的任务</Button>}</div><p className="page-description">{descriptions[page]}</p><ErrorBox text={error} />{capError && ['接口调试', '用例库', '响应时间'].includes(page) && <div className="section"><Alert type="warning" title={capError} action={<Button size="small" onClick={() => setSettingsOpen(true)}>运行环境</Button>} /></div>}
      {page === '工作台' && <><div className="dashboard-grid"><section><div className="hero"><p className="accent small">当前机型 / {selectedModel?.name || connection.model}</p><h2>从一次接口验证开始</h2><p className="muted">调试参数，沉淀用例，再扩展到设备回归、响应时间和长期老化。任务记录与证据保存在本机。</p><Space wrap><Button type="primary" onClick={() => navigate('接口调试')}>接口调试</Button><Button onClick={() => navigate('用例库')}>选择用例</Button><Button onClick={() => prepareRun({ kind: 'cases', all_cases: true, case_ids:visibleCases.filter(c=>c.enabled!==false).map(c=>c.id) })}>设备全用例</Button></Space></div><section className="section"><h2>常用工作流程</h2>{[['01', '调试接口', '填写参数，查看返回与异常', '接口调试'], ['02', '沉淀用例', '编辑单例、固定版本并组成测试计划', '用例库'], ['03', '执行与追溯', '运动状态、日志与恢复结论相互关联', '测试执行']].map(x => <button className="workflow-row" key={x[0]} onClick={() => navigate(x[3])}><small>{x[0]}</small><span><strong>{x[1]}</strong><small>{x[2]}</small></span><span>→</span></button>)}</section></section><div className="stack"><Panel title="本机概览"><div className="stats-row"><div><small>当前机型用例</small><strong>{visibleCases.length}</strong></div><div><small>本机任务</small><strong>{tasks.length}</strong></div><div><small>运行中</small><strong>{active.length}</strong></div></div>{tasks.length ? <><StateTag state={tasks[0].state} /><p>{tasks[0].snapshot.cases?.[0]?.name || tasks[0].request.api || tasks[0].kind}</p><Button onClick={() => { setTaskId(tasks[0].id); navigate('测试执行'); }}>查看最近任务</Button></> : <p className="muted">暂无执行记录。请先配置实机与 SDK，再创建用例或从团队用例库启用版本。</p>}</Panel><Panel title="专项测试"><div className="link-row"><div>响应时间<small>SDK 与运动完成分别统计</small></div><Button type="link" onClick={() => navigate('响应时间')}>进入</Button></div><div className="link-row"><div>老化测试<small>循环场景、运行边界与异常恢复</small></div><Button type="link" onClick={() => navigate('老化测试')}>进入</Button></div></Panel></div></div>{shared.releases?.length > 0 && <Alert className="section" type="info" title="局域网用例版本已检查，请在用例库选择启用。运行中的任务不会自动切换。" action={<Button onClick={() => navigate('用例库')}>用例库</Button>} />}</>}
      {page === '设备管理' && <DevicePage session={session} onDisconnect={disconnect} onRun={start} connection={connection} setConnection={setConnection} models={models} environments={environments} locks={locks} refresh={refresh} />}
      {page === '接口调试' && <DebugPage tasks={tasks} session={session} connection={connection} connected={checked} onConnect={() => start({ kind: 'connection' })} onDisconnect={disconnect} capabilities={capabilities} onRun={start} onDraft={edit} model={connection.model} />}
      {page === '用例库' && (editing ? <><div className="toolbar"><Button onClick={()=>setEditing(false)}>返回用例列表</Button><Tag>用例库 / 单例编辑</Tag></div><Builder document={draft} setDocument={setDraft} cases={cases} capabilities={capabilities} methods={methods} onSaved={refresh} onRun={prepareRun} /></> : <Library cases={visibleCases} onEdit={edit} onNew={() => edit(newCase(connection.model))} onRun={prepareRun} onAddToPlan={addToPlan} refresh={refresh} shared={shared} openSettings={() => setSettingsOpen(true)} />)}
      {page === '用例编排' && <Plans plans={plans} cases={cases} model={connection.model} document={planDraft.model===connection.model?planDraft:newPlan(connection.model)} setDocument={setPlanDraft} refresh={refresh} onRun={prepareRun} onEdit={edit} />}
      {page === '测试执行' && <><RunSetup selection={runSelection} setSelection={setRunSelection} cases={cases} plans={plans} connection={connection} connected={checked} onConnect={()=>start({kind:'connection'})} onStart={launch} /><div className="section"><Execution taskId={selectedTest} tasks={testTasks} onSelect={setTaskId} onReport={() => navigate('报告中心')} /></div></>}
      {page === '响应时间' && <LatencyPage capabilities={capabilities} onRun={prepareRun} taskId={taskId} onExecution={() => navigate('测试执行')} />}
      {page === '老化测试' && <AgingPage cases={visibleCases} onRun={prepareRun} taskId={taskId} onExecution={() => navigate('测试执行')} />}
      {page === '报告中心' && <Reports tasks={tasks} onExecution={id => { setTaskId(id); navigate('测试执行'); }} />}
      {page === '公共方法' && <div className="two-columns"><section>{methods.map(method => <Panel key={method.id} title={method.name} extra={<Tag>{method.version}</Tag>}><p>{method.description}</p><JsonDetails title="输入参数与默认值" value={method.parameters} /></Panel>)}</section><Panel title="扩展约定"><p>公共方法由开发人员补充实现并登记名称、参数、版本和适用条件。测试人员在编排页选择方法，引用前序结果。</p><p>共享用例携带方法版本要求；本机版本不匹配时拒绝启动，不静默替换。</p><p className="muted small">SDK 接口目录从所选环境读取，新增 SDK 方法无需在网页中编写 Python；复杂逻辑仍需要开发与验证。</p></Panel></div>}
      <footer>本机运行 / 用例 Git 管理 / 结果本地保存<span>Windows 与 Linux 源码启动 · 离线 SDK 环境独立管理</span></footer>
    </main></div><Settings open={settingsOpen} close={() => setSettingsOpen(false)} settings={settings} environments={environments} refresh={refresh} /></div>;
}
