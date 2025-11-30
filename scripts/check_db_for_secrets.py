#!/usr/bin/env python3
"""
Check database for unredacted secrets.

Usage (from maybelle, using scrubber's secrets):
    python3 check_db_for_secrets.py --scrubber-url http://scrubber:8001

Usage (locally, with vault decryption):
    ansible-vault view secrets/vault.yml | python3 scripts/check_db_for_secrets.py --secrets-stdin

The script connects to the postgres database and scans messages for secrets.
"""

import argparse
import json
import os
import sys
import re
from typing import List, Tuple

try:
    import psycopg2
except ImportError:
    print("psycopg2 not installed. Run: pip install psycopg2-binary")
    sys.exit(1)


def load_secrets_from_stdin() -> List[str]:
    """Load secrets from YAML on stdin (piped from ansible-vault view)."""
    try:
        import yaml
    except ImportError:
        print("PyYAML not installed. Run: pip install pyyaml")
        sys.exit(1)

    data = yaml.safe_load(sys.stdin)
    secrets = [v for v in data.values() if isinstance(v, str) and len(v) > 4]
    return secrets


def load_secrets_from_scrubber(url: str) -> List[str]:
    """Get secret count from scrubber (we can't get the actual secrets, but we can test)."""
    try:
        import requests
    except ImportError:
        print("requests not installed. Run: pip install requests")
        sys.exit(1)

    response = requests.get(f"{url}/health")
    if response.status_code == 200:
        data = response.json()
        print(f"Scrubber has {data['secrets_loaded']} secrets loaded")
        return None  # Signal to use scrubber API for checking
    else:
        print(f"Failed to reach scrubber: {response.status_code}")
        sys.exit(1)


def get_db_connection():
    """Connect to postgres using environment variables."""
    return psycopg2.connect(
        host=os.environ.get('POSTGRES_HOST', 'localhost'),
        port=os.environ.get('POSTGRES_PORT', '5432'),
        dbname=os.environ.get('POSTGRES_DB', 'magenta_memory'),
        user=os.environ.get('POSTGRES_USER', 'magent'),
        password=os.environ.get('POSTGRES_PASSWORD', '')
    )


def check_with_secrets_list(secrets: List[str], fix: bool = False) -> List[Tuple[int, str, str]]:
    """Check database for secrets using a local secrets list."""
    conn = get_db_connection()
    cur = conn.cursor()

    findings = []
    fixed_count = 0

    # Get all messages
    cur.execute("SELECT id, content FROM conversations_message")
    rows = cur.fetchall()

    print(f"Scanning {len(rows)} messages for {len(secrets)} secrets...")

    for msg_id, content in rows:
        if not content:
            continue

        found_secrets = []
        new_content = content

        for secret in secrets:
            if secret in content:
                # Mask the secret for display (show first 4 chars)
                masked = secret[:4] + '...' + secret[-2:] if len(secret) > 6 else '****'
                found_secrets.append(masked)
                new_content = new_content.replace(secret, '[REDACTED]')

        if found_secrets:
            findings.append((msg_id, ', '.join(found_secrets), content[:100]))

            if fix:
                cur.execute(
                    "UPDATE conversations_message SET content = %s WHERE id = %s",
                    (new_content, msg_id)
                )
                fixed_count += 1

    if fix and fixed_count > 0:
        conn.commit()
        print(f"Fixed {fixed_count} messages")

    cur.close()
    conn.close()

    return findings


def check_with_scrubber(scrubber_url: str, fix: bool = False) -> List[Tuple[int, str]]:
    """Check database for secrets using the scrubber API."""
    import requests

    conn = get_db_connection()
    cur = conn.cursor()

    findings = []
    fixed_count = 0
    batch_size = 100

    # Get message count
    cur.execute("SELECT COUNT(*) FROM conversations_message")
    total = cur.fetchone()[0]
    print(f"Scanning {total} messages using scrubber API...")

    # Process in batches
    cur.execute("SELECT id, content FROM conversations_message ORDER BY id")

    batch = []
    batch_ids = []

    for msg_id, content in cur:
        if not content:
            continue

        batch.append(content)
        batch_ids.append(msg_id)

        if len(batch) >= batch_size:
            # Send batch to scrubber
            response = requests.post(
                f"{scrubber_url}/scrub/batch",
                json={"texts": batch},
                timeout=30
            )

            if response.status_code == 200:
                result = response.json()
                scrubbed = result['texts']

                for i, (orig, scrub, mid) in enumerate(zip(batch, scrubbed, batch_ids)):
                    if orig != scrub:
                        findings.append((mid, orig[:100]))

                        if fix:
                            cur.execute(
                                "UPDATE conversations_message SET content = %s WHERE id = %s",
                                (scrub, mid)
                            )
                            fixed_count += 1

            batch = []
            batch_ids = []

    # Process remaining
    if batch:
        response = requests.post(
            f"{scrubber_url}/scrub/batch",
            json={"texts": batch},
            timeout=30
        )

        if response.status_code == 200:
            result = response.json()
            scrubbed = result['texts']

            for i, (orig, scrub, mid) in enumerate(zip(batch, scrubbed, batch_ids)):
                if orig != scrub:
                    findings.append((mid, orig[:100]))

                    if fix:
                        cur.execute(
                            "UPDATE conversations_message SET content = %s WHERE id = %s",
                            (scrub, mid)
                        )
                        fixed_count += 1

    if fix and fixed_count > 0:
        conn.commit()
        print(f"Fixed {fixed_count} messages")

    cur.close()
    conn.close()

    return findings


def main():
    parser = argparse.ArgumentParser(description='Check database for unredacted secrets')
    parser.add_argument('--secrets-stdin', action='store_true',
                        help='Read secrets from YAML on stdin (pipe from ansible-vault view)')
    parser.add_argument('--scrubber-url', type=str,
                        help='URL of scrubber service (e.g., http://scrubber:8001)')
    parser.add_argument('--fix', action='store_true',
                        help='Actually redact the secrets (default is dry-run)')

    args = parser.parse_args()

    if not args.secrets_stdin and not args.scrubber_url:
        parser.error("Must specify either --secrets-stdin or --scrubber-url")

    if args.secrets_stdin and args.scrubber_url:
        parser.error("Cannot use both --secrets-stdin and --scrubber-url")

    if args.fix:
        print("*** FIX MODE: Will update database ***")
    else:
        print("*** DRY RUN: No changes will be made ***")

    if args.secrets_stdin:
        secrets = load_secrets_from_stdin()
        print(f"Loaded {len(secrets)} secrets from stdin")
        findings = check_with_secrets_list(secrets, fix=args.fix)
    else:
        load_secrets_from_scrubber(args.scrubber_url)  # Just to verify it's up
        findings = check_with_scrubber(args.scrubber_url, fix=args.fix)

    print(f"\nFound {len(findings)} messages with secrets:")
    for finding in findings[:20]:  # Show first 20
        if len(finding) == 3:
            msg_id, secrets_found, preview = finding
            print(f"  ID {msg_id}: {secrets_found}")
        else:
            msg_id, preview = finding
            print(f"  ID {msg_id}: {preview}...")

    if len(findings) > 20:
        print(f"  ... and {len(findings) - 20} more")

    if findings and not args.fix:
        print("\nRun with --fix to redact these secrets")


if __name__ == '__main__':
    main()
