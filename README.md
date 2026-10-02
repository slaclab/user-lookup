# user-lookup
simple ldap to graphql microservice

## GraphQL API

`users(filter: UserInput!)` searches the source (AD) LDAP and returns users enriched with
`uidnumber`, `gidNumber` (primary, from AD), `secondaryGidNumbers` (from SDF LDAP `posixGroup`
membership) and `preferredemail` (from urawi). Run with `uvicorn main:app`.

## LDAP → coact posix sync

The uid/gid data is also mirrored into coact's `users` collection so coact-api can serve `myGids`
from Mongo instead of hitting LDAP per request. user-lookup never talks to Mongo; it pushes a full
snapshot to coact-api's `usersPosixSync` mutation, which diffs, guards and writes.

| script | purpose |
| --- | --- |
| `ldap_posix.py` | shared LDAP reads: per-user `fetch_gidNumber` / `fetch_secondaryGidNumbers`, and paged bulk `build_snapshot()` |
| `coact_client.py` | tiny GraphQL client for coact-api (`usersPosixSync`, `posixSyncStatus`) |
| `migrate_posix.py` | one-shot backfill: snapshot → dry-run report → sanity checks → `--apply` |
| `sync_posix.py` | recurring reconcile (run every 5–10 min); exit 2 if coact-api aborted |

Typical first run against dev:

```
make secrets
make migrate-dry-run COACT_API_URL=http://coact-api-service:8000/graphql-service   # review ./migration/report-*.txt
make migrate-apply  COACT_API_URL=http://coact-api-service:8000/graphql-service
make sync-dry-run                                                                   # should report changed=0
```

### Environment

| var | default | notes |
| --- | --- | --- |
| `SOURCE_LDAP_SERVER` | `ldaps://sdfldap001.sdf.slac.stanford.edu` | AD; person entries with `uidNumber`/`gidNumber` |
| `SOURCE_LDAP_USER_BASEDN` | – | e.g. `DC=win,DC=slac,DC=Stanford,DC=edu` |
| `SOURCE_LDAP_BIND_USERNAME` / `SOURCE_LDAP_BIND_PASSWORD` | – | simple bind |
| `SDF_LDAP_SERVER` | `ldaps://sdfldap001.sdf.slac.stanford.edu` | anonymous bind |
| `SDF_LDAP_GROUP_BASEDN` | `ou=Group,dc=sdf,dc=slac,dc=stanford,dc=edu` | `posixGroup` tree |
| `LDAP_PAGE_SIZE` | `500` | bulk searches use RFC 2696 paging (AD truncates unpaged searches) |
| `COACT_API_URL` | `http://coact-api-service:8000/graphql-service` | in-cluster only; identity is a trusted header |
| `COACT_SYNC_USERNAME` | `user-lookup-bot` | must exist in coact with `isbot: true` and be listed in coact-api's `POSIX_SYNC_USERNAMES` |
| `COACT_USERNAME_HEADER` | `x-vouch-idp-claims-name` | header coact-api reads the username from |
| `URAWI_TOKEN` | – | API only; not needed by the sync scripts |

The sync only ever writes `gidnumber`, `secondarygids`, `ldapsyncedat` on users that already exist in
coact. `uidnumber` mismatches are reported, not written. LDAP is never written.
