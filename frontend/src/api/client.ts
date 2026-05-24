const base = "";

export async function apiGet<T>(path: string): Promise<T> {
  const r = await fetch(`${base}${path}`);
  if (!r.ok) throw new Error(await r.text());
  return r.json() as Promise<T>;
}

export async function apiPatch<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${base}${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json() as Promise<T>;
}

export async function apiDelete<T = { ok: boolean }>(path: string): Promise<T> {
  const r = await fetch(`${base}${path}`, { method: "DELETE" });
  if (!r.ok) throw new Error(await r.text());
  if (r.status === 204 || r.headers.get("content-length") === "0") return {} as T;
  const text = await r.text();
  return text ? (JSON.parse(text) as T) : ({} as T);
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method: "POST" };
  if (body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  const r = await fetch(`${base}${path}`, init);
  if (!r.ok) throw new Error(await r.text());
  if (r.status === 204 || r.headers.get("content-length") === "0") return {} as T;
  const text = await r.text();
  return text ? (JSON.parse(text) as T) : ({} as T);
}

export async function apiDownloadCsv(path: string, fallbackFilename: string): Promise<void> {
  const r = await fetch(`${base}${path}`);
  if (!r.ok) throw new Error(await r.text());
  const blob = await r.blob();
  const cd = r.headers.get("Content-Disposition") || "";
  const match = /filename="([^"]+)"/.exec(cd);
  const filename = match?.[1] || fallbackFilename;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
