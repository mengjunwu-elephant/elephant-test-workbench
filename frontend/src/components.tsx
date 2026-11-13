import { useEffect, useRef, useState } from 'react';
import { Alert, Input, Select, Space, Tag } from 'antd';
import type { EChartsOption } from 'echarts';
import { format, states } from './types';

export function Panel({ title, extra, children, className = '' }: { title: string; extra?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return <section className={'panel ' + className}><div className="panel-title"><span>{title}</span>{extra}</div><div className="panel-body">{children}</div></section>;
}
export function Field({ label, children, note }: { label: string; children: React.ReactNode; note?: string }) {
  return <label className="field"><span>{label}</span>{children}{note && <small>{note}</small>}</label>;
}
export function ValueInput({ value, onChange, multiline = false }: { value: any; onChange: (value: any) => void; multiline?: boolean }) {
  const text = format(value);
  const [draft, setDraft] = useState(text);
  const [error, setError] = useState('');
  useEffect(() => { setDraft(text); setError(''); }, [text]);
  const commit = () => {
    try { const parsed = JSON.parse(draft); onChange(parsed); setError(''); }
    catch { setError('请输入 JSON 值：数字、true、null、数组、对象或带双引号的字符串'); }
  };
  return <div><Input.TextArea aria-label="参数 JSON 值" autoSize={{ minRows: multiline ? 3 : 1, maxRows: 12 }} value={draft} onChange={e => setDraft(e.target.value)} onBlur={commit} status={error ? 'error' : undefined} /><small className="error">{error}</small></div>;
}
export function Argument({ value, onChange }: { value: any; onChange: (value: any) => void }) {
  const ref = value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).length === 1 && '$ref' in value;
  return <div className="argument"><Select aria-label="参数来源" value={ref ? 'ref' : 'literal'} style={{ width: 85 }} options={[{ value: 'literal', label: '值' }, { value: 'ref', label: '引用' }]} onChange={kind => onChange(kind === 'ref' ? { $ref: 'params.angle' } : null)} />{ref ? <Input aria-label="变量引用路径" value={value.$ref} placeholder="params.angle / result.0" onChange={e => onChange({ $ref: e.target.value })} /> : <ValueInput value={value} onChange={onChange} />}</div>;
}
export function StateTag({ state }: { state: string }) {
  return <Tag color={state === 'blocked' || state === 'failed' ? 'error' : state === 'completed' ? 'success' : undefined}>{states[state] || state}</Tag>;
}
export function ModeTag() {
  return <Tag color="warning">实机任务</Tag>;
}
export function ErrorBox({ text }: { text?: string }) { return text ? <Alert title={text} type="error" showIcon /> : null; }

export function Chart({ option }: { option: EChartsOption }) {
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let disposed = false;
    let chart: any;
    let observer: ResizeObserver;
    import('echarts').then(echarts => {
      if (disposed || !root.current) return;
      chart = echarts.init(root.current);
      chart.setOption(option);
      observer = new ResizeObserver(() => chart.resize()); observer.observe(root.current);
    });
    return () => { disposed = true; observer?.disconnect(); chart?.dispose(); };
  }, [option]);
  return <div ref={root} style={{ height: 250, width: '100%' }} />;
}

export function JsonDetails({ title, value }: { title: string; value: any }) {
  return <details><summary>{title}</summary><pre>{JSON.stringify(value, null, 2)}</pre></details>;
}

