"""
LDAP POSIX identity reads (uidNumber, primary gidNumber, secondary gidNumbers).

Single home for both the per-user lookups used by the GraphQL API and the snapshot of coact users
used by migrate_posix.py / sync_posix.py, so that all paths derive identity the same way (keyed on AD
`uid`) and share the same timeout / retry behaviour.

Two directories are involved:
  SOURCE_LDAP (AD)  - person entries carrying uidNumber / gidNumber
  SDF_LDAP          - posixGroup entries carrying gidNumber / memberUid
"""

import logging
import time
from os import environ
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple, TypeVar

import bonsai
from bonsai import LDAPClient

LOG = logging.getLogger(__name__)

# SOURCE_LDAP: maps to the Windows ldap instance
SOURCE_LDAP_SERVER = environ.get('SOURCE_LDAP_SERVER', 'ldaps://sdfldap001.sdf.slac.stanford.edu')
SOURCE_LDAP_USER_BASEDN = environ.get('SOURCE_LDAP_USER_BASEDN', None)
SOURCE_LDAP_BIND_USERNAME = environ.get('SOURCE_LDAP_BIND_USERNAME', None)
SOURCE_LDAP_BIND_PASSWORD = environ.get('SOURCE_LDAP_BIND_PASSWORD', None)

# SDF LDAP: anonymous bind, posixGroup tree
SDF_LDAP_SERVER = environ.get('SDF_LDAP_SERVER', 'ldaps://sdfldap001.sdf.slac.stanford.edu')
SDF_LDAP_USER_BASEDN = environ.get('SDF_LDAP_USER_BASEDN')
SDF_LDAP_GROUP_BASEDN = environ.get('SDF_LDAP_GROUP_BASEDN', 'ou=Group,dc=sdf,dc=slac,dc=stanford,dc=edu')

LDAP_TIMEOUT = float(environ.get('LDAP_TIMEOUT', '30.0'))
LDAP_RETRIES = int(environ.get('LDAP_RETRIES', '3'))
LDAP_RETRY_BACKOFF = float(environ.get('LDAP_RETRY_BACKOFF', '0.5'))
# AD caps result sets (MaxPageSize, typically 1000); bulk searches must use paged_search.
LDAP_PAGE_SIZE = int(environ.get('LDAP_PAGE_SIZE', 500))

# errors that will never succeed on a retry, so fail fast on them
LDAP_FATAL_ERRORS = (
    bonsai.AuthenticationError,
    bonsai.InvalidDN,
    bonsai.NoSuchObjectError,
)

SOURCE_LDAP_CLIENT = LDAPClient(SOURCE_LDAP_SERVER)
if SOURCE_LDAP_BIND_USERNAME and SOURCE_LDAP_BIND_PASSWORD:
    SOURCE_LDAP_CLIENT.set_credentials("SIMPLE", user=SOURCE_LDAP_BIND_USERNAME, password=SOURCE_LDAP_BIND_PASSWORD)
LOG.info(f"connecting to {SOURCE_LDAP_SERVER} with {SOURCE_LDAP_BIND_USERNAME}, using basedn {SOURCE_LDAP_USER_BASEDN}")

SDF_LDAP_CLIENT = LDAPClient(SDF_LDAP_SERVER)
LOG.info(f"connecting to {SDF_LDAP_SERVER} with anonymous bind, using basedn {SDF_LDAP_USER_BASEDN}")

T = TypeVar('T')


