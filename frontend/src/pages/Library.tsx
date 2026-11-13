import { useEffect, useState } from 'react';
import { App, Alert, Button, Input, Modal, Select, Space, Table, Tag, Upload } from 'antd';
import { api, post, put, downloadJson } from '../api';
import { CaseDoc, categoryNames } from '../types';
import { JsonDetails, Panel } from '../components';

export function Library({ cases, onEdit, onNew, onRun, refresh, onAddToPlan }: { cases: CaseDoc[]; onEdit: (doc: CaseDoc) => void; onNew: () => void; onRun: (body: any) => void; refresh: () => void; onAddToPlan: (ids:string[])=>void; shared: any; openSettings: () => void }) {
  const { message, modal } = App.useApp();
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('active');
  const [selected, setSelected] = useState<string[]>([]);
  const [repository, setRepository] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [commitMessage, setCommitMessage] = useState('');
  const [ledger,setLedger]=useState<any>(null);
  const [sources, setSources] = useState<any[] | null>(null);
  const check = async () => { try { setRepository(await api('/case-repository')); } catch (error) { message.error(String(error)); } };
  useEffect(() => { check(); api('/remediation').then(setLedger).catch(()=>{}); }, []);
  const act = async (fn: () => Promise<any>, success?: string) => { setBusy(true); try { await fn(); if (success) message.success(success); refresh(); await check(); } catch (error) { message.error(String(error)); } finally { setBusy(false); } };
  const git = (action: string) => modal.confirm({ title: {commit:'提交用例到本地 Git',push:'推送当前分支到已配置远端',pull:'从远端快进更新源码与用例'}[action], content: action === 'pull' ? '需整个源码工作区干净且本机用例已写回；不自动合并、不覆盖本机编辑。拉取会更新当前分支，平台源码变化需重启生效。' : action === 'push' ? '推送当前分支全部已提交内容。请先审查源码仓库的待推送提交；本机运行数据不属于用例文件。' : '仅提交本项目 case_library 中的用例资产；其他源码变更仍由你在终端审查提交。', onOk: () => act(() => post('/case-repository/git',{ action, message:commitMessage }), 'Git 操作完成') });
  const visible = cases.filter(c => {
    const retired=!!c.migration?.superseded_by?.length||!!c.migration?.archived;
    const review=c.steps.some(s=>s.kind==='review');
    return (filter==='all'||filter==='active'&&!retired||filter==='review'&&review&&!retired||filter==='converted'&&!review&&c.enabled===false||filter==='enabled'&&c.enabled!==false||filter==='archived'&&retired) && (c.name+JSON.stringify(c.migration||{})+JSON.stringify(c.steps)).toLowerCase().includes(search.toLowerCase());
  });
  const enabled = cases.filter(c => c.enabled !== false);
  return <>
    <details className="section"><summary>版本与同步</summary><Panel title="用例文件与 Git 同步" extra={<Button onClick={check}>刷新状态</Button>}>
      <p>用例随本项目源码仓库管理。先保存草稿，再写回文件、审查差异、提交，最后按需推送。设备配置、SDK、日志和结果保留在本机。</p>
      {repository && <><div className="toolbar"><Tag>{repository.file_count} 份仓库用例</Tag><Tag>{repository.local_changes} 份本机变更</Tag><Tag>{repository.disk_changes} 份文件变化</Tag><Tag>{repository.has_remote ? '已配置远端' : '尚未配置 Git 远端'}</Tag></div><Space wrap><Button loading={busy} onClick={() => act(() => post('/case-repository/load'), '仓库用例已载入；现有编辑冲突会阻断覆盖')}>从文件载入用例</Button><Button disabled={!repository.git_available} loading={busy} onClick={() => act(() => post('/case-repository/save'), '草稿已写回用例文件')}>写回用例文件</Button></Space><div className="toolbar section"><Input aria-label="用例提交说明" placeholder="填写本次用例修改说明" value={commitMessage} onChange={e => setCommitMessage(e.target.value)} style={{ width:280 }} /><Button disabled={!repository.git_available || !commitMessage.trim() || busy} onClick={() => git('commit')}>提交用例</Button><Button disabled={!repository.has_remote || busy} onClick={() => git('push')}>推送</Button><Button disabled={!repository.has_remote || busy} onClick={() => git('pull')}>拉取更新</Button></div><details><summary>查看文件状态与已跟踪差异</summary><pre className="debug-result">{repository.git_status || '没有用例文件变更'}</pre><pre className="debug-result">{repository.diff || '未跟踪文件请先在源码目录审查；提交后可通过 Git 查看完整历史。'}</pre></details></>}
    </Panel></details>
    <div className="toolbar section"><Input.Search aria-label="搜索用例" value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索用例、接口或原文件" style={{ width:270 }} /><Select value={filter} onChange={setFilter} style={{ width:150 }} options={[{value:'active',label:'当前用例'},{value:'all',label:'全部含原迁移条目'},{value:'enabled',label:'已启用'},{value:'converted',label:'已转换待启用'},{value:'review',label:'待完善 / 阻断'},{value:'archived',label:'已归档 / 已承接'}]} /><Button onClick={() => act(async () => downloadJson('elephant-cases.json',await api('/cases-export')))}>导出用例包</Button><Upload accept=".json" showUploadList={false} beforeUpload={file => { file.text().then(text => act(() => post('/cases-import',JSON.parse(text)),'已导入为草稿')).catch(error => message.error(String(error))); return false; }}><Button>导入用例包</Button></Upload><Button type="primary" onClick={onNew}>新增用例</Button></div>
    {cases.some(c => c.migration?.workbook) && <Alert className="section" type="info" title={`当前机型：${cases.length} 份用例，${cases.reduce((n,c) => n + c.datasets.length,0)} 组参数；${enabled.length} 份已启用`} description="旧用例参数和逻辑依据已迁入。待完善条目需要补全前置、断言及恢复后启用，不能直接执行；点击来源可核对原始逻辑。" />}
    {ledger?.summary&&<details className="section"><summary>全库整改进度与逐例问题</summary><p>{ledger.summary.fully_remediated_originals} 个原条目已完整承接 · {ledger.summary.generated_scenarios} 份新场景 · {ledger.summary.blocked_originals} 个原条目仍阻断 · {ledger.summary.unmapped_source_files} 份来源待核查</p><p>转换完成与实机验收分开；新场景需人工启用。本轮没有实机验收证据。</p><Button onClick={()=>downloadJson('remediation-ledger.json',ledger)}>下载完整整改台账</Button>{ledger.entries?.filter((e:any)=>cases.some(c=>c.id===e.original_id)).map((e:any)=><details key={e.original_id}><summary>{e.workbook} / {e.sheet} · {e.state}</summary><pre className="debug-result">{JSON.stringify(e,null,2)}</pre></details>)}</details>}
    <div className="toolbar section"><Tag>已选 {selected.length} 条</Tag><Button disabled={!selected.length} onClick={() => onRun({ kind:'cases',case_ids:selected })}>准备运行所选用例</Button><Button disabled={!selected.length} onClick={()=>onAddToPlan(selected)}>加入测试计划</Button><Button disabled={!enabled.length} onClick={() => onRun({ kind:'cases',all_cases:true })}>准备运行设备已启用用例</Button></div>
    <Table rowKey="id" size="middle" dataSource={visible} rowSelection={{ selectedRowKeys:selected,onChange:values => setSelected(values as string[]),getCheckboxProps:row => ({ disabled:!!row.migration?.superseded_by?.length }) }} pagination={{ pageSize:12 }} scroll={{ x:750 }} columns={[
      { title:'用例',dataIndex:'name',render:(value,row) => <><strong>{value}</strong>{row.migration?.sheet && <small className="block muted">{row.migration.workbook} · {row.datasets.length} 组参数</small>}</> },
      { title:'场景',dataIndex:'category',render:value => categoryNames[value] },
      { title:'状态 / 版本',render:(_,row) => <><Tag color={row.enabled === false ? 'warning' : 'success'}>{row.migration?.superseded_by?.length ? '已拆分承接' : row.steps.some(s=>s.kind==='review') ? '待完善' : '已转换 / 已编写'}</Tag><Tag>{row.enabled===false?'未启用':'已启用'}</Tag>{row.migration?.workbook&&<Tag>实机未验收</Tag>}v{row.revision}</> },
      { title:'操作',render:(_,row) => <Space wrap><Button size="small" onClick={() => onEdit(structuredClone(row))}>查看 / 编辑</Button>{row.migration?.workbook && <Button size="small" onClick={async () => { try { setSources(await api<any[]>('/case-repository/source/'+row.id)); } catch(error) { message.error(String(error)); } }}>迁移来源</Button>}<Button size="small" onClick={()=>act(()=>put('/cases/'+row.id,{...row,enabled:false,migration:{...row.migration,archived:!row.migration?.archived}}),'归档状态已保存，原版本与来源保留')}>{row.migration?.archived?'取消归档':'归档'}</Button><Button size="small" onClick={() => act(async () => onEdit(await post<CaseDoc>('/cases/'+row.id+'/fork')))}>复制</Button></Space> },
    ]} />
    <p className="muted small">执行使用启动时快照；文件同步与后续编辑不会改变历史结果。Git 冲突及未写回编辑不会被自动覆盖。</p>
    <Modal title="原测试逻辑与恢复依据（仅查看，不执行）" open={sources !== null} onCancel={() => setSources(null)} footer={null} width={1000}>{sources?.length ? sources.map((s:any) => <details key={s.file}><summary>{s.file}</summary><pre className="debug-result">{s.text}</pre></details>) : <p>此工作表未找到可静态关联的测试文件，请核对迁移清单。</p>}</Modal>
  </>;
}
