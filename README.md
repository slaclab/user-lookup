# user-lookup
simple ldap to graphql microservice

## GraphQL API

`users(filter: UserInput!)` searches the source (AD) LDAP and returns users enriched with
`uidnumber`, `gidNumber` (primary, from AD), `secondaryGidNumbers` (from SDF LDAP `posixGroup`
membership) and `preferredemail` (from urawi). Run with `uvicorn main:app`.

## LDAP → coact posix sync

The uid/gid data is also mirrored into coact's `users` collection so coact-api can serve `myGids`
from Mongo instead of hitting LDAP per request. user-lookup never talks to Mongo; it pushes snapshots
to coact-api, which diffs, guards and writes.

Ownership after the one-time migration:

| field | written by |
| --- | --- |
| `uidnumber` | coactd at registration (`userUpsert`) |
| `gidnumber` (primary) | the migration once; coactd at registration for new users |
| `secondarygids` | coactd on membership changes; `sync_posix.py` corrects anything missed |

| script | purpose |
| --- | --- |
| `ldap_posix.py` | shared LDAP reads: per-user `fetch_gidNumber` / `fetch_secondaryGidNumbers`; `build_snapshot()` (migration: one paged AD read + one posixGroup read); `build_secondary_snapshot()` (sync: posixGroup read only) |
| `coact_client.py` | tiny GraphQL client for coact-api (`posixSyncUsernames`, `usersPosixSync`, `usersSecondaryGidsSync`, `posixSyncStatus`) |
| `migrate_posix.py` | one-shot backfill via `usersPosixSync`: snapshot → dry-run report → sanity checks → `--apply`. Needs AD credentials |
| `sync_posix.py` | periodic fallback via `usersSecondaryGidsSync`: secondary gids of initialised users only, SDF LDAP only (no AD); exit 2 if coact-api aborted |

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
| `SOURCE_LDAP_SERVER` | `ldaps://sdfldap001.sdf.slac.stanford.edu` | AD; person entries with `uidNumber`/`gidNumber` (API and migration only) |
| `SOURCE_LDAP_USER_BASEDN` | – | e.g. `DC=win,DC=slac,DC=Stanford,DC=edu` |
| `SOURCE_LDAP_BIND_USERNAME` / `SOURCE_LDAP_BIND_PASSWORD` | – | simple bind |
| `SDF_LDAP_SERVER` | `ldaps://sdfldap001.sdf.slac.stanford.edu` | anonymous bind |
| `SDF_LDAP_GROUP_BASEDN` | `ou=Group,dc=sdf,dc=slac,dc=stanford,dc=edu` | `posixGroup` tree |
| `LDAP_PAGE_SIZE` | `500` | the posixGroup read uses RFC 2696 paging |
| `COACT_API_URL` | `http://coact-api-service:8000/graphql-service` | in-cluster only; identity is a trusted header |
| `COACT_SYNC_USERNAME` | `user-lookup-bot` | a plain coact user (not `isbot`: bots are implicitly admins) listed in coact-api's `POSIX_SYNC_USERNAMES` |
| `COACT_USERNAME_HEADER` | `x-vouch-idp-claims-name` | header coact-api reads the username from |
| `URAWI_TOKEN` | – | API only; not needed by the sync scripts |

The migration writes `gidnumber`, `secondarygids`, `ldapsyncedat`; the periodic sync writes only
`secondarygids` (+ `ldapsyncedat`) and only for users that already have `ldapsyncedat`. Neither creates
users or writes `uidnumber` (mismatches are reported by the migration). LDAP is never written.
