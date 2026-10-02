#!/usr/bin/env python3
"""
Recurring LDAP -> coact posix reconcile.

Reads the full uid/gid snapshot from LDAP and pushes it to coact-api's usersPosixSync mutation, which
computes the diff, applies its safety guard and writes. Stateless: run on a schedule (every 5-10 min).

Exit codes: 0 ok, 1 error, 2 coact-api aborted the sync (guard tripped or write failed).

Env: SOURCE_LDAP_*, SDF_LDAP_SERVER, SDF_LDAP_GROUP_BASEDN, COACT_API_URL, COACT_SYNC_USERNAME.
"""

import argparse
import logging
import sys
from os import environ

LOG = logging.getLogger("sync_posix")


def format_result(res: dict) -> str:
    return (f"dryRun={res['dryRun']} total={res['total']} matched={res['matched']} changed={res['changed']} "
            f"unknown={len(res['unknownUsers'])} uidMismatches={len(res['uidMismatches'])} "
            f"aborted={res['aborted']} reason={res['reason']}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--dry-run', action='store_true', help='compute and report the diff in coact-api without writing')
    p.add_argument('--force', action='store_true', help='override the coact-api churn / minimum-entries guard')
    p.add_argument('-v', '--verbose', action='store_true')
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose or environ.get('DEBUG') else logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')

    from ldap_posix import build_snapshot
    from coact_client import CoactClient

    try:
        snapshot = build_snapshot()
    except Exception as e:
        LOG.exception(f"failed to read LDAP snapshot: {e}")
        return 1
    if not snapshot:
        LOG.error("LDAP snapshot is empty; not calling coact-api")
        return 1

    try:
        res = CoactClient().users_posix_sync(snapshot, dry_run=args.dry_run, force=args.force)
    except Exception as e:
        LOG.exception(f"usersPosixSync failed: {e}")
        return 1

    LOG.info(format_result(res))
    if res['uidMismatches']:
        LOG.warning(f"uidnumber mismatches (coact vs ldap, not written): {res['uidMismatches'][:20]}"
                    + (" ..." if len(res['uidMismatches']) > 20 else ""))
    if res['aborted']:
        LOG.error(f"coact-api aborted the sync: {res['reason']}")
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
