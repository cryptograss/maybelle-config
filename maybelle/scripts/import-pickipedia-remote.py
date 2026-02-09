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
        ['ssh', '-t', maybelle, import_script],
        text=True
    )

    sys.exit(result.returncode)


if __name__ == '__main__':
    main()
