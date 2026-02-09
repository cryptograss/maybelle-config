# PickiPedia VPS Migration

Infrastructure for migrating PickiPedia from NearlyFreeSpeech to a dedicated Hetzner VPS.

## Background

PickiPedia was hosted on NearlyFreeSpeech shared hosting, which hit connection limits during a large video upload, causing complete PHP failure. Rather than troubleshoot NFSN's shared environment, we're migrating to our own VPS for full control.

## Recommended Server

**Hetzner CX32** (~€8/month)
- 4 vCPU (AMD EPYC)
- 8 GB RAM
- 80 GB NVMe SSD
- Location: Falkenstein or Nuremberg (network locality with hunter/maybelle)

## Stack

- **OS**: Ubuntu 24.04 LTS
- **Web Server**: Nginx + Caddy (SSL termination)
- **PHP**: PHP-FPM 8.2
- **Database**: MariaDB 10.11 LTS
- **SSL**: Let's Encrypt via Caddy (automatic)

## Prerequisites

1. **Order Hetzner VPS**
   - Go to https://console.hetzner.cloud
   - Create CX32 server with Ubuntu 24.04
   - Add your SSH key during creation
   - Note the IP address

2. **Update DNS**
   - Create `pickipedia.cryptograss.live` A record pointing to new server IP
   - Keep `pickipedia.xyz` pointing to NFSN until ready to switch

3. **Add SSH key to new server**
   - The deploy script runs from maybelle, so maybelle needs SSH access
   - Add maybelle's root SSH key to the new server's authorized_keys

## Deployment

Deployment runs FROM maybelle (same pattern as hunter):

```bash
# From your laptop - pipe vault password to deploy script
echo "$ANSIBLE_VAULT_PASSWORD" | ssh root@maybelle.cryptograss.live \
    /mnt/persist/maybelle-config/maybelle/scripts/deploy-pickipedia.sh --fresh-host
```

The `--fresh-host` flag handles SSH host key setup for the new server.

## Post-Deployment Migration

After the playbook completes, SSH to the new server and run the migration:

```bash
ssh root@pickipedia.cryptograss.live

# Run the import script
/usr/local/bin/import-pickipedia-backup.sh

# Create LocalSettings.local.php with secrets
cp /var/www/pickipedia/LocalSettings.local.php.template /var/www/pickipedia/LocalSettings.local.php
vim /var/www/pickipedia/LocalSettings.local.php
# Fill in: $wgDBpassword, $wgSecretKey, $wgUpgradeKey
# Get these values from the vault or the current NFSN installation
```

## DNS Cutover

Once the new server is verified working:

1. Update `pickipedia.xyz` A record to point to new server IP
2. Update `www.pickipedia.xyz` CNAME or A record
3. Wait for DNS propagation (usually 5-30 minutes)
4. Update maybelle's Jenkins jobs to deploy to new server instead of NFSN

## Files

```
pickipedia-vps/
├── README.md                 # This file
└── ansible/
    ├── inventory.yml         # Server config
    ├── playbook.yml          # Main playbook
    └── roles/
        ├── mariadb/          # Database
        ├── php-fpm/          # PHP processing
        ├── nginx/            # Local web server
        ├── caddy/            # SSL termination
        └── mediawiki/        # Wiki application
```

## Backup Sources

Backups are pulled from maybelle:
- **Database**: `/mnt/persist/pickipedia/backups/pickipedia_YYYYMMDD.sql.gz`
- **Images**: `/mnt/persist/pickipedia/backups/images/`

Daily backups run at 3:30 AM via the `backup-pickipedia` Jenkins job.

## Monitoring

After migration, update the `pickipedia-uptime` Jenkins job to point at the new server. The health check endpoint is available at:

```
https://pickipedia.xyz/wiki/Main_Page  # Main page check
http://[server-ip]:8081/health          # Caddy health endpoint
```

## Rolling Back

If migration fails, simply point DNS back to NFSN. The old installation will still be there (though possibly still having the connection issues that prompted this migration).

## Future Jenkins Integration

The `pickipedia-build` and `pickipedia-rsync-status` jobs will need updating to deploy to the new server instead of NFSN. This involves:

1. Adding SSH key for new server to maybelle
2. Updating deploy script target in `deploy-pickipedia-to-nfs.sh`
3. Testing rsync deployment

This is a separate task from the migration itself.

## Quick Reference

```bash
# Initial deployment (from laptop)
echo "$ANSIBLE_VAULT_PASSWORD" | ssh root@maybelle.cryptograss.live \
    /mnt/persist/maybelle-config/maybelle/scripts/deploy-pickipedia.sh --fresh-host

# Subsequent deploys (no --fresh-host needed)
echo "$ANSIBLE_VAULT_PASSWORD" | ssh root@maybelle.cryptograss.live \
    /mnt/persist/maybelle-config/maybelle/scripts/deploy-pickipedia.sh

# SSH to new server
ssh root@pickipedia.cryptograss.live

# Run migration import
/usr/local/bin/import-pickipedia-backup.sh

# Check services
systemctl status nginx php8.2-fpm mariadb caddy
```
