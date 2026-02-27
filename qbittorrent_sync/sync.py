"""Core sync engine: fetch torrents, compute diffs, apply changes."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import qbittorrentapi
from rich.console import Console
from rich.table import Table

from qbittorrent_sync.config import AppConfig, InstanceConfig

log = logging.getLogger("qbt-sync")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class TorrentEntry:
    """Lightweight snapshot of a torrent's sync-relevant properties."""

    hash: str
    name: str
    save_path: str
    category: str
    tags: list[str]
    content_path: str
    download_path: str = ""
    file_priorities: list[int] | None = None


@dataclass
class SyncDiff:
    """Computed diff between master and a single child."""

    child_name: str
    to_delete: list[TorrentEntry] = field(default_factory=list)
    to_add: list[TorrentEntry] = field(default_factory=list)
    to_relocate: list[tuple[TorrentEntry, TorrentEntry]] = field(default_factory=list)
    to_sync_files: list[TorrentEntry] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return (
            not self.to_delete
            and not self.to_add
            and not self.to_relocate
            and not self.to_sync_files
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _connect(instance: InstanceConfig) -> qbittorrentapi.Client:
    client = qbittorrentapi.Client(
        host=instance.host,
        username=instance.username,
        password=instance.password,
    )
    client.auth_log_in()
    log.debug("Connected to %s (%s)", instance.name, instance.host)
    return client


def _torrent_to_entry(t: qbittorrentapi.TorrentDictionary) -> TorrentEntry:
    raw_tags = t.get("tags", "") or ""
    tags = [tag.strip() for tag in raw_tags.split(",") if tag.strip()] if raw_tags else []
    return TorrentEntry(
        hash=t["hash"],
        name=t.get("name", ""),
        save_path=t.get("save_path", ""),
        category=t.get("category", ""),
        tags=tags,
        content_path=t.get("content_path", ""),
        download_path=t.get("download_path", ""),
    )


def _fetch_master_torrents(
    client: qbittorrentapi.Client,
    min_seeding_seconds: int,
) -> dict[str, TorrentEntry]:
    """Return eligible master torrents keyed by info-hash."""
    torrents = client.torrents_info()
    result: dict[str, TorrentEntry] = {}
    for t in torrents:
        state = (t.get("state") or "").lower()
        is_completed = state in {
            "uploading", "stalledup", "forcedup",
            "pausedup", "queuedup", "checkingup",
            "seeding", "completed",
        }
        # Also accept explicit progress == 1.0 as completed
        if not is_completed and t.get("progress", 0) < 1.0:
            continue

        seeding_time = t.get("seeding_time", 0) or 0
        if seeding_time < min_seeding_seconds:
            log.debug(
                "Skipping %s (seeding %ds < %ds)",
                t.get("name", t["hash"]),
                seeding_time,
                min_seeding_seconds,
            )
            continue

        result[t["hash"]] = _torrent_to_entry(t)

    for h, entry in result.items():
        try:
            files = client.torrents_files(torrent_hash=h)
            entry.file_priorities = [f.priority for f in files]
        except Exception:
            log.warning("Failed to fetch file priorities for %s", entry.name)

    deselected_count = sum(
        1 for e in result.values()
        if e.file_priorities and any(p == 0 for p in e.file_priorities)
    )
    if deselected_count:
        log.info("%d torrent(s) have deselected files on master", deselected_count)

    return result


def _fetch_child_torrents(
    client: qbittorrentapi.Client,
) -> dict[str, TorrentEntry]:
    """Return all torrents on a child instance keyed by info-hash."""
    return {t["hash"]: _torrent_to_entry(t) for t in client.torrents_info()}


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

def compute_diff(
    master: dict[str, TorrentEntry],
    child: dict[str, TorrentEntry],
    child_name: str,
) -> SyncDiff:
    diff = SyncDiff(child_name=child_name)

    master_hashes = set(master)
    child_hashes = set(child)

    for h in child_hashes - master_hashes:
        diff.to_delete.append(child[h])

    for h in master_hashes - child_hashes:
        diff.to_add.append(master[h])

    for h in master_hashes & child_hashes:
        if master[h].save_path != child[h].save_path:
            diff.to_relocate.append((master[h], child[h]))
        if master[h].file_priorities and any(p == 0 for p in master[h].file_priorities):
            diff.to_sync_files.append(master[h])

    return diff


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def _apply_deletes(
    child_client: qbittorrentapi.Client,
    entries: list[TorrentEntry],
) -> int:
    if not entries:
        return 0
    hashes = [e.hash for e in entries]
    child_client.torrents_delete(delete_files=False, torrent_hashes=hashes)
    for e in entries:
        log.info("Deleted torrent: %s", e.name)
    return len(entries)


def _apply_adds(
    master_client: qbittorrentapi.Client,
    child_client: qbittorrentapi.Client,
    entries: list[TorrentEntry],
    skip_hash_check: bool,
) -> int:
    added = 0
    for entry in entries:
        try:
            torrent_bytes = master_client.torrents_export(torrent_hash=entry.hash)
        except Exception:
            log.warning("Failed to export .torrent for %s — skipping", entry.name)
            continue

        has_deselected = entry.file_priorities and any(
            p == 0 for p in entry.file_priorities
        )

        add_kwargs: dict = dict(
            torrent_files=torrent_bytes,
            save_path=entry.save_path,
            category=entry.category,
            tags=entry.tags if entry.tags else None,
            is_skip_checking=skip_hash_check,
            use_auto_torrent_management=False,
            is_paused=has_deselected,
        )
        if entry.download_path:
            add_kwargs["download_path"] = entry.download_path

        try:
            child_client.torrents_add(**add_kwargs)
            if entry.download_path:
                log.info(
                    "Added torrent: %s → %s (temp: %s)",
                    entry.name, entry.save_path, entry.download_path,
                )
            else:
                log.info("Added torrent: %s → %s", entry.name, entry.save_path)
            added += 1
        except qbittorrentapi.Conflict409Error:
            log.debug("Torrent already exists on child: %s", entry.name)
            continue
        except Exception:
            log.warning("Failed to add torrent %s — skipping", entry.name, exc_info=True)
            continue

        if has_deselected:
            deselected_ids = [
                i for i, p in enumerate(entry.file_priorities) if p == 0
            ]
            time.sleep(1)
            try:
                child_client.torrents_file_priority(
                    torrent_hash=entry.hash,
                    file_ids=deselected_ids,
                    priority=0,
                )
                log.debug(
                    "Deselected %d file(s) for %s", len(deselected_ids), entry.name
                )
            except Exception:
                log.warning(
                    "Failed to set file priorities for %s", entry.name, exc_info=True
                )
            child_client.torrents_resume(torrent_hashes=entry.hash)

    return added


def _apply_relocates(
    child_client: qbittorrentapi.Client,
    entries: list[tuple[TorrentEntry, TorrentEntry]],
) -> int:
    relocated = 0
    for master_entry, _child_entry in entries:
        h = master_entry.hash
        try:
            child_client.torrents_pause(torrent_hashes=h)
            child_client.torrents_set_save_path(
                save_path=master_entry.save_path,
                torrent_hashes=h,
            )
            child_client.torrents_resume(torrent_hashes=h)
            log.info(
                "Relocated torrent: %s → %s",
                master_entry.name,
                master_entry.save_path,
            )
            relocated += 1
        except Exception:
            log.warning("Failed to relocate torrent %s — skipping", master_entry.name, exc_info=True)
    return relocated


def _apply_file_priority_sync(
    child_client: qbittorrentapi.Client,
    entries: list[TorrentEntry],
) -> int:
    """Deselect files on child that master has deselected."""
    synced = 0
    for master_entry in entries:
        if not master_entry.file_priorities:
            continue

        try:
            child_files = child_client.torrents_files(torrent_hash=master_entry.hash)
        except Exception:
            log.warning(
                "Failed to fetch files for %s on child — skipping", master_entry.name
            )
            continue

        ids_to_deselect = [
            i
            for i, mp in enumerate(master_entry.file_priorities)
            if mp == 0 and i < len(child_files) and child_files[i].priority != 0
        ]

        if not ids_to_deselect:
            continue

        try:
            child_client.torrents_file_priority(
                torrent_hash=master_entry.hash,
                file_ids=ids_to_deselect,
                priority=0,
            )
            log.info(
                "Deselected %d file(s) for %s",
                len(ids_to_deselect),
                master_entry.name,
            )
            synced += 1
        except Exception:
            log.warning(
                "Failed to update file priorities for %s — skipping",
                master_entry.name,
                exc_info=True,
            )
    return synced


# ---------------------------------------------------------------------------
# Summary output
# ---------------------------------------------------------------------------

def _print_diff_table(diff: SyncDiff, console: Console, dry_run: bool) -> None:
    label = "[bold yellow][DRY RUN][/] " if dry_run else ""
    title = f"{label}Sync summary for [bold cyan]{diff.child_name}[/]"

    table = Table(title=title, show_lines=True)
    table.add_column("Action", style="bold")
    table.add_column("Count", justify="right")
    table.add_column("Details")

    if diff.to_delete:
        names = "\n".join(e.name for e in diff.to_delete[:10])
        if len(diff.to_delete) > 10:
            names += f"\n… and {len(diff.to_delete) - 10} more"
        table.add_row("[red]Delete[/]", str(len(diff.to_delete)), names)

    if diff.to_add:
        names = "\n".join(e.name for e in diff.to_add[:10])
        if len(diff.to_add) > 10:
            names += f"\n… and {len(diff.to_add) - 10} more"
        table.add_row("[green]Add[/]", str(len(diff.to_add)), names)

    if diff.to_relocate:
        details: list[str] = []
        for master_e, child_e in diff.to_relocate[:10]:
            details.append(f"{master_e.name}: {child_e.save_path} → {master_e.save_path}")
        if len(diff.to_relocate) > 10:
            details.append(f"… and {len(diff.to_relocate) - 10} more")
        table.add_row("[yellow]Relocate[/]", str(len(diff.to_relocate)), "\n".join(details))

    if diff.to_sync_files:
        names = "\n".join(e.name for e in diff.to_sync_files[:10])
        if len(diff.to_sync_files) > 10:
            names += f"\n… and {len(diff.to_sync_files) - 10} more"
        table.add_row("[magenta]File selection[/]", str(len(diff.to_sync_files)), names)

    if diff.is_empty:
        table.add_row("[dim]—[/]", "0", "Already in sync")

    console.print(table)


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def run_sync(cfg: AppConfig, *, dry_run: bool, console: Console) -> None:
    """Execute a full sync cycle."""
    min_seed_secs = cfg.sync.min_seeding_time_minutes * 60

    # --- master ---
    console.print(f"\nConnecting to master [bold]{cfg.master.host}[/] …")
    try:
        master_client = _connect(cfg.master)
    except Exception:
        log.exception("Cannot connect to master at %s", cfg.master.host)
        raise

    master_torrents = _fetch_master_torrents(master_client, min_seed_secs)
    console.print(f"  Found [bold]{len(master_torrents)}[/] eligible torrent(s) on master.\n")

    # --- children ---
    for child_cfg in cfg.children:
        console.rule(f"[bold]{child_cfg.name}[/] — {child_cfg.host}")
        try:
            child_client = _connect(child_cfg)
        except Exception:
            log.error("Cannot connect to child %s at %s — skipping", child_cfg.name, child_cfg.host)
            continue

        child_torrents = _fetch_child_torrents(child_client)
        log.debug("Child %s has %d torrent(s)", child_cfg.name, len(child_torrents))

        diff = compute_diff(master_torrents, child_torrents, child_cfg.name)
        _print_diff_table(diff, console, dry_run)

        if dry_run or diff.is_empty:
            continue

        deleted = _apply_deletes(child_client, diff.to_delete)
        added = _apply_adds(master_client, child_client, diff.to_add, cfg.sync.skip_hash_check)
        relocated = _apply_relocates(child_client, diff.to_relocate)
        file_synced = _apply_file_priority_sync(child_client, diff.to_sync_files)

        console.print(
            f"\n  [bold green]Done:[/] {deleted} deleted, {added} added,"
            f" {relocated} relocated, {file_synced} file-selection synced.\n"
        )
