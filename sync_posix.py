#!/usr/bin/env python3
"""
Fallback SDF LDAP -> coact supplemental-gid reconcile.

coactd (sdf-cli) records group membership changes in coact as it makes them; this catches anything it missed.
Only secondary gids of already-initialised users are reconciled: uid and primary gid were set by the one-time
migration (or at registration for new users) and are not re-read, so this never touches AD.

  1. ask coact-api for the initialised users (posixSyncUsernames)
  2. read every posixGroup from SDF LDAP once (anonymous) and keep those users' memberships
  3. push one {username, secondarygids} entry per user to usersSecondaryGidsSync, which diffs, applies its
     guards (coverage, posixGroup count vs last successful run, churn) and writes secondarygids only

Logs counts only. changed > 0 means coactd missed a membership change; unsynced > 0 means users that were never
initialised at registration (not handled here). Stateless; run infrequently (every few days).

Exit codes: 0 ok, 1 error, 2 coact-api aborted the sync (guard tripped or write failed).

Env: SDF_LDAP_SERVER, SDF_LDAP_GROUP_BASEDN, COACT_API_URL, COACT_SYNC_USERNAME.
"""

import argparse
import logging
import sys
from os import environ

LOG = logging.getLogger("sync_posix")


def format_result(res: dict) -> str:
    return (f"dryRun={res['dryRun']} matched={res['matched']} changed={res['changed']} "
            f"unknown={len(res['unknownUsers'])} unsynced={res.get('unsynced')} "
            f"aborted={res['aborted']} reason={res['reason']}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--dry-run', action='store_true', help='compute and report the diff in coact-api without writing')
    p.add_argument('--force', action='store_true', help='override the coact-api coverage / group-count / churn guards')
    p.add_argument('-v', '--verbose', action='store_true')
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose or environ.get('DEBUG') else logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')

    from ldap_posix import build_secondary_snapshot
    from coact_client import CoactClient

    client = CoactClient()
    try:
        usernames = client.posix_sync_usernames()
    except Exception as e:
        LOG.exception(f"failed to list coact users: {e}")
        return 1
    if not usernames:
        LOG.error("coact-api returned no initialised users; nothing to reconcile (has the migration run?)")
        return 1

    try:
        entries, group_count = build_secondary_snapshot(usernames)
    except Exception as e:
        LOG.exception(f"failed to read SDF LDAP posixGroups: {e}")
        return 1
    if group_count == 0:
        LOG.error("SDF LDAP returned no posixGroups; not calling coact-api")
        return 1

    try:
        res = client.users_secondary_gids_sync(entries, group_count, dry_run=args.dry_run, force=args.force)
    except Exception as e:
        LOG.exception(f"usersSecondaryGidsSync failed: {e}")
        return 1

    LOG.info(f"requested={len(usernames)} groups={group_count} " + format_result(res))
    if res['changed'] and not res['aborted']:
        verb = "would correct" if args.dry_run else "corrected"
        LOG.warning(f"{verb} secondary gids for {res['changed']} users; coactd may have missed membership changes")
    if res['aborted']:
        LOG.error(f"coact-api aborted the sync: {res['reason']}")
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
