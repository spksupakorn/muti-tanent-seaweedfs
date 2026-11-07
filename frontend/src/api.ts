const API = (import.meta.env.VITE_API_URL as string | undefined) ?? "http://127.0.0.1:8000";
export const MAX_UPLOAD_BYTES = 100 * 1024 * 1024;

export type Tenant = { id: string; name: string; role: "admin" | "member" };
export type FileItem = { id: string; name: string; folder: string; size: number; content_type: string; created_at: string };
export type Listing = { folder: string; folders: string[]; files: FileItem[] };
export type TrashedItem = FileItem & { trashed_at: string };
export type AuditEntry = {
  id: string;
  action:
    | "upload" | "download" | "rename" | "move" | "delete" | "restore"
    | "key_destroyed" | "member_added" | "member_changed" | "member_removed" | "password_reset";
  file_id: string | null;
  file_name: string;
  file_folder: string;
  user_email: string | null;
  by_operator: boolean;
  detail: string;
  created_at: string;
};

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

// shortcut: tokens live in localStorage, so a script injected into the page could read them.
// Fine for a local PoC; move the refresh token to an httpOnly cookie before real use.
const TOKENS = "filestore.tokens";
const TENANT = "filestore.tenant";
type Tokens = { access: string; refresh: string };

const readTokens = (): Tokens | null => JSON.parse(localStorage.getItem(TOKENS) ?? "null");
export const loggedIn = () => readTokens() !== null;
export const getTenantId = () => localStorage.getItem(TENANT);
export const setTenantId = (id: string) => localStorage.setItem(TENANT, id);

let onSignedOut: () => void = () => {};
export const whenSignedOut = (fn: () => void) => (onSignedOut = fn);

export function logout() {
  localStorage.removeItem(TOKENS);
  localStorage.removeItem(TENANT);
  onSignedOut();
}

async function errorOf(res: Response): Promise<ApiError> {
  let message = `Request failed (${res.status})`;
  try {
    const body = await res.json();
    if (typeof body.detail === "string") message = body.detail;
  } catch {
    /* keep the generic message */
  }
  return new ApiError(res.status, message);
}

export async function login(email: string, password: string): Promise<void> {
  const res = await fetch(`${API}/api/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) throw await errorOf(res);
  const { access, refresh } = await res.json();
  localStorage.setItem(TOKENS, JSON.stringify({ access, refresh }));
}

// One refresh at a time: parallel requests that all got a 401 share a single refresh call.
let refreshing: Promise<boolean> | null = null;
function refresh(): Promise<boolean> {
  refreshing ??= (async () => {
    try {
      const tokens = readTokens();
      if (!tokens) return false;
      const res = await fetch(`${API}/api/auth/refresh`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ refresh: tokens.refresh }),
      });
      if (!res.ok) return false;
      const next = await res.json();
      localStorage.setItem(TOKENS, JSON.stringify({ access: next.access, refresh: next.refresh }));
      return true;
    } finally {
      setTimeout(() => (refreshing = null), 0);
    }
  })();
  return refreshing;
}

function authHeaders(): Record<string, string> {
  const tokens = readTokens();
  const headers: Record<string, string> = {};
  if (tokens) headers.Authorization = `Bearer ${tokens.access}`;
  const tenant = getTenantId();
  if (tenant) headers["X-Tenant-ID"] = tenant;
  return headers;
}

/** Run a request; on a 401 refresh the session once and run it again. Still 401: the user is signed out. */
async function withSessionRetry<T extends { status: number }>(attempt: () => Promise<T>): Promise<T> {
  let result = await attempt();
  if (result.status === 401) {
    if (await refresh()) result = await attempt();
    if (result.status === 401) {
      logout();
      throw new ApiError(401, "Your session expired. Please sign in again.");
    }
  }
  return result;
}

/** A request with the bearer token and tenant. */
const authed = (path: string, init: RequestInit = {}): Promise<Response> =>
  withSessionRetry(() => fetch(`${API}${path}`, { ...init, headers: { ...init.headers, ...authHeaders() } }));

async function json<T>(path: string): Promise<T> {
  const res = await authed(path);
  if (!res.ok) throw await errorOf(res);
  return res.json();
}

export const listTenants = () => json<Tenant[]>("/api/tenants");
export const listFiles = (folder: string) => json<Listing>(`/api/files?folder=${encodeURIComponent(folder)}`);

export async function download(file: FileItem): Promise<void> {
  const res = await authed(`/api/files/${file.id}/download`);
  if (!res.ok) throw await errorOf(res);
  const url = URL.createObjectURL(await res.blob());
  const a = Object.assign(document.createElement("a"), { href: url, download: file.name });
  document.body.append(a); // some browsers ignore a click on an anchor that is not in the page
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000); // revoking at once can cancel the download in Safari
}

/** Upload with progress. fetch cannot report upload progress, so this uses XMLHttpRequest. */
export async function upload(file: File, folder: string, onProgress: (fraction: number) => void): Promise<FileItem> {
  if (file.size > MAX_UPLOAD_BYTES) throw new ApiError(413, "File is larger than the 100 MB limit");

  const attempt = () => {
    onProgress(0);
    const { promise, resolve, reject } = Promise.withResolvers<{ status: number; body: string }>();
    const form = new FormData();
    form.append("file", file);
    form.append("folder", folder);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API}/api/files`);
    for (const [k, v] of Object.entries(authHeaders())) xhr.setRequestHeader(k, v);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => resolve({ status: xhr.status, body: xhr.responseText });
    xhr.onerror = () => reject(new ApiError(0, "Could not reach the server"));
    xhr.send(form);
    return promise;
  };

  const result = await withSessionRetry(attempt);
  if (result.status !== 200) {
    let message = `Upload failed (${result.status})`;
    try {
      const detail = JSON.parse(result.body).detail;
      if (typeof detail === "string") message = detail;
    } catch {
      /* keep the generic message */
    }
    throw new ApiError(result.status, message);
  }
  onProgress(1);
  return JSON.parse(result.body);
}

/** Rename and/or move. A taken name is a 409 with a readable message; nothing changes then. */
export async function updateFile(id: string, change: { name?: string; folder?: string }): Promise<FileItem> {
  const res = await authed(`/api/files/${id}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(change),
  });
  if (!res.ok) throw await errorOf(res);
  return res.json();
}

export async function trashFile(id: string): Promise<void> {
  const res = await authed(`/api/files/${id}`, { method: "DELETE" });
  if (!res.ok) throw await errorOf(res);
}

export const listTrash = () => json<TrashedItem[]>("/api/trash");

/** Back to the original folder under the old name; a 409 if that name was reused meanwhile. */
export async function restoreFile(id: string): Promise<FileItem> {
  const res = await authed(`/api/trash/${id}/restore`, { method: "POST" });
  if (!res.ok) throw await errorOf(res);
  return res.json();
}

/** The Tenant's audit log, newest first. Only a Tenant admin may read it: anyone else gets a 403. */
export const listAudit = (limit: number, offset: number) =>
  json<AuditEntry[]>(`/api/audit?limit=${limit}&offset=${offset}`);