def _with_retry(op: Callable[[bonsai.LDAPConnection], T], client: LDAPClient, description: str) -> T:
    """Run op(conn) on a fresh connection, bounding connect with LDAP_TIMEOUT and retrying transient
    failures up to LDAP_RETRIES times. Raises the last error if every attempt fails."""
    attempts = LDAP_RETRIES + 1
    for attempt in range(1, attempts + 1):
        try:
            with client.connect(timeout=LDAP_TIMEOUT) as conn:
                return op(conn)
        except LDAP_FATAL_ERRORS as e:
            LOG.warning(f"{description} failed unrecoverably: {e}")
            raise
        except (bonsai.LDAPError, OSError) as e:
            if attempt == attempts:
                LOG.warning(f"{description} failed after {attempt} attempt(s): {e}")
                raise
            delay = LDAP_RETRY_BACKOFF * (2 ** (attempt - 1))
            LOG.warning(f"{description} failed (attempt {attempt}/{attempts}), retrying in {delay}s: {e}")
            time.sleep(delay)


def ldap_search(client: LDAPClient, base: str, filter_exp: str, attrlist: Optional[List[str]] = None,
                scope=bonsai.LDAPSearchScope.SUB, description: str = 'ldap search') -> List[dict]:
    """Single-shot search with timeout and retry."""
    return _with_retry(
        lambda conn: conn.search(base, scope, filter_exp, attrlist=attrlist, timeout=LDAP_TIMEOUT),
        client, description)


def ldap_paged_search(client: LDAPClient, base: str, filter_exp: str, attrlist: Optional[List[str]] = None,
                      scope=bonsai.LDAPSearchScope.SUB, description: str = 'ldap paged search') -> List[dict]:
    """Full result set via RFC 2696 paging (plain search() silently truncates at AD's MaxPageSize).
    Pages are consumed inside the retry so a mid-stream failure restarts the whole search."""
    def _op(conn):
        it = conn.paged_search(base, scope, filter_exp, attrlist=attrlist, timeout=LDAP_TIMEOUT, page_size=LDAP_PAGE_SIZE)
        return list(it)  # bonsai auto-acquires subsequent pages while iterating
    return _with_retry(_op, client, description)


def _first_int(entry, attr) -> Optional[int]:
    if attr in entry and entry[attr]:
        try:
            return int(entry[attr][0])
        except (TypeError, ValueError) as e:
            LOG.warning(f"invalid {attr} on {entry.get('dn')}: {e}")
    return None


# --- per-user lookups (GraphQL users query) ---------------------------------------------------

def fetch_gidNumber(username: str) -> Optional[int]:
    """Primary gidNumber for a user from the source (AD) ldap."""
    try:
        results = ldap_search(SOURCE_LDAP_CLIENT, SOURCE_LDAP_USER_BASEDN, f"(uid={username})",
                              attrlist=['gidNumber'], description=f"gidNumber lookup for {username}")
        if results and 'gidNumber' in results[0]:
            return int(results[0]['gidNumber'][0])
        LOG.warning(f"No entry found for {username} in SOURCE_LDAP")
    except Exception as e:
        LOG.warning(f"Failed to fetch gidNumber for {username}: {e}")
    return None


def fetch_secondaryGidNumbers(username: str) -> Optional[List[int]]:
    """gidNumbers of all posixGroups the user is a memberUid of. None when there are none (API contract)."""
    try:
        results = ldap_search(SDF_LDAP_CLIENT, SDF_LDAP_GROUP_BASEDN, f"(memberUid={username})",
                              attrlist=['gidNumber'], description=f"group gidNumber lookup for {username}")
        gidnumbers = sorted({g for g in (_first_int(e, 'gidNumber') for e in results) if g is not None})
        return gidnumbers if gidnumbers else None
    except Exception as e:
        LOG.warning(f"Failed to fetch group gidNumbers for {username}: {e}")
    return None


# --- bulk snapshot (migration / recurring sync) -----------------------------------------------

