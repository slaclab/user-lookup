#!/usr/bin/env python3
"""
One-shot migration: populate coact users with the uid/gid data currently in LDAP.

Steps
  1. read the full posix snapshot from LDAP and save it to --out/snapshot-<ts>.json
  2. dry-run usersPosixSync against coact-api and write a human-readable report next to it
  3. sanity checks (snapshot size, coact coverage, secondary gids present); stop unless they pass
  4. with --apply: run usersPosixSync for real with force=true (the first run necessarily changes every
     user, which trips coact-api's churn guard by design), then print posixSyncStatus

Re-running after a successful apply is safe and should report changed=0.
LDAP is only read. All writes go to coact-api (dev first: COACT_API_URL=http://coact-api-service:8000/graphql-service).

Env: SOURCE_LDAP_*, SDF_LDAP_SERVER, SDF_LDAP_GROUP_BASEDN, COACT_API_URL, COACT_SYNC_USERNAME.
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from os import environ, makedirs, path

LOG = logging.getLogger("migrate_posix")


def report_text(snapshot: list, res: dict) -> str:
    with_secondary = sum(1 for e in snapshot if e['secondarygids'])
    no_gid = [e['username'] for e in snapshot if e['gidnumber'] is None]
    lines = [
        "LDAP posix -> coact migration report",
        f"generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        f"LDAP accounts in snapshot:            {len(snapshot)}",
        f"  with >=1 secondary gid:             {with_secondary}",
        f"  with no primary gid:                {len(no_gid)}",
        f"coact users matched in snapshot:      {res['matched']}",
        f"coact users that would change:        {res['changed']}",
        f"coact users not in LDAP (untouched):  {len(res['unknownUsers'])}",
        f"uidnumber mismatches (not written):   {len(res['uidMismatches'])}",
        f"coact-api guard would abort:          {res['aborted']} ({res['reason']})",
        "",
        "coact users not in LDAP:",
        *[f"  {u}" for u in res['unknownUsers']],
        "",
        "uidnumber mismatches (coact vs ldap):",
        *[f"  {m}" for m in res['uidMismatches']],
        "",
        "LDAP accounts with no primary gid (first 50):",
        *[f"  {u}" for u in no_gid[:50]],
    ]
    return "\n".join(lines) + "\n"


def sanity_checks(snapshot: list, res: dict, min_accounts: int, min_coverage: float) -> list:
    problems = []
    if len(snapshot) < min_accounts:
        problems.append(f"snapshot has {len(snapshot)} accounts, expected at least {min_accounts} (paging or filter problem?)")
    if not any(e['secondarygids'] for e in snapshot):
        problems.append("no account has any secondary gid: SDF_LDAP group search returned nothing (check SDF_LDAP_SERVER / SDF_LDAP_GROUP_BASEDN)")
    coact_users = res['matched'] + len(res['unknownUsers'])
    coverage = res['matched'] / coact_users if coact_users else 0.0
    if coverage < min_coverage:
        problems.append(f"only {coverage:.1%} of coact (non-bot) users found in LDAP, expected >= {min_coverage:.0%}")
    return problems


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--out', default='./migration', help='directory for snapshot + report files')
    p.add_argument('--apply', action='store_true', help='write to coact after the dry run and sanity checks pass')
    p.add_argument('--min-accounts', type=int, default=1000, help='minimum LDAP accounts expected in the snapshot')
    p.add_argument('--min-coverage', type=float, default=0.95, help='minimum fraction of coact users that must be present in LDAP')
    p.add_argument('--yes', action='store_true', help='do not prompt for confirmation before applying')
    p.add_argument('-v', '--verbose', action='store_true')
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose or environ.get('DEBUG') else logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')

    from ldap_posix import build_snapshot
    from coact_client import CoactClient, COACT_API_URL

    ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    makedirs(args.out, exist_ok=True)

    # 1. snapshot
    snapshot = build_snapshot()
    snap_file = path.join(args.out, f"snapshot-{ts}.json")
    with open(snap_file, 'w') as f:
        json.dump(snapshot, f, indent=1)
    LOG.info(f"wrote {len(snapshot)} entries to {snap_file}")
    if not snapshot:
        LOG.error("empty snapshot; aborting")
        return 1

    # 2. dry run + report
    client = CoactClient()
    res = client.users_posix_sync(snapshot, dry_run=True)
    report = report_text(snapshot, res)
    report_file = path.join(args.out, f"report-{ts}.txt")
    with open(report_file, 'w') as f:
        f.write(report)
    print(report)
    LOG.info(f"wrote report to {report_file}")

    # 3. sanity
    problems = sanity_checks(snapshot, res, args.min_accounts, args.min_coverage)
    if problems:
        for pr in problems:
            LOG.error(f"sanity check failed: {pr}")
        return 1
    LOG.info("sanity checks passed")

    if not args.apply:
        LOG.info("dry run only; re-run with --apply to write to coact")
        return 0

    # 4. apply
    if not args.yes:
        answer = input(f"Write gid data for {res['changed']} users to {COACT_API_URL}? [y/N] ").strip().lower()
        if answer != 'y':
            LOG.info("not applying")
            return 0
    res = client.users_posix_sync(snapshot, dry_run=False, force=True)
    LOG.info(f"apply: total={res['total']} matched={res['matched']} changed={res['changed']} "
             f"aborted={res['aborted']} reason={res['reason']} syncedAt={res['syncedAt']}")
    if res['aborted']:
        LOG.error("coact-api aborted the write")
        return 2
    status = client.posix_sync_status()
    LOG.info(f"posixSyncStatus: {json.dumps(status)}")

    # 5. idempotency check
    verify = client.users_posix_sync(snapshot, dry_run=True)
    if verify['changed'] != 0:
        LOG.error(f"post-apply dry run still reports {verify['changed']} changes; investigate")
        return 2
    LOG.info("post-apply dry run reports 0 changes; migration complete")
    return 0


if __name__ == '__main__':
    sys.exit(main())
