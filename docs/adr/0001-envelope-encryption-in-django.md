# Encrypt files in Django with per-tenant envelope keys

Files are encrypted in the backend (AES-256-GCM in 1 MiB segments, one data key per file, wrapped by a random per-tenant key that is itself stored wrapped by a master key) before they reach SeaweedFS, instead of using SeaweedFS encryption. This gives each tenant its own key (revoke or destroy it independently: deleting a tenant's wrapped key makes its files unreadable, except in database dumps taken earlier) and keeps backups as ciphertext, at the cost of routing every upload and download through Django and owning the key-handling code. The master key is stored outside every backup; losing it makes all files unreadable.

## Considered Options

- SeaweedFS `-encryptVolumeData`: per-chunk keys held in the filer store; no per-tenant key.
- SeaweedFS SSE-S3: one cluster-wide key.
- SeaweedFS SSE-KMS with OpenBao: per-tenant keys, but adds a service and had unresolved bugs in 4.x.
- SeaweedFS SSE-C: `filer.backup` cannot read these objects.
