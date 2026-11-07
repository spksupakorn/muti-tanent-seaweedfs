import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  FileItem,
  Listing,
  Tenant,
  download,
  getTenantId,
  listFiles,
  listTenants,
  loggedIn,
  login,
  logout,
  setTenantId,
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

function Files() {
  const [tenants, setTenants] = useState<Tenant[] | null>(null);
  const [tenantId, setTenant] = useState<string | null>(getTenantId());
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
      {tenantId && <Browser key={tenantId} />}
    </main>
  );
}

function Browser() {
  const [folder, setFolder] = useState("/");
  const [listing, setListing] = useState<Listing | null>(null);
  const [error, setError] = useState("");
  const [progress, setProgress] = useState<number | null>(null);
  const [notice, setNotice] = useState("");
  const input = useRef<HTMLInputElement>(null);

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

  const parts = folder.split("/").filter(Boolean);
  const goTo = (n: number) => setFolder("/" + parts.slice(0, n).join("/"));
  const into = (name: string) => setFolder(folder === "/" ? `/${name}` : `${folder}/${name}`);

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
        <input ref={input} type="file" onChange={onPick} disabled={progress !== null} aria-label="Choose a file to upload" />
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
                  <button className="link folder" onClick={() => into(name)}>📁 {name}</button>
                </td>
                <td />
              </tr>
            ))}
            {listing.files.map((f) => (
              <tr key={f.id}>
                <td>{f.name}</td>
                <td>{formatSize(f.size)}</td>
                <td>{new Date(f.created_at).toLocaleString()}</td>
                <td><button onClick={() => onDownload(f)}>Download</button></td>
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
