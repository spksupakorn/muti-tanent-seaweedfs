import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  AuditEntry,
  FileItem,
  Listing,
  Tenant,
  TrashedItem,
  download,
  getTenantId,
  listAudit,
  listFiles,
  listTenants,
  listTrash,
  loggedIn,
  login,
  logout,
  restoreFile,
  setTenantId,
  trashFile,
  updateFile,
  upload,
  whenSignedOut,
} from "./api";

const errorText = (e: unknown) => (e instanceof Error ? e.message : "Something went wrong");

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let n = bytes / 1024;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) (n /= 1024), i++;
  return `${n.toFixed(n < 10 ? 1 : 0)} ${units[i]}`;
}

export default function App() {
  const [signedIn, setSignedIn] = useState(loggedIn());
  useEffect(() => whenSignedOut(() => setSignedIn(false)), []);
  return signedIn ? <Files /> : <Login onDone={() => setSignedIn(true)} />;
}

function Login({ onDone }: { onDone: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(email, password);
      onDone();
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? "Wrong email or password" : errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="card narrow">
      <h1>File storage</h1>
      <form onSubmit={submit}>
        <label>
          Email
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus required />
        </label>
        <label>
          Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {error && <p role="alert" className="error">{error}</p>}
        <button disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
      </form>
    </main>
  );
}

type View = "files" | "trash" | "audit";
const VIEWS: View[] = ["files", "trash", "audit"];

