import { useEffect, useState } from 'react';
import { App, Button, Drawer, Input, InputNumber, Select, Space, Table, Tabs, Tag, Upload } from 'antd';
import type { UploadFile } from 'antd';
import { api, post, put } from '../api';
import { Field, JsonDetails, Panel } from '../components';

export function Settings({ open, close, settings, environments, refresh }: { open: boolean; close: () => void; settings: any; environments: any[]; refresh: () => void }) {
  const { message } = App.useApp();
  const [files, setFiles] = useState<UploadFile[]>([]);
  const [busy, setBusy] = useState(false);
  const [url, setUrl] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [newUser, setNewUser] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [newRole, setNewRole] = useState('tester');
  useEffect(() => { if (open) { setUrl(settings.hub?.url || '');  } }, [open]);
  const act = async (fn: () => Promise<any>, success?: string) => { try { await fn(); if (success) message.success(success); refresh(); } catch (error) { message.error(String(error)); } };
  const upload = async () => {
    setBusy(true);
    try {
      const data = new FormData(); for (const file of files) { const raw = file.originFileObj || file as unknown as File; data.append('files', raw as Blob, file.name); }
      await api('/environments/import', { method: 'POST', body: data }); setFiles([]); message.success('候选环境正在本机准备，可在下表查看进度'); refresh();
    } catch (error) { message.error(String(error)); } finally { setBusy(false); }
  };
  return <Drawer title="设置" open={open} onClose={close} size={850}><Tabs items={[
    { key: 'environments', label: '运行环境', children: <><p className="muted">只接受本地 wheel 或包含依赖与 manifest.json 的 zip。安装在新建环境，不覆盖运行中的 SDK。</p><Upload.Dragger multiple accept=".whl,.zip,.json" fileList={files} beforeUpload={file => { setFiles(old => [...old, file]); return false; }} onRemove={file => setFiles(old => old.filter(x => x.uid !== file.uid))}><p>选择或拖入离线 wheel / 更新包</p><p className="muted small">pyserial、NumPy 基础依赖由应用自动安装，无需随 SDK 上传。</p></Upload.Dragger><Button type="primary" className="section" disabled={!files.length} loading={busy} onClick={upload}>准备候选环境</Button><Table className="section" rowKey="id" size="small" dataSource={environments} pagination={false} columns={[{ title: '环境', dataIndex: 'name', render: (value, row: any) => <><strong>{value}</strong><p className="small muted">{row.details.sdk_version || row.details.source}</p>{row.active && <Tag color="success">当前启用</Tag>}</> }, { title: '状态', dataIndex: 'state', render: (value, row: any) => <><Tag>{value}</Tag><p className="small">{row.details.progress}</p>{row.details.error && <p className="error">{row.details.error}</p>}</> }, { title: '操作', render: (_, row: any) => <Space wrap><Button size="small" disabled={row.state !== 'ready' || row.active} onClick={() => act(() => post('/environments/' + row.id + '/activate'), '环境已启用；后续实机任务使用此版本')}>{row.active ? '已启用' : '启用 / 回退'}</Button><JsonDetails title="安装记录" value={row.details} /></Space> }]} /><p className="small muted">准备成功表示依赖安装与 SDK 导入成功，不代表已经通过实机或所有机型兼容性验证。</p></> },
    { key: 'sharing', label: '局域网用例', children: <div className="stack"><Panel title="连接团队用例服务"><Field label="用例服务地址" note="由维护人员在局域网服务器单独启动；本机结果不上传。"><Input value={url} onChange={e => setUrl(e.target.value)} placeholder="http://服务器地址:8766" /></Field><Button onClick={() => act(() => put('/settings/hub', { url }), '地址已保存，请登录')}>保存服务地址</Button><div className="two-columns section"><Field label="账号"><Input value={username} autoComplete="username" onChange={e => setUsername(e.target.value)} /></Field><Field label="密码"><Input.Password value={password} autoComplete="current-password" onChange={e => setPassword(e.target.value)} /></Field></div><Space wrap><Button type="primary" onClick={() => act(async () => { await post('/shared/login', { username, password }); setPassword(''); await post('/shared/check'); }, '已登录并检查共享版本')}>登录</Button><Button onClick={() => act(() => post('/shared/logout'), '已退出用例服务')}>退出登录</Button></Space><p className="small muted">{settings.hub?.user?.username ? `当前：${settings.hub.user.username} / ${settings.hub.user.role}` : '登录会话仅在本机服务内存中保存；服务重启需重新登录。'}</p></Panel>{settings.hub?.user?.role === 'maintainer' && <Panel title="维护人员 · 创建团队账号"><Field label="新账号"><Input value={newUser} onChange={e => setNewUser(e.target.value)} /></Field><Field label="密码（至少 10 字符）"><Input.Password value={newPassword} onChange={e => setNewPassword(e.target.value)} /></Field><Field label="角色"><Select value={newRole} onChange={setNewRole} options={[{ value: 'tester', label: '测试人员 · 草稿与拉取' }, { value: 'maintainer', label: '维护人员 · 发布与账号管理' }]} /></Field><Button onClick={() => act(async () => { await post('/shared/users', { username: newUser, password: newPassword, role: newRole }); setNewPassword(''); }, '账号已创建')}>创建账号</Button></Panel>}</div> },
    { key: 'data', label: '本机数据', children: <><Panel title="结果与备份"><p>用例草稿、设备连接、环境记录、任务结果保存在本机 SQLite；原始日志、样本和附件独立落盘。</p><p>报告中心可导出单次任务。完整备份包含一致性数据库及运行证据，不包含 SDK 环境目录或账号密码。</p><Button onClick={() => act(async () => { const result = await post('/backups'); window.location.assign(result.download); }, '备份已生成')}>创建并下载完整备份</Button></Panel><p className="small muted section">本版本不自动删除历史数据，避免证据丢失。容量、保留期限与自动归档策略需要按实际采样量进一步配置。</p></> },
  ]} /></Drawer>;
}
