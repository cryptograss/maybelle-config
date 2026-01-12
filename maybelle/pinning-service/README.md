# Blue Railroad Pinning Service

Downloads videos from Instagram/YouTube via yt-dlp and pins them to IPFS (both Pinata cloud and local node).

## API Endpoints

### POST /pin-from-url
Download video from URL and pin to IPFS.

```bash
curl -X POST https://pinning.maybelle.cryptograss.live/pin-from-url \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{"url": "https://www.instagram.com/p/ABC123/"}'
```

Response:
```json
{
  "cid": "QmXyz...",
  "ipfsUri": "ipfs://QmXyz...",
  "gatewayUrl": "https://gateway.pinata.cloud/ipfs/QmXyz...",
  "filename": "video.mp4",
  "size": 12345678,
  "locallyPinned": true
}
```

### POST /pin-file
Upload and pin a file directly.

```bash
curl -X POST https://pinning.maybelle.cryptograss.live/pin-file \
  -H "X-API-Key: YOUR_API_KEY" \
  -F "file=@video.mp4"
```

### POST /pin-cid
Pin an existing CID to local IPFS node.

```bash
curl -X POST https://pinning.maybelle.cryptograss.live/pin-cid \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_API_KEY" \
  -d '{"cid": "QmXyz..."}'
```

### GET /health
Health check (no auth required).

## Vault Variables Required

Add these to `secrets/vault.yml`:

```yaml
# Pinata IPFS pinning service credentials
# Get from https://app.pinata.cloud/keys
pinata_api_key: "your-pinata-api-key"
pinata_secret_key: "your-pinata-secret-api-key"

# API key for authenticating requests to the pinning service
# Generate with: openssl rand -hex 32
pinning_service_api_key: "your-random-api-key"
```

## Storage

- IPFS data: `/mnt/persist/ipfs/data` (persistent across deploys)
- Staging: `/mnt/persist/ipfs/staging` (temporary file storage)

## Ports

- 3001: Pinning service API (exposed via Caddy at pinning.maybelle.cryptograss.live)
- 5001: IPFS API (localhost only)
- 4001: IPFS swarm (public, for peering with other nodes)
