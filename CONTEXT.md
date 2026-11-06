# Multi-tenant File Storage

A proof-of-concept service where organizations store, download and manage files in isolation from one another, with files encrypted at rest and recoverable from backup.

## Language

**Tenant**:
An organization that owns files and members. All data belongs to exactly one Tenant and is invisible to every other Tenant.
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
A stored document with a name, a size, and content owned by one Tenant.
_Avoid_: Object, blob, upload, asset

**Folder**:
A named location that groups Files within a Tenant. Folders are paths, not separate stored items.
_Avoid_: Directory, bucket, prefix

**Trash**:
The state of a File that was deleted but can still be restored until it is purged.
_Avoid_: Recycle bin, archive

**Tenant key**:
The secret that protects the keys of one Tenant's Files. Destroying it makes all of that Tenant's Files unreadable.
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