// The address bar holds the view (#/files, #/trash, #/audit), so a link or a typed URL opens it directly.
function viewFromHash(): View {
  const name = window.location.hash.replace(/^#\//, "");
  return VIEWS.find((v) => v === name) ?? "files";
}

function useView(): [View, (view: View) => void] {
  const [view, setView] = useState<View>(viewFromHash());
  useEffect(() => {
    const onChange = () => setView(viewFromHash());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return [view, (next) => (window.location.hash = `#/${next}`)];
}

function Files() {
  const [tenants, setTenants] = useState<Tenant[] | null>(null);
  const [tenantId, setTenant] = useState<string | null>(getTenantId());
  const [view, setView] = useView();
  const [error, setError] = useState("");

  useEffect(() => {
    listTenants()
      .then((list) => {
        setTenants(list);
        // Keep the remembered Tenant if the user still belongs to it, otherwise start with the first.
        const current = list.find((t) => t.id === tenantId) ?? list[0];
        if (current) {
          setTenantId(current.id);
          setTenant(current.id);
        }
      })
      .catch((e) => setError(errorText(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function switchTenant(id: string) {
    setTenantId(id);
    setTenant(id);
  }

  const role = tenants?.find((t) => t.id === tenantId)?.role;

  return (
    <main className="card">
      <header>
        <h1>File storage</h1>
        <div className="bar">
          {tenants && tenants.length > 0 && (
            <label>
              Organization{" "}
              <select value={tenantId ?? ""} onChange={(e) => switchTenant(e.target.value)} aria-label="Organization">
                {tenants.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name} ({t.role})
                  </option>
                ))}
              </select>
            </label>
          )}
          <button className="link" onClick={logout}>Sign out</button>
        </div>
      </header>
      {error && <p role="alert" className="error">{error}</p>}
      {tenants && tenants.length === 0 && <p>You do not belong to any organization yet.</p>}
      {tenantId && (
        <>
          <nav aria-label="View" className="tabs">
            <button className={view === "files" ? "tab on" : "tab"} aria-current={view === "files"} onClick={() => setView("files")}>
              Files
            </button>
            <button className={view === "trash" ? "tab on" : "tab"} aria-current={view === "trash"} onClick={() => setView("trash")}>
              Trash
            </button>
            {role === "admin" && (
              <button className={view === "audit" ? "tab on" : "tab"} aria-current={view === "audit"} onClick={() => setView("audit")}>
                Audit log
              </button>
            )}
          </nav>
          {view === "files" && <Browser key={tenantId} />}
          {view === "trash" && <Trash key={tenantId} />}
          {view === "audit" && role === "admin" && <Audit key={tenantId} />}
          {view === "audit" && role === "member" && (
            <p role="alert" className="error">Only organization admins can read the audit log.</p>
          )}
        </>
      )}
    </main>
  );
}

type Editing = { id: string; mode: "rename" | "move"; value: string };

function Browser() {
  const [folder, setFolder] = useState("/");
  const [listing, setListing] = useState<Listing | null>(null);
  const [error, setError] = useState("");
  const [progress, setProgress] = useState<number | null>(null);
  const [notice, setNotice] = useState("");
  const [editing, setEditing] = useState<Editing | null>(null);
  const [editError, setEditError] = useState("");

  const load = useCallback(async (path: string) => {
    try {
      setListing(await listFiles(path));
      setError("");
    } catch (e) {
      setError(errorText(e));
    }
  }, []);

  useEffect(() => {
    void load(folder);
  }, [folder, load]);

  async function onPick(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setNotice("");
    setError("");
    setProgress(0);
    try {
      await upload(file, folder, setProgress);
      setNotice(`Uploaded ${file.name}`);
      await load(folder);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setProgress(null);
    }
  }

  async function onDownload(file: FileItem) {
    try {
      await download(file);
    } catch (e) {
      setError(errorText(e));
    }
  }

  function startEdit(file: FileItem, mode: Editing["mode"]) {
    setEditing({ id: file.id, mode, value: mode === "rename" ? file.name : file.folder });
    setEditError("");
    setNotice("");
  }

  async function saveEdit(file: FileItem, e: React.FormEvent) {
    e.preventDefault();
    if (!editing) return;
    const change = editing.mode === "rename" ? { name: editing.value } : { folder: editing.value };
    try {
      const updated = await updateFile(file.id, change);
      setEditing(null);
      setNotice(
        editing.mode === "rename" ? `Renamed ${file.name} to ${updated.name}` : `Moved ${updated.name} to ${updated.folder}`,
      );
      await load(folder);
    } catch (err) {
      // A taken name lands here: say so next to the file, and leave everything as it was.
      setEditError(errorText(err));
    }
  }

  async function onDelete(file: FileItem) {
    setNotice("");
    try {
      await trashFile(file.id);
      setNotice(`Moved ${file.name} to Trash`);
      await load(folder);
    } catch (e) {
      setError(errorText(e));
    }
  }

  const parts = folder.split("/").filter(Boolean);
  const goTo = (n: number) => setFolder("/" + parts.slice(0, n).join("/"));

  return (
    <section>
      <nav aria-label="Folder path" className="crumbs">
        <button className="link" onClick={() => goTo(0)}>All files</button>
        {parts.map((p, i) => (
          <span key={i}>
            {" / "}
            <button className="link" onClick={() => goTo(i + 1)}>{p}</button>
          </span>
        ))}
        {parts.length > 0 && (
          <button className="link up" onClick={() => goTo(parts.length - 1)}>↑ Up</button>
        )}
      </nav>

      <div className="upload">
        <input type="file" onChange={onPick} disabled={progress !== null} aria-label="Choose a file to upload" />
        {progress !== null && (
          <div className="progress" role="progressbar" aria-valuenow={Math.round(progress * 100)} aria-valuemin={0} aria-valuemax={100}>
            <div style={{ width: `${progress * 100}%` }} />
            <span>{Math.round(progress * 100)}%</span>
          </div>
        )}
        <small>Uploads go to {folder}. Maximum 100 MB per file.</small>
      </div>

      {notice && <p role="status" className="ok">{notice}</p>}
      {error && <p role="alert" className="error">{error}</p>}

      {listing && (
        <table>
          <thead>
            <tr><th>Name</th><th>Size</th><th>Uploaded</th><th /></tr>
          </thead>
          <tbody>
            {listing.folders.map((name) => (
              <tr key={`d:${name}`}>
                <td colSpan={3}>
                  <button className="link folder" onClick={() => setFolder(folder === "/" ? `/${name}` : `${folder}/${name}`)}>
                    📁 {name}
                  </button>
                </td>
                <td />
              </tr>
            ))}
            {listing.files.map((f) => (
              <tr key={f.id}>
                {editing?.id === f.id ? (
                  <td colSpan={4}>
                    <form className="inline" onSubmit={(e) => saveEdit(f, e)}>
                      <input
                        aria-label={editing.mode === "rename" ? "New name" : "New folder"}
                        value={editing.value}
                        onChange={(e) => setEditing({ ...editing, value: e.target.value })}
                        autoFocus
                        required
                      />
                      <button>Save</button>
                      <button type="button" className="link" onClick={() => setEditing(null)}>Cancel</button>
                      {editError && <span role="alert" className="error">{editError}</span>}
                    </form>
                  </td>
                ) : (
                  <>
                    <td>{f.name}</td>
                    <td>{formatSize(f.size)}</td>
                    <td>{new Date(f.created_at).toLocaleString()}</td>
                    <td className="actions">
                      <button onClick={() => onDownload(f)}>Download</button>
                      <button className="link" onClick={() => startEdit(f, "rename")}>Rename</button>
                      <button className="link" onClick={() => startEdit(f, "move")}>Move</button>
                      <button className="link danger" onClick={() => onDelete(f)}>Delete</button>
                    </td>
                  </>
                )}
              </tr>
            ))}
            {listing.folders.length === 0 && listing.files.length === 0 && (
              <tr><td colSpan={4} className="empty">This folder is empty.</td></tr>
            )}
          </tbody>
        </table>
      )}
    </section>
  );
}

function Trash() {
  const [items, setItems] = useState<TrashedItem[] | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    try {
      setItems(await listTrash());
    } catch (e) {
      setError(errorText(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function onRestore(item: TrashedItem) {
    setError("");
    setNotice("");
    try {
      const back = await restoreFile(item.id);
      setNotice(`Restored ${back.name} to ${back.folder}`);
      await load();
    } catch (e) {
      // A reused name is a 409: the file stays in Trash and can be restored once the name is free.
      setError(`${item.name} was not restored: ${errorText(e)}`);
    }
  }

  return (
    <section>
      <p>
        <small>Deleted files stay here until they are purged. Restoring puts a file back in its original folder.</small>
      </p>
      {notice && <p role="status" className="ok">{notice}</p>}
      {error && <p role="alert" className="error">{error}</p>}
      {items && (
        <table>
          <thead>
            <tr><th>Name</th><th>Folder</th><th>Size</th><th>Deleted</th><th /></tr>
          </thead>
          <tbody>
            {items.map((t) => (
              <tr key={t.id}>
                <td>{t.name}</td>
                <td>{t.folder}</td>
                <td>{formatSize(t.size)}</td>
                <td>{new Date(t.trashed_at).toLocaleString()}</td>
                <td className="actions"><button onClick={() => onRestore(t)}>Restore</button></td>
              </tr>
            ))}
            {items.length === 0 && <tr><td colSpan={5} className="empty">Trash is empty.</td></tr>}
          </tbody>
        </table>
      )}
    </section>
  );
}

const PAGE = 50;
const ACTION_TEXT: Record<AuditEntry["action"], string> = {
  upload: "Uploaded",
  download: "Downloaded",
  rename: "Renamed",
  move: "Moved",
  delete: "Moved to Trash",
  restore: "Restored",
  key_destroyed: "Tenant key destroyed: all files are now unreadable",
  member_added: "Member added",
  member_changed: "Member changed",
  member_removed: "Member removed",
  password_reset: "Password reset",
};

function Audit() {
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);
  const [more, setMore] = useState(false);
  const [error, setError] = useState("");

  const loadPage = useCallback(async (offset: number) => {
    try {
      const page = await listAudit(PAGE, offset);
      setEntries((current) => (offset === 0 ? page : [...(current ?? []), ...page]));
      setMore(page.length === PAGE);
      setError("");
    } catch (e) {
      setError(errorText(e));
    }
  }, []);

  useEffect(() => {
    void loadPage(0);
  }, [loadPage]);

  return (
    <section>
      <p>
        <small>Who did what to which file in this organization, newest first.</small>
      </p>
      {error && <p role="alert" className="error">{error}</p>}
      {entries && (
        <table>
          <thead>
            <tr><th>When</th><th>Who</th><th>Action</th><th>File</th></tr>
          </thead>
          <tbody>
            {entries.map((e) => (
              <tr key={e.id}>
                <td>{new Date(e.created_at).toLocaleString()}</td>
                <td>{e.by_operator ? "Platform operator" : (e.user_email ?? "Deleted user")}</td>
                <td>{ACTION_TEXT[e.action]}</td>
                <td>
                  {e.file_name ? <>{e.file_name} <small>{e.file_folder}</small></> : (e.detail || "All files in this organization")}
                </td>
              </tr>
            ))}
            {entries.length === 0 && <tr><td colSpan={4} className="empty">No activity yet.</td></tr>}
          </tbody>
        </table>
      )}
      {more && entries && <button onClick={() => void loadPage(entries.length)}>Load more</button>}
    </section>
  );
}
