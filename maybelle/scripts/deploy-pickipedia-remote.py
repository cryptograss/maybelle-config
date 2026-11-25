#!/usr/bin/env python3
"""
Deploy PickiPedia VPS from your laptop via maybelle

This script:
1. Gets the vault password from your environment
2. SSHs to maybelle and runs the deploy script there
3. The deploy script on maybelle handles ansible + Jenkins reporting

Usage:
    ./deploy-pickipedia-remote.py           # Normal deploy
    ./deploy-pickipedia-remote.py --fresh-host  # Fresh server (resets SSH keys)

Prerequisites:
- SSH access to maybelle from your laptop
- Vault password in ANSIBLE_VAULT_PASSWORD or ANSIBLE_VAULT_PASSWORD_FILE
"""

import subprocess
import sys
import os

# The deploy on maybelle runs for ten minutes or more with nothing to say (hunter's image
# build logs to a file on hunter, so the SSH channel carries no output at all), and a TCP
# connection that quiet gets dropped by a NAT or a venue's Wi-Fi. ssh then waits for a
# reply that never comes: maybelle finishes the deploy and posts its log, while the laptop
# hangs until Ctrl-C (Justin, #ops, 25 Nov 2026). These put a keepalive on the connection
# every 15 seconds, which also keeps the NAT mapping, and give up after 20 unanswered
# ones (5 minutes), so a connection that really died ends in an error, not a hang.
SSH_ALIVE = ['-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=20',
             '-o', 'ConnectTimeout=15']


def get_vault_password():
    """Get vault password from environment or file"""
    password = os.environ.get('ANSIBLE_VAULT_PASSWORD')
    if password:
        return password

    password_file = os.environ.get('ANSIBLE_VAULT_PASSWORD_FILE')
    if password_file:
        try:
            with open(password_file, 'r') as f:
                return f.read().strip()
        except FileNotFoundError:
            raise Exception(f"Vault password file not found: {password_file}")

    raise Exception("Neither ANSIBLE_VAULT_PASSWORD nor ANSIBLE_VAULT_PASSWORD_FILE is set")


def main():
    # Parse arguments
    fresh_host = '--fresh-host' in sys.argv

    print("=" * 60)
    print("DEPLOY PICKIPEDIA VPS VIA MAYBELLE")
    if fresh_host:
        print("(FRESH HOST - will reset SSH keys)")
    print("=" * 60)
    print()

    # Check for vault password early
    try:
        vault_password = get_vault_password()
        print("✓ Vault password found")
    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        print("\nSet one of:")
        print("  export ANSIBLE_VAULT_PASSWORD='your-password'")
        print("  export ANSIBLE_VAULT_PASSWORD_FILE='/path/to/password/file'")
        sys.exit(1)

    # Confirm
    print("\n" + "-" * 60)
    print("Will deploy PickiPedia VPS (5.78.112.39)")
    print("Stack: MariaDB + PHP-FPM + Nginx + Caddy + MediaWiki")
    if fresh_host:
        print("⚠  FRESH HOST MODE: Old SSH host keys will be removed")
    print("-" * 60)

    confirm = input("\nContinue? (y/n): ").strip().lower()
    if confirm != 'y':
        print("Cancelled")
        sys.exit(0)

    # Run deploy script on maybelle, passing vault password via stdin
    print("\nConnecting to maybelle...")
    print()

    maybelle = 'root@maybelle.cryptograss.live'
    deploy_script = '/mnt/persist/maybelle-config/maybelle/scripts/deploy-pickipedia.sh'

    # Pass --fresh-host flag to the remote script
    script_args = deploy_script
    if fresh_host:
        script_args += ' --fresh-host'

    result = subprocess.run(
        ['ssh', '-t', *SSH_ALIVE, maybelle, script_args],
        input=vault_password + '\n',
        text=True
    )

    sys.exit(result.returncode)


if __name__ == '__main__':
    main()
