"""Interactively enroll an existing identity for browser-based OAuth login.

Run this from a trusted administrative shell with the existing DB environment.
The password is read without echo, never accepted on the command line, and is
stored only as a salted scrypt hash. This command does not issue API keys.
"""

from __future__ import annotations

import argparse
import getpass

from apps.shared import database


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identity-id", type=int, required=True)
    args = parser.parse_args()
    username = input("OAuth username: ").strip().lower()
    password = getpass.getpass("New password (minimum 12 characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        parser.error("Passwords do not match.")
    database.set_oauth_password(args.identity_id, username, password)
    print("OAuth login credential enrolled for the existing identity.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
