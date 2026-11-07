# Multi-tenant File Storage

A proof-of-concept service where organizations store, download and manage files in isolation from one another, with files encrypted at rest and recoverable from backup.

## Language

**Tenant**:
An organization that owns files and members; people see it as an "organization" in the app. All data belongs to exactly one Tenant and is invisible to every other Tenant.
_Avoid_: Workspace, account, customer, org

**User**:
A person who signs in. A User exists independently of any Tenant and may belong to several.
_Avoid_: Member (a Member is a User seen through a Membership), account

**Membership**:
The link between a User and a Tenant, carrying the User's Role in that Tenant.

**Role**:
What a User may do inside a Tenant: `admin` or `member`.
_Avoid_: Permission, group

**File**:
A stored document with a name, a size, and content owned by one Tenant. Within a Folder a Tenant has at most one File of a given name; Files in Trash do not hold their name.
_Avoid_: Object, blob, upload, asset

**Folder**:
A named location that groups Files within a Tenant. Folders are paths, not separate stored items.
_Avoid_: Directory, bucket, prefix

**Half-finished upload**:
A File whose record exists but whose content is not fully stored yet. It is invisible to everyone and removed if it is still unfinished after an hour.
_Avoid_: Pending file, partial upload

**Trash**:
The state of a File that was deleted but can still be restored until it is purged.
_Avoid_: Recycle bin, archive

**Purge**:
The permanent removal of a File's record and stored content: from Trash after seven days, and only once the Backup holds a complete copy of the content.
_Avoid_: Hard delete, cleanup (cleanup is the removal of Half-finished uploads)

**Audit log**:
A Tenant's record of who did what to which File and when. Only the Tenant's admins can read it; actions taken by a Platform operator are shown as the platform's, without naming the operator.
_Avoid_: Activity feed, history

**Tenant key**:
The secret that protects the keys of one Tenant's Files. Destroying it makes all of that Tenant's Files unreadable, except through Backups taken before it was destroyed.
_Avoid_: Master key (that is the secret above all Tenant keys), encryption key

**Backup**:
A recoverable copy of Files and their records, taken separately from the live data.

**Restore drill**:
A rehearsal that rebuilds the system from a Backup and proves Files can be listed, downloaded and decrypted.

**Master key**:
The secret above all Tenant keys. It is kept apart from every Backup, because a Backup without it cannot be decrypted.
_Avoid_: Root key, system key

**Platform operator**:
A person who runs the whole service across Tenants. An operator sees File names and records but never File contents.
_Avoid_: Superuser, super admin (an `admin` is a Role inside one Tenant)

**Back office**:
The separate service where Platform operators work: it lists every Tenant's Files, moves them to Trash and back, and manages Tenants, Users and Memberships. It holds no Master key and no access to stored content.
_Avoid_: Admin panel (the SeaweedFS admin UI is a different thing: it shows storage health)
