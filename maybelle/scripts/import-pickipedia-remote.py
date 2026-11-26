#!/usr/bin/env python3
"""
Import PickiPedia data to VPS from your laptop via maybelle

This script:
1. SSHs to maybelle
2. Runs the import script there (which imports DB, images, and secrets to VPS)

Usage:
    ./import-pickipedia-remote.py

Prerequisites:
- SSH access to maybelle from your laptop
- PickiPedia VPS already deployed (run deploy-pickipedia-remote.py first)
- Backups exist on maybelle at /mnt/persist/pickipedia/backups/
"""

import subprocess
import sys

# The deploy on maybelle runs for ten minutes or more with nothing to say (hunter's image
# build logs to a file on hunter, so the SSH channel carries no output at all), and a TCP
# connection that quiet gets dropped by a NAT or a venue's Wi-Fi. ssh then waits for a
# reply that never comes: maybelle finishes the deploy and posts its log, while the laptop
# hangs until Ctrl-C (Justin, #ops, 25 Nov 2026). These put a keepalive on the connection
# every 15 seconds, which also keeps the NAT mapping, and give up after 20 unanswered
# ones (5 minutes), so a connection that really died ends in an error, not a hang.
SSH_ALIVE = ['-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=20',
             '-o', 'ConnectTimeout=15']


def main():
    print("=" * 60)
    print("IMPORT PICKIPEDIA DATA VIA MAYBELLE")
    print("=" * 60)
    print()
    print("This will:")
    print("  1. Import database from maybelle backups")
    print("  2. Import images from maybelle backups")
    print("  3. Configure secrets (LocalSettings.local.php)")
    print("  4. Run MediaWiki update.php")
    print()
    print("-" * 60)

    confirm = input("\nContinue? (y/n): ").strip().lower()
    if confirm != 'y':
        print("Cancelled")
        sys.exit(0)

    print("\nConnecting to maybelle...")
    print()

    maybelle = 'root@maybelle.cryptograss.live'
    import_script = '/mnt/persist/maybelle-config/maybelle/scripts/import-pickipedia.sh'

    result = subprocess.run(
        ['ssh', '-t', *SSH_ALIVE, maybelle, import_script],
        text=True
    )

    sys.exit(result.returncode)


if __name__ == '__main__':
    main()
