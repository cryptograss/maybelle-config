# Scrubber Service Status - November 30, 2025

## Status: WORKING ✓

Secrets are being redacted successfully as of block ~21300000.

## Architecture
- **Scrubber container**: FastAPI service at port 8001 with `/scrub` and `/scrub/batch` endpoints
- **Secrets injection**: Extracted locally by chapter-1.sh, copied into container via `docker cp`
- **Storage**: Named Docker volume `scrubber-secrets` (persists across reboots)
- **Caller**: Memory Lane ingest endpoint calls `SCRUBBER_URL=http://scrubber:8001`
- **Security**: Container runs read-only, non-root user, no-new-privileges

## Key Files
- Scrubber code: `/home/magent/workspace/magenta/scrubber/main.py`
- Scrubber Dockerfile: `/home/magent/workspace/magenta/scrubber/Dockerfile`
- Ingest view calling scrubber: `/home/magent/workspace/magenta/conversations/views.py`
- Secrets extraction: `/home/magent/workspace/maybelle-config/maybelle/scripts/maybelle-chapter-1.sh` (lines 66-78)
- Docker compose with scrubber: `/home/magent/workspace/maybelle-config/maybelle/ansible/maybelle.yml`

## Deploy Flow
1. chapter-1.sh extracts secrets from vault locally, copies JSON to `/tmp/scrubber-secrets.json` on maybelle
2. Ansible starts scrubber container with named volume at `/app/secrets`
3. Ansible does `docker cp` to inject secrets into container
4. Ansible chowns to scrubber user, removes temp file, restarts container
5. Scrubber loads 17 secrets on startup

## Debugging Commands
```bash
docker logs scrubber | tail -20           # Check startup and requests
docker exec -u root scrubber cat /app/secrets/secrets.json | head -c 100  # Verify secrets loaded
docker logs memory-lane | grep -i scrub   # Check Memory Lane calling scrubber
```
