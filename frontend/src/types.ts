export type Model = { id: string; name: string; connection: string; class: string };
export type Connection = { mode: 'hardware'; model: string; address: string; second_address?: string; baudrate: number; netport?: number; connect_timeout?: number; environment_id?: string; arm?: 'left' | 'right' };
export type Step = { id: string; name: string; phase: string; kind: string; api?: string; args?: any[]; kwargs?: Record<string, any>; save_as?: string; selector?: (string | number)[]; expected_exception?: string; assertion?: string; actual?: any; expected?: any; tolerance?: number; timeout?: number; method?: string; parameters?: Record<string, any>; message?: string };
export type CaseDoc = { id: string; name: string; model: string; description: string; category: string; sdk_requirement: string; method_versions: Record<string, string>; parameters: Record<string, any>; datasets: Record<string, any>[]; steps: Step[]; revision: number; status: string; shared_id: string; base_shared_revision: number; enabled?: boolean; migration?: Record<string, any> };
export type Task = { id: string; state: string; kind: string; request: any; snapshot: any; summary: any; telemetry: any; created_at: string; ended_at: string | null; resource: string };
export type WorkEvent = { seq: number; time: string; type: string; level?: string; message?: string; [key: string]: any };
export const phases = { setup: '前置', execute: '执行', verify: '验证', restore: '恢复' };
export const kinds = { call: '调用接口', read: '读取接口', wait: '等待条件', assert: '断言', confirm: '人工确认', method: '公共方法', review: '迁移待完善' };
export const terminal = new Set(['completed', 'failed', 'blocked', 'stopped', 'interrupted']);
export const states: Record<string, string> = { queued: '准备中', connecting: '正在连接 / 校验', degraded: '读回无效 · 重试中', connected: '已连接', disconnecting: '正在断开', running: '运行中', restoring: '恢复中', waiting_confirmation: '等待人工确认', paused: '已暂停', stopping: '停止请求处理中', completed: '已完成', failed: '测试失败', blocked: '连接失效 · 可重新连接', stopped: '已停止任务', interrupted: '任务中断' };
export const categoryNames: Record<string, string> = { normal: '正常参数', boundary: '边界参数', exception: '预期异常', manual: '人工判断' };
export const format = (value: any) => value === undefined ? '' : JSON.stringify(value);
export const newStep = (kind: string): Step => ({ id: crypto.randomUUID(), name: kinds[kind as keyof typeof kinds], kind, phase: kind === 'assert' ? 'verify' : 'execute', api: 'get_angles_info', args: [], kwargs: {}, timeout: 15, actual: { $ref: 'result' }, expected: null, assertion: 'eq', tolerance: 0.1, method: 'wait_motion', parameters: {}, save_as: '', expected_exception: '', selector: [], message: '请确认现场条件满足' });
export function newCase(model: string): CaseDoc {
  return { id: crypto.randomUUID(), name: '新的测试用例', model, description: '', category: 'normal', sdk_requirement: '', method_versions: {}, parameters: {}, datasets: [{}], steps: [{ ...newStep('read'), name: '读取状态', save_as: 'result' }], revision: 1, status: 'draft', shared_id: '', base_shared_revision: 0 };
}


export type PlanItem = { item_id:string; case_id:string; content_hash:string; dataset_indices:number[]; overrides:Record<string,any>; repeat:number };
export type TestPlan = { id:string; name:string; model:string; revision:number; description:string; archived:boolean; failure_policy:'continue'|'stop'; items:PlanItem[]; definitions:Record<string,CaseDoc> };
export const newPlan = (model:string):TestPlan => ({id:crypto.randomUUID(),name:'新的测试计划',model,revision:1,description:'',archived:false,failure_policy:'continue',items:[],definitions:{}});
