# Delivery Kid

IPFS pinning server - same service as maybelle, on a dedicated VPS.

The pinning service code lives in `maybelle/pinning-service/`. This directory
contains the ansible playbook to deploy it to Hetzner with Storage Box storage.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Hetzner CX22 VPS                            │
│                     (~€3.29/month)                              │
│                                                                 │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐ │
│  │ Pinning Service │  │   IPFS Daemon   │  │     aria2       │ │
│  │   (port 3001)   │  │  (5001 / 8080)  │  │   (6800/6881)   │ │
│  └────────┬────────┘  └────────┬────────┘  └─────────────────┘ │
│           │                    │                                │
│           └────────┬───────────┘                                │
│                    │                                            │
│  ┌─────────────────▼─────────────────┐                         │
│  │         40GB Local NVMe           │                         │
│  │  - IPFS metadata (badger/leveldb) │                         │
│  │  - DHT routing table              │                         │
│  │  - OS and applications            │                         │
│  └───────────────────────────────────┘                         │
│                    │                                            │
│                    │ symlink: .ipfs/blocks -> /mnt/storage-box │
│                    │                                            │
└────────────────────┼────────────────────────────────────────────┘
                     │
                     │ SMB/CIFS mount
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│               Hetzner Storage Box BX11                          │
│                   (~€3.20/month)                                │
│                                                                 │
│  ┌───────────────────────────────────┐                         │
│  │           1TB HDD Storage         │                         │
│  │     - IPFS blocks (flatfs)        │                         │
│  │     - The actual pinned content   │                         │
│  └───────────────────────────────────┘                         │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘

Total: ~€6.50/month (~$7) for 1TB of IPFS storage + BitTorrent seeding
```

## Why This Split?

IPFS stores two kinds of data:
- **Metadata** (DHT, peer info, datastore indexes) - small, latency-sensitive
- **Blocks** (actual content) - large, written once, read sequentially

By keeping metadata on fast local NVMe and blocks on cheap network storage,
we get the best of both worlds: responsive peer discovery with massive storage capacity.

## Setup

### 1. Provision Infrastructure

**Hetzner VPS (CX22):**
- Ubuntu 24.04
- Add your SSH key

**Hetzner Storage Box (BX11):**
- Enable SMB/CIFS in Robot console (Settings → SMB → Enable)
- Note the hostname: `uXXXXXX.your-storagebox.de`

**DNS:**
- Point `delivery.cryptograss.live` to VPS IP
- Point `ipfs.cryptograss.live` to VPS IP

### 2. Configure Inventory

```bash
cd ansible
cp inventory.yml.example inventory.yml
# Edit inventory.yml with your values
```

Add Storage Box password to `secrets/vault.yml`:
```yaml
storage_box_password: "your-storage-box-password"
```

### 3. Deploy

```bash
ansible-playbook -i inventory.yml playbook.yml
```

## Endpoints

| Endpoint | Description |
|----------|-------------|
| `POST /api/pin` | Pin an existing CID |
| `POST /api/upload` | Upload and pin a file |
| `POST /api/upload-directory` | Upload and pin a directory (tar) |
| `POST /api/torrent` | Add a torrent for seeding |
| `GET /api/pins` | List all pinned content |
| `GET /api/health` | Health check |

## Related

- **[delivery-driver](https://github.com/magent-cryptograss/delivery-driver)** - CLI tool for creating releases (runs on your laptop)
- **maybelle** - CI server that deploys this
