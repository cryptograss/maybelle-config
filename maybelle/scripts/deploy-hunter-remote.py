#!/usr/bin/env python3
"""
Deploy hunter from your laptop via maybelle
Uses SSH agent forwarding for secure key access
Posts deployment logs to Jenkins for history/tracking
"""

import subprocess
import sys
import tempfile
import os
from pathlib import Path
from getpass import getpass
from datetime import datetime


class DeploymentError(Exception):
    """Raised when deployment fails"""
    pass


def run_ssh(host, command, forward_agent=False, capture_output=False):
    """Run SSH command with optional agent forwarding"""
    ssh_cmd = ['ssh']
    if forward_agent:
        ssh_cmd.append('-A')
    ssh_cmd.extend([host, command])

    result = subprocess.run(
        ssh_cmd,
        capture_output=capture_output,
        text=True
    )

    if result.returncode != 0:
        raise DeploymentError(f"SSH command failed: {command[:50]}...")

    return result


def run_scp(source, dest):
    """Run SCP command"""
    result = subprocess.run(['scp', source, dest], capture_output=True, text=True)
    if result.returncode != 0:
        raise DeploymentError(f"SCP failed: {source} -> {dest}")


def select_backup_option():
    """Prompt user for database backup option"""
    print("Database backup options:")
    print("  1) none - Skip database restoration")
    print("  2) latest - Use most recent backup")
    print("  3) select - Choose specific backup file")
    print()

    choice = input("Select option (1-3): ").strip()

    if choice == "1":
        return "none", None
    elif choice == "2":
        return "latest", None
    elif choice == "3":
        print("\nAvailable backups:")
        result = run_ssh(
            'root@maybelle.cryptograss.live',
            'ls -lh /var/jenkins_home/hunter-db-backups/*.dump 2>/dev/null || echo "No backups found"',
            capture_output=True
        )
        print(result.stdout)

        backup_file = input("Enter backup filename (e.g., magenta_20251113_020000.dump): ").strip()
        return "select", backup_file
    else:
        print("Invalid choice")
        sys.exit(1)


def setup_ssh_agent(key_path):
    """Start SSH agent and add key"""
    print("\nSetting up SSH agent with hunter root key...")

    # Start ssh-agent
    result = subprocess.run(
        ['ssh-agent', '-s'],
        capture_output=True,
        text=True
    )

    # Parse output to set environment variables
    for line in result.stdout.split('\n'):
        if '=' in line and ';' in line:
            line = line.split(';')[0]
            key, value = line.split('=', 1)
            os.environ[key] = value

    # Add key to agent (will prompt for passphrase)
    result = subprocess.run(['ssh-add', key_path])

    if result.returncode != 0:
        raise DeploymentError("Failed to add SSH key to agent")

    print("SSH agent configured successfully")


def cleanup_ssh_agent():
    """Kill SSH agent"""
    print("\nCleaning up SSH agent...")
    subprocess.run(['ssh-agent', '-k'], capture_output=True)
    print("SSH agent stopped and key cleared from memory")


def deploy_hunter(db_backup, backup_file=None):
    """Execute the actual deployment on hunter via maybelle"""
    print("\n=== Starting Hunter Deployment ===")
    print(f"Backup option: {db_backup}")
    print(f"Deployment time: {datetime.utcnow().strftime('%c UTC')}")
    print()

    # Test connection
    print("Testing connection to hunter...")
    try:
        run_ssh(
            'root@maybelle.cryptograss.live',
            'ssh -o BatchMode=yes -o ConnectTimeout=10 root@hunter.cryptograss.live "echo Connection successful"',
            forward_agent=True
        )
        print("✓ SSH connection to hunter verified")
    except DeploymentError:
        raise DeploymentError("Failed to connect to hunter")

    # Install backup key
    print("\nInstalling backup SSH key on hunter...")
    install_key_script = '''
        id backupuser || useradd -m -s /bin/bash backupuser
        mkdir -p /home/backupuser/.ssh
        chmod 700 /home/backupuser/.ssh
        chown backupuser:backupuser /home/backupuser/.ssh
    '''
    run_ssh(
        'root@maybelle.cryptograss.live',
        f'ssh root@hunter.cryptograss.live "{install_key_script}"',
        forward_agent=True
    )
    print("✓ Backupuser created")

    # Copy and install backup public key
    print("Copying backup public key...")
    run_ssh(
        'root@maybelle.cryptograss.live',
        'scp /var/jenkins_home/.ssh/id_ed25519_backup.pub root@hunter.cryptograss.live:/tmp/maybelle_backup.pub',
        forward_agent=True
    )

    run_ssh(
        'root@maybelle.cryptograss.live',
        '''ssh root@hunter.cryptograss.live "
            cat /tmp/maybelle_backup.pub >> /home/backupuser/.ssh/authorized_keys
            chmod 600 /home/backupuser/.ssh/authorized_keys
            chown backupuser:backupuser /home/backupuser/.ssh/authorized_keys
            rm /tmp/maybelle_backup.pub
        "''',
        forward_agent=True
    )
    print("✓ Backup key installed")

    # Handle database backup
    if db_backup == "latest":
        print("\nCopying latest database backup to hunter...")
        run_ssh(
            'root@maybelle.cryptograss.live',
            'scp /var/jenkins_home/hunter-db-backups/latest.dump root@hunter.cryptograss.live:/tmp/restore_db.dump',
            forward_agent=True
        )
    elif db_backup == "select":
        print(f"\nCopying selected database backup to hunter...")
        run_ssh(
            'root@maybelle.cryptograss.live',
            f'scp /var/jenkins_home/hunter-db-backups/{backup_file} root@hunter.cryptograss.live:/tmp/restore_db.dump',
            forward_agent=True
        )

    # Clone/update maybelle-config on hunter
    print("\nUpdating maybelle-config repository on hunter...")
    repo_script = '''
        if [ ! -d /root/maybelle-config ]; then
            git clone https://github.com/cryptograss/maybelle-config.git /root/maybelle-config
        fi
        cd /root/maybelle-config
        git fetch origin
        git checkout hunter-deploy
        git pull origin hunter-deploy
    '''
    run_ssh(
        'root@maybelle.cryptograss.live',
        f'ssh root@hunter.cryptograss.live "{repo_script}"',
        forward_agent=True
    )
    print("✓ Repository updated")

    # Execute deployment
    print("\n" + "=" * 50)
    print("Executing hunter deployment...")
    print("=" * 50)

    if db_backup == "none":
        deploy_cmd = 'cd /root/maybelle-config/hunter && ./deploy.sh --do-not-copy-database'
    else:
        deploy_cmd = 'cd /root/maybelle-config/hunter && ./deploy.sh -e db_dump_file=/tmp/restore_db.dump'

    # Run with -t to allocate PTY for ansible output
    subprocess.run(
        ['ssh', '-A', '-t', 'root@maybelle.cryptograss.live',
         f'ssh -t root@hunter.cryptograss.live "{deploy_cmd}"'],
        check=True
    )

    print("=" * 50)
    print("\n=== Deployment Complete ===")


