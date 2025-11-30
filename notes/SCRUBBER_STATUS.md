# Scrubber Service Status - November 30, 2025

## What We Built
- Isolated scrubber container for secrets redaction
- FastAPI service at port 8001 with `/scrub` and `/scrub/batch` endpoints
- Secrets extracted locally in chapter-1.sh, copied to `/mnt/persist/scrubber-secrets/secrets.json`
- Memory Lane calls scrubber API via `SCRUBBER_URL=http://scrubber:8001`
- Jenkins Docker socket removed for security

## Current Issue
Secrets are NOT being redacted. Tested with Alchemy API key `KeT2pwb7STFmRCUjRgGbP` from arthel/.env - appeared in plain text in spy view.

## Debugging Needed
1. Check if scrubber container is running: `docker ps | grep scrubber`
2. Check scrubber health from inside: `docker exec scrubber curl -s http://localhost:8001/health`
3. Check if secrets file exists: `docker exec scrubber cat /app/secrets/secrets.json | head -c 200`
4. Check Memory Lane logs for scrubber calls: `docker logs memory-lane | grep -i scrub`
5. Verify the Alchemy key is in the vault (it might be in arthel/.env but not vault.yml)

## Key Files
- Scrubber code: `/home/magent/workspace/magenta/scrubber/main.py`
- Scrubber Dockerfile: `/home/magent/workspace/magenta/scrubber/Dockerfile`
- Ingest view calling scrubber: `/home/magent/workspace/magenta/conversations/views.py` (around line 876)
- Secrets extraction: `/home/magent/workspace/maybelle-config/maybelle/scripts/maybelle-chapter-1.sh` (lines 66-79)
- Docker compose with scrubber: `/home/magent/workspace/maybelle-config/maybelle/ansible/maybelle.yml` (around line 369)

## Possible Issues
1. Scrubber container not running or crashing
2. Secrets file not mounted correctly (should be at `/mnt/persist/scrubber-secrets/secrets.json`)
3. Alchemy key not in vault.yml (only in arthel/.env which isn't a vault secret)
4. Memory Lane not actually calling the scrubber (check SCRUBBER_URL env var)
5. Network issue between memory-lane and scrubber containers

## Also Note
- The /stream/ endpoint was renamed to /spy/ but deploy might not have picked it up yet
- Deployed chapter-1 with new scrubber infrastructure
- Watcher is successfully ingesting messages (confirmed in logs)
