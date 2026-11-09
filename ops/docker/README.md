# Running the refresh in Docker

The image is only the toolchain: Python 3.14, uv, 7-Zip, innoextract (built
from master for Inno Setup 6.1–6.4), rclone, and Camoufox's Firefox for
ASRock. The code comes from the git checkout that compose mounts at `/app`,
so updating the host is a `git pull`. Rebuild (`docker compose build`) only
when `uv.lock` or the Dockerfile changes.

## Storage

| Path | Size (Nov 2026) | Where |
|---|---|---|
| `data/index.sqlite`, `data/raw`, `data/manual`, `public/` | ~3.5 GB, slow growth | local disk: SQLite must not sit on NFS/SMB |
| `data/tmp` (extraction scratch) | up to ~10 GB during a run | local disk |
| `data/payloads` | 211 GB unpruned; small with pruning | local disk or NAS (`WINIDX_PAYLOADS`) |
| image | ~4 GB (mostly Firefox and its libraries) | Docker's disk |

`refresh.sh` runs `winidx prune --keep-days 30` after publish: payloads that
are extracted and more than 30 days old are deleted, and fetch treats an
extracted payload as held, so it doesn't download it again. The cost is
re-extraction. When extraction improves, payloads older than the grace
period must be downloaded again. Set `WINIDX_KEEP_DAYS=` (empty) to keep
everything.

The payload store is written once and read once per payload, which a NAS
handles fine. Mount the share on the host, then point compose at it.

## Host setup

```sh
git clone https://github.com/dtucny/WinDriverIndex.git ~/git/WinDriverIndex
cd ~/git/WinDriverIndex
cat > .env <<'END'                # read by compose; gitignored
WINIDX_PAYLOADS=/mnt/nas/winidx-payloads
# WINIDX_KEEP_DAYS=30            # default; empty keeps every payload
# WINIDX_UID=1000                # must own the checkout and data/
END
install -m600 /dev/null ~/.config/winidx/r2.env   # then fill in R2_* (see README)
docker compose build
```

To move an existing install, stop its timer and copy across. The database
needs a consistent copy, so take it with `sqlite3 data/index.sqlite
".backup /path/index.sqlite"` rather than copying the file while WAL is in
use. Also copy `data/raw`, `data/manual`, `data/asrock_cookies.txt` and
`public/`. `public/` holds the previous water level and `changes.json`
history that the next publish diffs against. Payloads are optional: copy
what you want to keep (or `winidx prune` first). Unextracted payloads that
are missing are simply fetched again.

## Running

```sh
docker compose run --rm winidx                         # full refresh + deploy
docker compose run --rm winidx uv run winidx status    # any single command
```

Weekly, as a user unit (the user must be in the `docker` group):

```sh
mkdir -p ~/.config/systemd/user
cp ops/docker/winidx-weekly.service ops/winidx-weekly.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now winidx-weekly.timer
loginctl enable-linger $USER
```
