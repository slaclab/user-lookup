"""
Minimal GraphQL client for the LDAP posix sync: which users to read, and pushing their snapshot to coact-api.

Identity is asserted with the same header coact-api trusts from its ingress (x-vouch-idp-claims-name by
default), so this must only ever talk to coact-api over the in-cluster service address.
"""

from os import environ
from typing import List, Optional
import logging

import requests

LOG = logging.getLogger(__name__)

COACT_API_URL = environ.get('COACT_API_URL', 'http://coact-api-service:8000/graphql-service')
COACT_SYNC_USERNAME = environ.get('COACT_SYNC_USERNAME', 'user-lookup-bot')
COACT_USERNAME_HEADER = environ.get('COACT_USERNAME_HEADER', 'x-vouch-idp-claims-name')
COACT_TIMEOUT = int(environ.get('COACT_TIMEOUT', 300))

USERS_POSIX_SYNC = """
mutation UsersPosixSync($entries: [UserPosixInput!]!, $dryRun: Boolean!, $force: Boolean!) {
  usersPosixSync(entries: $entries, dryRun: $dryRun, force: $force) {
    dryRun total matched changed unknownUsers uidMismatches aborted reason syncedAt
  }
}
"""

POSIX_SYNC_USERNAMES = """
query PosixSyncUsernames($includeUnsynced: Boolean!) { posixSyncUsernames(includeUnsynced: $includeUnsynced) }
"""

USERS_SECONDARY_GIDS_SYNC = """
mutation UsersSecondaryGidsSync($entries: [UserSecondaryGidsInput!]!, $groupCount: Int!, $dryRun: Boolean!, $force: Boolean!) {
  usersSecondaryGidsSync(entries: $entries, groupCount: $groupCount, dryRun: $dryRun, force: $force) {
    dryRun total matched changed unknownUsers uidMismatches aborted reason syncedAt unsynced
  }
}
"""

POSIX_SYNC_STATUS = """
query PosixSyncStatus {
  posixSyncStatus { lastrun lastsuccess kind dryRun total matched changed unknownUsers aborted reason groupCount lastGroupCount unsynced }
}
"""


class CoactClient:
    def __init__(self, url: str = COACT_API_URL, username: str = COACT_SYNC_USERNAME,
                 header: str = COACT_USERNAME_HEADER, timeout: int = COACT_TIMEOUT):
        self.url = url
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"content-type": "application/json", header: username})
        LOG.info(f"coact-api at {url} as {username}")

    def execute(self, query: str, variables: Optional[dict] = None) -> dict:
        r = self.session.post(self.url, json={"query": query, "variables": variables or {}}, timeout=self.timeout)
        r.raise_for_status()
        body = r.json()
        if body.get("errors"):
            raise RuntimeError(f"graphql errors: {body['errors']}")
        return body["data"]

    def users_posix_sync(self, entries: List[dict], dry_run: bool, force: bool = False) -> dict:
        return self.execute(USERS_POSIX_SYNC, {"entries": entries, "dryRun": dry_run, "force": force})["usersPosixSync"]

    def posix_sync_usernames(self, include_unsynced: bool = False) -> List[str]:
        """Non-bot coact users to read from LDAP: initialised users only, or all of them (migration)."""
        return self.execute(POSIX_SYNC_USERNAMES, {"includeUnsynced": include_unsynced})["posixSyncUsernames"]

    def users_secondary_gids_sync(self, entries: List[dict], group_count: int, dry_run: bool, force: bool = False) -> dict:
        return self.execute(USERS_SECONDARY_GIDS_SYNC, {"entries": entries, "groupCount": group_count,
                                                        "dryRun": dry_run, "force": force})["usersSecondaryGidsSync"]

    def posix_sync_status(self) -> Optional[dict]:
        return self.execute(POSIX_SYNC_STATUS)["posixSyncStatus"]