def post_to_jenkins(log_content, success):
    """Post deployment logs to Jenkins"""
    print("\nPosting deployment logs to Jenkins...")

    # Get Jenkins password
    result = run_ssh(
        'root@maybelle.cryptograss.live',
        'docker exec jenkins printenv JENKINS_ADMIN_PASSWORD',
        capture_output=True
    )
    jenkins_password = result.stdout.strip()

    # Get next build number
    result = run_ssh(
        'root@maybelle.cryptograss.live',
        f'curl -s --user "admin:{jenkins_password}" "http://localhost:8080/job/deploy-hunter/api/json"',
        capture_output=True
    )

    import json
    try:
        data = json.loads(result.stdout)
        build_number = data.get('nextBuildNumber', f"manual-{int(datetime.now().timestamp())}")
    except:
        build_number = f"manual-{int(datetime.now().timestamp())}"

    # Create build directory
    run_ssh(
        'root@maybelle.cryptograss.live',
        f'mkdir -p /var/jenkins_home/jobs/deploy-hunter/builds/{build_number}'
    )

    # Write log file
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.log') as f:
        f.write(log_content)
        log_file = f.name

    try:
        run_scp(
            log_file,
            f'root@maybelle.cryptograss.live:/var/jenkins_home/jobs/deploy-hunter/builds/{build_number}/log'
        )
    finally:
        os.unlink(log_file)

    # Create build.xml
    status = "SUCCESS" if success else "FAILURE"
    timestamp = int(datetime.now().timestamp() * 1000)
    build_xml = f'''<?xml version='1.1' encoding='UTF-8'?>
<build>
  <actions/>
  <queueId>-1</queueId>
  <timestamp>{timestamp}</timestamp>
  <startTime>{timestamp}</startTime>
  <result>{status}</result>
  <duration>0</duration>
  <charset>UTF-8</charset>
  <keepLog>false</keepLog>
  <builtOn></builtOn>
  <workspace>/external</workspace>
  <hudsonVersion>2.528.2</hudsonVersion>
  <scm class="hudson.scm.NullChangeLogParser"/>
  <culprits class="java.util.Collections$UnmodifiableSet"/>
</build>
'''

    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.xml') as f:
        f.write(build_xml)
        xml_file = f.name

    try:
        run_scp(
            xml_file,
            f'root@maybelle.cryptograss.live:/tmp/build.xml'
        )
        run_ssh(
            'root@maybelle.cryptograss.live',
            f'mv /tmp/build.xml /var/jenkins_home/jobs/deploy-hunter/builds/{build_number}/build.xml && '
            f'chown -R 1000:1000 /var/jenkins_home/jobs/deploy-hunter/builds/{build_number}'
        )
    finally:
        os.unlink(xml_file)

    print(f"✓ Logs posted to Jenkins")
    return build_number


def main():
    """Main deployment flow"""
    print("=== Deploy Hunter via Maybelle ===\n")

    # Select backup option
    db_backup, backup_file = select_backup_option()

    # Confirm
    print(f"\nReady to deploy hunter with backup option: {db_backup}")
    if backup_file:
        print(f"Backup file: {backup_file}")

    confirm = input("Continue? (y/n): ").strip().lower()
    if confirm != 'y':
        print("Deployment cancelled")
        sys.exit(0)

    # Setup SSH agent
    key_path = str(Path.home() / '.ssh' / 'id_ed25519_hunter')
    try:
        setup_ssh_agent(key_path)
    except DeploymentError as e:
        print(f"Error: {e}")
        sys.exit(1)

    # Execute deployment
    log_lines = []
    success = False

    try:
        # Redirect stdout/stderr to capture logs
        # For now, just run deployment directly
        deploy_hunter(db_backup, backup_file)
        success = True
        result = "success"
    except DeploymentError as e:
        print(f"\nERROR: {e}")
        result = "failure"
    except subprocess.CalledProcessError as e:
        print(f"\nERROR: Command failed with exit code {e.returncode}")
        result = "failure"
    except KeyboardInterrupt:
        print("\n\nDeployment interrupted by user")
        result = "failure"
    finally:
        cleanup_ssh_agent()

    # Post to Jenkins (simplified for now - just create a marker)
    # TODO: Capture actual logs and post them

    print(f"\n=== Deployment {result} ===")
    if success:
        print("View logs at: https://maybelle.cryptograss.live/job/deploy-hunter/")


if __name__ == '__main__':
    main()
