/** Send a JSON request to the local Quant Studio API and expose its detail. */
export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`http://127.0.0.1:8765${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options?.headers },
  });
  const payload = await response.json();
  if (!response.ok) {
    const detail = Array.isArray(payload.detail)
      ? payload.detail.map((issue: { msg: string }) => issue.msg).join("；")
      : payload.detail;
    throw new Error(detail ?? `请求失败 (${response.status})`);
  }
  return payload as T;
}
