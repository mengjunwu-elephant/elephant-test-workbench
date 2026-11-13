export async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch('/api' + path, {
    ...options,
    headers: options.body instanceof FormData ? options.headers : { 'Content-Type': 'application/json', ...options.headers },
  });
  if (!response.ok) {
    let detail: any;
    try { detail = (await response.json()).detail; } catch { detail = response.statusText; }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return response.json();
}
export const post = <T = any>(path: string, value?: any) => api<T>(path, { method: 'POST', body: value === undefined ? undefined : JSON.stringify(value) });
export const put = <T = any>(path: string, value: any) => api<T>(path, { method: 'PUT', body: JSON.stringify(value) });
export function downloadJson(name: string, value: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }));
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = name; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

