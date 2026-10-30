export const base = import.meta.env.VITE_API_BASE_URL ?? "";

export async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${base}/api/v1/${path}`, body === undefined ? undefined : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const value = await response.json().catch(() => null);
  if (!response.ok) throw new Error(typeof value?.detail === "string" ? value.detail : `API returned ${response.status}`);
  return value as T;
}
