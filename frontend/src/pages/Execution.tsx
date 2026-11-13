import { useEffect, useMemo, useState } from 'react';
import { App, Button, Checkbox, Empty, Input, Select, Space, Tag } from 'antd';
import { api, post } from '../api';
import { Chart, JsonDetails, ModeTag, Panel, StateTag } from '../components';
import { Task, WorkEvent, terminal } from '../types';

export function useTask(taskId: string) {
  const [task, setTask] = useState<Task | null>(null);
  const [events, setEvents] = useState<WorkEvent[]>([]);
  const [streamState, setStreamState] = useState('未连接');
  useEffect(() => {
    setTask(null); setEvents([]);
    if (!taskId) return;
    let disposed = false;
    let socket: WebSocket;
    let retry: ReturnType<typeof setTimeout>;
    const refresh = () => api<Task>('/tasks/' + taskId).then(row => { if (!disposed) setTask(row); }).catch(() => { if (!disposed) setStreamState('任务读取失败'); });
    refresh();
    const poll = setInterval(refresh, 1000);
    const connect = () => {
      if (disposed) return;
      socket = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/tasks/${taskId}/stream`);
      socket.onopen = () => setStreamState('已连接');
      socket.onmessage = event => {
        const item = JSON.parse(event.data);
        if (item.type === 'heartbeat') return;
        setEvents(old => { const all = new Map(old.map(x => [x.seq, x])); all.set(item.seq, item); return [...all.values()].sort((a, b) => a.seq - b.seq).slice(-3000); });
        if (['finished', 'complete', 'confirmation', 'state'].includes(item.type)) refresh();
      };
      socket.onclose = () => { if (!disposed) { setStreamState('已断开 · 正在重连'); retry = setTimeout(connect, 1500); } };
      socket.onerror = () => socket.close();
    };
    connect();
    return () => { disposed = true; clearInterval(poll); clearTimeout(retry); socket?.close(); };
  }, [taskId]);
  return { task, events, streamState };
}

export function Execution({ taskId, tasks, onSelect, onReport }: { taskId: string; tasks: Task[]; onSelect: (id: string) => void; onReport: () => void }) {
  const { message } = App.useApp();
  const { task, events, streamState } = useTask(taskId);
  const [level, setLevel] = useState('全部');
  const [search, setSearch] = useState('');
  const [follow, setFollow] = useState(true);
  const [tick, setTick] = useState(Date.now());
  useEffect(() => { const id = setInterval(() => setTick(Date.now()), 500); return () => clearInterval(id); }, []);
  useEffect(() => { if (follow) { const el = document.getElementById('live-events'); if (el) el.scrollTop = el.scrollHeight; } }, [events, follow]);
  const control = async (action: string, extra = {}) => { try { await post('/tasks/' + taskId + '/control', { action, ...extra }); message.success('操作已提交'); } catch (error) { message.error(String(error)); } };
  if (!task) return <><Select aria-label="选择任务" value={taskId || undefined} placeholder="选择历史任务" onChange={onSelect} options={tasks.map(t => ({ value: t.id, label: `${t.kind} · ${t.id.slice(0, 8)}` }))} style={{ width: 320 }} /><Empty description="从上方配置开始本次运行，或选择历史测试任务" /></>;
  const done = terminal.has(task.state);
  const tel = task.telemetry || {};
  const fresh = tel.quality === 'ok' && tick - Date.parse(tel.time) < 2500;
  const motion = !fresh ? '状态未知 / 数据已过期' : tel.moving === true ? '运动中' : tel.moving === false ? '停止状态读回' : '运动状态不支持';
  const calls = events.filter(x => x.type === 'call' && ['set_angle', 'send_angle', 'set_angles', 'send_angles'].includes(x.api));
  const lastMotion = calls[calls.length - 1];
  const target = (i: number) => lastMotion?.api?.endsWith('angles') ? lastMotion?.args?.[0]?.[i] : lastMotion?.args?.[0] === i + 1 ? lastMotion?.args?.[1] : undefined;
  const filtered = events.filter(e => (level === '全部' || (e.level || 'INFO') === level) && JSON.stringify(e).toLowerCase().includes(search.toLowerCase()));
  const confirmation = task.summary?.confirmation;
  const results = task.summary?.results || events.filter(e => e.type === 'case_result');
  return <>
    <div className="toolbar"><Select aria-label="选择任务" value={taskId} onChange={onSelect} options={tasks.map(t => ({ value: t.id, label: `${t.kind} · ${t.id.slice(0, 8)} · ${t.created_at.slice(11, 19)}` }))} style={{ width: 290 }} /><StateTag state={task.state} /><ModeTag /><Space className="toolbar-right">{['cases','plan','aging'].includes(task.kind) && <><Button disabled={done || task.state === 'paused'} onClick={() => control('pause')}>循环边界暂停</Button><Button disabled={task.state !== 'paused'} onClick={() => control('resume')}>继续</Button></>}<Button danger disabled={done} onClick={() => control(task.kind === 'connection' ? 'disconnect' : 'stop')}>{task.kind === 'connection' ? '断开连接' : '请求停止'}</Button></Space></div>
    <div className="monitor-grid"><div className="stack"><Panel title="实时运动状态" extra={<Tag>{tel.quality || '等待首个读回'}</Tag>}>
      <div className="motion-header"><h2>{done ? '任务已结束 · 最后快照' : motion}</h2><span className="muted small">{tel.time ? new Date(tel.time).toLocaleTimeString() : '尚未采样'}</span></div>
      <p className="muted small">{done ? '以下是该任务的历史读回；当前设备连接状态以右上角全局状态为准。' : fresh ? '同一执行端协调状态读取，没有另开 SDK 连接。' : '连接中断、数据过期或能力不支持时，不能把最后读数视为当前状态。'}</p>
      {Array.isArray(tel.positions) ? <div className="table-scroll"><table className="data-table"><thead><tr><th>关节</th><th>读回角度（°）</th><th>目标（°）</th><th>差值（°）</th></tr></thead><tbody>{tel.positions.map((v: any, i: number) => <tr key={i}><td>J{i + 1}</td><td>{typeof v === 'number' ? v.toFixed(2) : String(v)}</td><td>{typeof target(i) === 'number' ? target(i).toFixed(2) : '—'}</td><td>{typeof v === 'number' && typeof target(i) === 'number' ? Math.abs(v - target(i)).toFixed(2) : '—'}</td></tr>)}</tbody></table></div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={tel.message || '没有可用位置读回；不以零值代替'} />}
    </Panel><Panel title="实时日志" extra={<span className="small muted">事件流：{streamState}</span>}><div className="toolbar"><Select aria-label="日志级别" value={level} onChange={setLevel} style={{ width: 110 }} options={['全部', 'INFO', 'WARN', 'ERROR'].map(value => ({ value, label: value }))} /><Input.Search aria-label="筛选日志" placeholder="搜索接口、步骤或错误" value={search} onChange={e => setSearch(e.target.value)} style={{ width: 240 }} /><Checkbox checked={follow} onChange={e => setFollow(e.target.checked)}>跟随最新</Checkbox></div><div className="event-log" id="live-events">{filtered.map(e => <div className={'event-line ' + ((e.level === 'ERROR') ? 'error' : '')} key={e.seq}><span>{new Date(e.time).toLocaleTimeString()}</span><span>{e.level || 'INFO'}</span><div><strong>{e.message || e.type}</strong>{['return', 'assertion', 'sample'].includes(e.type) && <JsonDetails title="参数 / 返回 / 证据" value={e} />}</div></div>)}</div><p className="small muted">页面最多保留最近 3,000 条事件；导出任务包含完整原始文件。</p></Panel></div>
      <div className="stack"><Panel title="任务与判定"><StateTag state={task.state} /><Tag>{task.snapshot.provenance?.source==='saved_git'?'已提交资产':'本地草稿 / 历史来源'}</Tag><p>{task.snapshot.plan?.name || task.snapshot.cases?.[0]?.name || task.request.api || task.kind}</p><div className="kv"><span>机型</span><span>{task.snapshot.model}</span></div><div className="kv"><span>用例快照</span><span>{task.snapshot.cases?.length || 0} 条 · 启动时固定</span></div><div className="kv"><span>SDK 环境</span><span>{task.snapshot.environment?.sdk_version || '未知'}</span></div>{task.summary?.counts && <div className="kv"><span>结果</span><span>通过 {task.summary.counts.passed} / 失败 {task.summary.counts.failed} / 跳过 {task.summary.counts.skipped}</span></div>}{confirmation && <div className="confirmation"><p>{confirmation.message}</p><Space wrap><Button type="primary" onClick={() => control('confirm', { id: confirmation.id, answer: 'continue' })}>确认继续</Button><Button onClick={() => control('confirm', { id: confirmation.id, answer: 'skip' })}>跳过并恢复</Button></Space></div>}{task.summary?.blocked && <p className="error">连接已失效，故障记录已保留。可在设备管理重新连接并校验状态。</p>}<details open={task.state === 'failed' || task.state === 'blocked'}><summary>用例结果与恢复</summary>{results.map((r: any, i: number) => <div className="result-item" key={i}><strong>{r.name}</strong><small className="block muted">条目 {r.entry?.item_id} · 重复 {r.entry?.repeat_index} · 数据组 {(r.entry?.dataset_index??0)+1}</small><Tag>{r.state}</Tag><p>恢复：{r.restore_error ? '失败：' + r.restore_error : r.restored ? '已验证成功' : '未配置恢复步骤'}</p>{r.error && <p className="error">{r.error}</p>}</div>)}</details><div className="section"><Button onClick={onReport}>查看本机报告</Button></div></Panel><Panel title="停止与恢复约定"><p className="small muted">停止请求先交给执行器处理恢复；若 SDK 阻塞超过停止等待时间，进程终止后仍标记状态待核实，不等同于设备已经停止。</p><JsonDetails title="任务参数与版本快照" value={task.snapshot} /></Panel></div>
    </div>
  </>;
}

export function TimingCharts({ samples }: { samples: any[] }) {
  const sdk = useMemo(() => samples.filter(s => s.sdk_status === 'ok' && typeof s.sdk_ms === 'number'), [samples]);
  const motion = useMemo(() => samples.filter(s => s.motion_status === 'ok' && typeof s.motion_ms === 'number'), [samples]);
  const option = (rows: any[], key: string, title: string) => ({ animation: false, color: ['#285f9c'], tooltip: { trigger: 'axis' as const }, grid: { left: 65, right: 20, bottom: 55, top: 25 }, xAxis: { type: 'category' as const, name: '采样序号（次）', nameLocation: 'middle' as const, nameGap: 32, data: rows.map(s => s.index) }, yAxis: { type: 'value' as const, name: title + '（ms）' }, series: [{ name: title, type: 'line' as const, data: rows.map(s => s[key]), showSymbol: rows.length < 50 }] });
  return <div className="two-columns"><Panel title="SDK 调用耗时（ms）">{sdk.length ? <Chart option={option(sdk, 'sdk_ms', 'SDK 调用')} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚无成功 SDK 样本" />}</Panel><Panel title="运动完成耗时（ms）">{motion.length ? <Chart option={option(motion, 'motion_ms', '运动完成')} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="未请求或尚无成功运动完成样本" />}</Panel></div>;
}