def fetch_all_posix_accounts() -> Dict[str, Tuple[Optional[int], Optional[int]]]:
    """All person entries with a uidNumber, keyed by uid -> (uidNumber, gidNumber). One paged search on the indexed
    uidNumber attribute (uid itself is not indexed in AD, so per-user/batched uid filters are more expensive).
    Used only by the one-time migration; the periodic sync does not read AD."""
    results = ldap_paged_search(SOURCE_LDAP_CLIENT, SOURCE_LDAP_USER_BASEDN,
                                "(&(objectclass=person)(uidNumber=*))",
                                attrlist=['uid', 'uidNumber', 'gidNumber'],
                                description="bulk posix account search")
    accounts: Dict[str, Tuple[Optional[int], Optional[int]]] = {}
    dupes = 0
    for entry in results:
        if 'uid' not in entry or not entry['uid']:
            continue
        uid = str(entry['uid'][0])
        if uid in accounts:
            dupes += 1
            continue
        accounts[uid] = (_first_int(entry, 'uidNumber'), _first_int(entry, 'gidNumber'))
    LOG.info(f"fetched {len(accounts)} posix accounts from SOURCE_LDAP ({dupes} duplicate uids skipped, first kept)")
    return accounts


def fetch_all_posix_groups() -> Tuple[Dict[str, Set[int]], int]:
    """All posixGroup entries inverted to memberUid -> {gidNumber}.

    Reads every group (~3k entries from the directory coactd manages): groups are what the server returns,
    so filtering by member would return the same large groups once per batch. Callers keep only the requested
    users' memberships. Returns (memberUid -> {gidNumber}, number of groups read).

    NIS-style split groups (e.g. atlas-a / atlas-b) share one gidNumber on purpose; the set dedupes
    them, which is exactly what fetch_secondaryGidNumbers returns for a member of only one shard.
    Do not add group-name normalisation here."""
    results = ldap_paged_search(SDF_LDAP_CLIENT, SDF_LDAP_GROUP_BASEDN, "(objectclass=posixGroup)",
                                attrlist=['gidNumber', 'memberUid'],
                                description="bulk posixGroup search")
    members: Dict[str, Set[int]] = {}
    groups = 0
    for entry in results:
        gid = _first_int(entry, 'gidNumber')
        if gid is None:
            continue
        groups += 1
        for uid in entry.get('memberUid', []) or []:
            members.setdefault(str(uid), set()).add(gid)
    LOG.info(f"fetched {groups} posixGroups from SDF_LDAP covering {len(members)} distinct members")
    return members, groups


def build_snapshot(usernames: Optional[Iterable[str]] = None) -> List[dict]:
    """One-time migration payload for usersPosixSync: [{username, uidnumber, gidnumber, secondarygids}] from AD
    (uid/primary gid) + SDF LDAP (secondary gids), restricted to `usernames` (the coact users) when given."""
    accounts = fetch_all_posix_accounts()
    groups, _ = fetch_all_posix_groups()
    wanted = set(usernames) if usernames is not None else None
    snapshot = []
    with_secondary = 0
    for uid, (uidnumber, gidnumber) in accounts.items():
        if wanted is not None and uid not in wanted:
            continue
        secondary = sorted(groups.get(uid, set()))
        if secondary:
            with_secondary += 1
        snapshot.append({
            "username": uid,
            "uidnumber": uidnumber,
            "gidnumber": gidnumber,
            "secondarygids": secondary,
        })
    scope = f"of {len(wanted)} requested users" if wanted is not None else "accounts"
    LOG.info(f"snapshot: {len(snapshot)} {scope}, {with_secondary} with secondary gids")
    return snapshot


def build_secondary_snapshot(usernames: Iterable[str]) -> Tuple[List[dict], int]:
    """Periodic-sync payload for usersSecondaryGidsSync: one [{username, secondarygids}] entry per requested user
    (empty list when the user is in no posixGroup), from SDF LDAP only. Returns (entries, posixGroups read)."""
    groups, group_count = fetch_all_posix_groups()
    entries = [{"username": u, "secondarygids": sorted(groups.get(u, set()))} for u in sorted(set(usernames))]
    with_secondary = sum(1 for e in entries if e["secondarygids"])
    LOG.info(f"secondary snapshot: {len(entries)} users, {with_secondary} with secondary gids, {group_count} posixGroups")
    return entries, group_count
