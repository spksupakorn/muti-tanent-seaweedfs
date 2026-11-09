# Production backlog

What separates this PoC from a production deployment. Nothing here is built. Status of each claim:
**[repo]** read in this repo, **[docs]** SeaweedFS documentation or source knowledge, not run on 4.52,
**[open]** unknown, needs a check. Beads is the issue tracker; turn an item into a bead when it is scheduled.

## 1. Storage topology

Today: one `weed server` (master + volume + filer + S3) in one container, filer metadata in leveldb on the
`seaweed-data` volume **[repo]**. One process is one failure domain and nothing scales separately.

| Part | PoC | Production |
|---|---|---|
| master | 1, inside `weed server` | 3 (raft, odd count) on different hosts |
| volume | 1 | 2 or more, on different hosts or racks |
| filer | 1, leveldb | 2 or more, shared metadata store |
| S3 | inside `weed server` | separate stateless `weed s3`, several behind a load balancer |
| admin | 1 | 1, operators only |

- Compose on one machine gives no HA however the containers are split. Needs several hosts (Kubernetes, Nomad, or
  similar). **[docs]**
- **Filer metadata store.** Several filers cannot share leveldb, so they need a shared store (Postgres is
  supported). Use a separate database and role from the app DB so it cannot collide with RLS. Back it up: lost
  metadata with surviving volumes leaves files unreachable. This reverses the "skip it" verdict that fit the
  single-filer PoC. **[docs]**
- **Replication.** `001` (same rack) was withdrawn because every copy sat on one disk. It means something only
  once volume servers are on different hosts or racks: `010` (other rack) or `100` (other data center). **[docs]**
- **Replication is not backup.** A delete or corruption replicates to every copy. `filer.backup` stays, and its
  sink moves from a local directory to off-machine or off-site storage. **[docs]**
- Re-run `scripts/restore-drill.sh` against the new topology. Whether `filer.backup` and `filer.copy` behave the
  same with several filers or masters is **[open]**.

## 2. PoC values to change

- `-master.volumeSizeLimitMB=256 -volume.max=100` in `compose.yaml` caps a volume server near 25 GB. Size it for the
  real data. **[repo]**
- The S3 `admin` identity is rendered in every deployment (marked as a shortcut in `compose.yaml`). Remove it from
  production. **[repo]**
- Filer and master take unauthenticated requests. Keep them on the internal `storage` network (already so **[repo]**)
  and look at `security.toml` (TLS/JWT between components). **[docs]**

## 3. Outside storage

- **TLS.** README states "No TLS; everything is on loopback". Tokens travel in clear text without it. An opt-in
  `compose.tls.yaml` (Traefik + Let's Encrypt, like `seaweedfs-stack`) is one route. Expose only the backend and
  the frontend, never `seaweedfs-admin`, the filer or S3. **[repo]**
- **Master key.** Move from a file to a KMS or HSM. ADR 0001 records that the PoC has no real KMS. **[repo]**
- **Postgres.** HA and point-in-time recovery. The hourly `pg_dump` gives neither. **[repo]**
- **Connection pooler.** The repo has no pooler, so this is a future risk, not a present one **[repo]**. The tenant
  setting is applied with `set_config(..., true)` inside the request transaction (`ATOMIC_REQUESTS`,
  `backend/accounts/rls.py`), the transaction-local form that stays safe behind a transaction-mode pooler.
  It has not been tried with a real pooler **[open]**. If one is added: keep every setting transaction-local (never
  a session-level `SET`), and disable Django server-side cursors, as its docs require for transaction pooling.
- **Backend runtime and migrations.** Confirm a production server and a safe way to run migrations with several
  instances. Not checked. **[open]**

## 4. Suggested order

1. Choose the topology: how many hosts, one data center or several.
2. Split master / volume / filer / S3.
3. Move the filer store to a separate Postgres.
4. Set replication to match the topology.
5. Move the `filer.backup` sink off the machine, then re-run the restore drill.

## 5. Comparison with `seaweedfs-stack` (2026-11-07)

Compared `/Users/colonxzerbirst/Project/seaweedfs-stack` with this repo. Reading only; that stack was not run.

**Applied here**

- `restart: unless-stopped` on `postgres`, `seaweedfs`, `seaweedfs-admin`, `backend`, `backoffice`, `frontend`
  (the `maintenance`, `filer-backup` and `db-backup` services already had it). **[repo]**
- `SEAWEEDFS_VERSION` (default `4.52`) replaces the tag written in five places in `compose.yaml`; documented as an
  optional line in `.env.example`. `docker compose config` resolves it for every profile. **[repo]**
- `scripts/test.sh` passed after the change: 156 compose tests and 15 host checks. `e2e.sh`, `restore-drill.sh` and
  `smoke.sh` were not re-run; the change is compose-only.

**Taken from it, deferred to deployment**

- Host firewall: allow only 22, 80 and 443 (`ufw`). Matters once TLS (section 3) puts a public proxy in front.
  Today every published port binds `127.0.0.1`. Not a repo change; a deployment step. **[docs]**
- TLS via Traefik with `socket-proxy` (Traefik reads `docker.sock` read-only): see section 3.

**Not taken, and why**

| Their choice | Why not here |
|---|---|
| Admin UI on a public domain behind Traefik | Ours binds `127.0.0.1:12535`; the UI knows no Tenant and bypasses Postgres and RLS |
| Admin password as `-adminPassword=...` flag | Visible in the process list. Ours uses the `WEED_ADMIN_PASSWORD` environment variable |
| Static `s3-config.json` with placeholder keys edited by hand | Ours renders `s3.json` from `.env` at start, no secrets in a tracked file |
| `SEAWEEDFS_VERSION=latest` | Ours is pinned to a tested version |
| Bind mounts `./volume1-data`, `./volume2-data` | Named volumes plus `filer.backup` cover backup and restore (drill-tested) |
| Replication `001` on two volume servers | One host and one rack: every copy on the same disk. Revisit only with a real topology (section 1) |
| Filer metadata in Postgres | Not needed with one filer. Becomes necessary with several (section 1) |
| `chmod 600 .env` | `.env` stays at mode 660, the existing setting |
