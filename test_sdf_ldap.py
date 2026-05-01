#!/usr/bin/env python3
"""Test script for SDF LDAP gidNumber lookups."""

import sys
import logging

logging.basicConfig(level=logging.DEBUG)

from schema import (
    fetch_gidNumber,
    fetch_secondaryGidNumbers,
    SDF_LDAP_CLIENT,
    SDF_LDAP_SERVER,
    SDF_LDAP_USER_BASEDN,
)

def test_connection():
    print(f"SDF_LDAP_SERVER:      {SDF_LDAP_SERVER}")
    print(f"SDF_LDAP_USER_BASEDN: {SDF_LDAP_USER_BASEDN}")
    print()
    try:
        with SDF_LDAP_CLIENT.connect() as conn:
            print("Connection OK")
    except Exception as e:
        print(f"Connection FAILED: {e}")
        sys.exit(1)

def test_fetch_gidNumber(username):
    print(f"\n--- fetch_gidNumber('{username}') ---")
    result = fetch_gidNumber(username)
    print(f"  gidNumber: {result}")
    return result

def test_fetch_secondaryGidNumbers(username):
    print(f"\n--- fetch_secondaryGidNumbers('{username}') ---")
    result = fetch_secondaryGidNumbers(username)
    print(f"  secondaryGidNumbers: {result}")
    return result

if __name__ == "__main__":
    usernames = sys.argv[1:] if len(sys.argv) > 1 else ["ytl"]

    test_connection()

    for u in usernames:
        print(f"\n{'='*40}")
        print(f"User: {u}")
        print(f"{'='*40}")
        test_fetch_gidNumber(u)
        test_fetch_secondaryGidNumbers(u)
