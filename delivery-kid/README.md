# delivery-kid

cryptograss's seedbox: it takes uploads, encodes video, pins Releases to IPFS
and seeds them over BitTorrent. For people, the page is
[Cryptograss:Delivery-kid](https://pickipedia.xyz/wiki/Cryptograss:Delivery-kid).
This README is the canonical description of what runs on the box and how;
`ansible/playbook.yml` is the deploy logic that makes it so.

Checked against this branch (`re/seeder-container`, off `production` at
`9a624c47`) at Ethereum block 26,371,678. The seeder's own container is new in
this branch: until it's deployed, production still seeds from inside the
pinning service's process.

## The box

- **Hetzner CX22**: 2 vCPUs, **4 GB RAM**, 40 GB local NVMe, Ubuntu 24.04. No swap.
- **Hetzner Storage Box BX11**: 1 TB, mounted over SMB/CIFS at `/mnt/storage-box`.
  Slow, cheap, big. Holds IPFS blocks, uploads, encodes and seeded files.
- Names: `delivery-kid.cryptograss.live` (the API), `ipfs.delivery-kid.cryptograss.live`
  (the gateway), `ipfs-admin.delivery-kid.cryptograss.live` (kubo's WebUI, behind a password).

## What runs on it

Three containers from one compose file (`/opt/pinning-service/docker-compose.yml`,
written by the playbook), plus Caddy on the host.

| Container | Image | What it does | Ports | Storage |
|---|---|---|---|---|
| `pinning-service` | built from `pinning-service/` | The web API (FastAPI on uvicorn): uploads, drafts, finalize, transcoding (ffmpeg runs as child processes of this container), IPFS adds, Pinata backup, `.torrent` generation, waveform peaks | `127.0.0.1:3001` | `/mnt/storage-box/staging` → `/staging` |
| `seeder` | the same image, `python -m app.seeder_main` | BitTorrent seeding with libtorrent: every torrent under `/staging/seeding`, plus new ones the pinning service hands over. **Memory limit 768 MB.** | `6881` TCP+UDP, public | `/mnt/storage-box/staging` → `/staging` |
| `ipfs` | `ipfs/kubo:latest` | The IPFS node: blocks, DHT, gateway | `127.0.0.1:5001` (API), `127.0.0.1:8080` (gateway), `4001` TCP+UDP public (swarm) | metadata on NVMe at `/var/lib/ipfs`; blocks on the Storage Box at `/mnt/storage-box/ipfs/blocks`; `/mnt/storage-box/staging` → `/export` |

All three are on the `delivery-kid-net` Docker network and `restart: unless-stopped`.

**Caddy** (systemd, on the host, installed from Caddy's GitHub release) holds the
TLS names and routes:

- `delivery-kid.cryptograss.live`: the API paths go to `localhost:3001`;
  `/webseed/{cid}/{name}/{path}` is rewritten to `/ipfs/{cid}/{path}` on the
  gateway (BEP 19 webseeds, so a torrent client can fetch from IPFS too);
  `/audit-latest.json` is served from `/var/lib/delivery-kid-audit` (written
  by maybelle's audit cron, read by `Special:DeliveryKidAudit`).
- `ipfs.delivery-kid.cryptograss.live`: the gateway, `localhost:8080`.
- `ipfs-admin.delivery-kid.cryptograss.live`: kubo's API at `localhost:5001`, behind basic auth.

### Why the seeder is its own container

Until block 26,371,678 the libtorrent session lived inside the pinning service's
uvicorn process (`init_seeder` in `main.py`, since commit `b6979d78`, 15 Mar
2026). On 8 Nov the kernel killed uvicorn for running out of memory, holding
2.7 GB, while it encoded Skyler's upload. The finalize died with it, and so did
all the seeding. Apart, a libtorrent problem can kill only the seeder, a web
deploy or crash doesn't stop the seeding, and the web process never loads
libtorrent at all (a test, `test_web_service_never_loads_libtorrent`, holds it
to that).

## The seeding folder

The pinning service and the seeder share nothing but this folder, on the
Storage Box: `/mnt/storage-box/staging/seeding` (`/staging/seeding` inside both
containers).

```
seeding/
  <cid>/torrent.dat        a seeded torrent's metainfo
  <cid>/data/...           its files, laid out as libtorrent expects
  .incoming/<cid>.<n>/     handed over by the pinning service, not yet taken
  .incoming/.tmp-<n>/      still being written; ignored
  .incoming/.failed-...    the seeder couldn't take it; kept for a person to look at
  .by-infohash/<infohash>  the CID seeding that infohash
  .status.json             what the seeder is doing, rewritten every 30 s
```

- **Handing over** (`app/services/seeding_store.py`): the pinning service moves
  the files and writes the `.torrent` into `.incoming/.tmp-<n>/`, then renames
  it to `.incoming/<cid>.<n>/` in one step.
- **Taking** (`app/services/seeder.py`, `app/seeder_main.py`): the seeder looks
  in `.incoming` every 5 seconds (the Storage Box is CIFS, where inotify sees
  nothing), lays the files out, replaces any older torrent for the same CID,
  indexes it in `.by-infohash`, starts seeding, and removes the entry.
- **Only the seeder** writes `<cid>/`, `.by-infohash` and `.status.json`. The
  pinning service reads them to serve `GET /torrent/{infohash}.torrent` and
  `GET /torrent/status`.
- Dot-names stay out of `ls`, which `maybelle/scripts/audit-storage.py` reads as
  the list of seeded CIDs.
- A torrent is served a few seconds after it's handed over, not at once: until
  the seeder takes it, `GET /torrent/{infohash}.torrent` answers 404.

## How a Release gets made and seeded

1. **Upload.** From a ReleaseDraft page on PickiPedia, or from a Mood in magenta
   (the browser sends the bytes straight here). Files land in
   `/staging/drafts/<draft-id>/`.
2. **Finalize.** Someone with PickiPedia's `finalize-release` right presses
   Finalize. The pinning service encodes video to AV1 + Opus HLS with ffmpeg,
   adds the result to IPFS, backs it up to Pinata, and marks the ReleaseDraft
   finalized. A finalize runs as a background job: leaving the page doesn't stop
   it, but a restart of the pinning service does, and the draft says so.
3. **The Release page.** The Blue Railroad Imports job on maybelle's Jenkins
   (`maybelle/jobs/pickipedia-import-bluerailroad.groovy`, every 2 minutes)
   creates `Release:<cid>`.
4. **The torrent.** The same job's "Enrich torrents" stage asks PickiPedia for
   Releases with no `bittorrent_infohash` (`api.php?action=releaselist&filter=missing-torrent`)
   and calls `POST /enrich/torrent` for each. The pinning service fetches the
   CID from kubo (streamed to disk), builds a deterministic torrent (same files,
   same infohash), hands it to the seeder, and returns the infohash, trackers,
   webseeds and `.torrent` URL. The job writes those to the Release page.
   `maybelle/scripts/reseed-cid.py` calls the same endpoint by hand.

## Memory

4 GB has to hold kubo, the pinning service, any ffmpeg encodes (1080p AV1 with
SVT-AV1 can take a GB or more), the seeder (capped at 768 MB) and the OS. Only
the seeder has a limit. When the box runs out, the kernel kills whichever
process is biggest, and that has been uvicorn. Until 8 Nov, `/enrich/torrent`
read a Release's whole IPFS archive into uvicorn's memory: a 1.4 GB Release
meant 1.4 GB or more held in the web process, and the Jenkins job asks again
every 2 minutes for as long as a Release has no torrent.

## Endpoints

Routed by Caddy to the pinning service. The code is the reference: `pinning-service/app/routes/`.

| Path | What |
|---|---|
| `GET /health`, `/version`, `/time` | Health (with IPFS reachability), build commit, server clock |
| `POST /draft-album`, `GET`/`DELETE /draft-album/{id}`, `POST /draft-album/{id}/finalize` | Album drafts |
| `POST /draft-content/init`, `POST /draft-content`, `POST /draft-content/from-url`, `GET`/`DELETE /draft-content/{id}`, `POST /draft-content/{id}/finalize` | Video and other single-item drafts |
| `GET /staging/drafts/{id}/{filename}` | A draft's staged file |
| `POST /pin/{cid}`, `DELETE /unpin/{cid}`, `GET /local-pins`, `GET /album-tracks/{cid}` | Pins |
| `POST /enrich/torrent` | Build a Release's torrent and hand it to the seeder (API key) |
| `GET /torrent/{infohash}.torrent` | A seeded torrent's metainfo |
| `GET /torrent/status` | The seeder's status; `running: false` when its status file is over 2 minutes old |
| `GET /peaks/{cid}.json` | Waveform peaks for a pinned audio file |

## Watching it

- **Uptime**: `maybelle/jobs/delivery-kid-uptime.groovy` runs
  `scripts/test-delivery-kid.py` every 5 minutes: health, version, clock drift,
  the seeder, and a known CID through the gateway. It alerts after 2 failures in a row.
- **Storage audit**: `maybelle/scripts/audit-storage.py`, every 30 minutes,
  cross-checks pins, seeding folders and drafts against PickiPedia. Live at
  [Special:DeliveryKidAudit](https://pickipedia.xyz/wiki/Special:DeliveryKidAudit).
- **Draft diagnostics**: each draft's log is copied to
  `ReleaseDraft:<id>/diagnostics` on PickiPedia when it finishes or fails.

## Deploying

By pull request to `production`, then a redeploy of delivery-kid from maybelle
(`maybelle/scripts/deploy-delivery-kid.sh`). Nothing changes on the box by hand.

## Setting up a new box

1. Provision a CX22 (Ubuntu 24.04, an SSH key) and a BX11 Storage Box with SMB enabled.
2. Point `delivery-kid.cryptograss.live`, `ipfs.delivery-kid.cryptograss.live` and
   `ipfs-admin.delivery-kid.cryptograss.live` at the VPS.
3. `cp ansible/inventory.yml.example ansible/inventory.yml` and fill it in. The
   Storage Box password and the API keys come from `secrets/vault.yml`, where a
   person puts them.
4. Deploy as above.
