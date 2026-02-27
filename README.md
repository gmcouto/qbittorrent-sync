# qbittorrent-sync

Synchronize torrents from a master qBittorrent instance to one or more child instances. Handles adding, deleting, relocating torrents, and syncing file selections.

Designed for setups where multiple qBittorrent instances share the same storage (e.g. via NFS/SMB mounts).

## Install

```bash
pip3 install . --break-system-packages
```

## Configuration

Copy the example config and edit it:

```bash
cp config.example.yaml config.yaml
```

See `config.example.yaml` for all available options.

## Usage

```bash
# Preview changes (dry run, the default)
qbt-sync

# Apply changes
qbt-sync --no-dry-run

# Custom config path
qbt-sync -c /path/to/config.yaml

# Verbose output
qbt-sync -v
```

## What it does

1. Cleans up stale/errored torrents on children
2. Fetches eligible torrents from master (completed + minimum seeding time)
3. For each child, computes a diff and applies:
   - **Delete** torrents not on master
   - **Add** torrents missing on child
   - **Recategorize** torrents whose category differs from master
   - **Relocate** torrents with mismatched save paths or temp (download) paths
   - **Sync file selections** (deselected files on master get deselected on children)
