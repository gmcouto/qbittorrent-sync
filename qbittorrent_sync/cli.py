"""CLI entrypoint using click."""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import asdict
from datetime import datetime, timedelta

import click
from rich.console import Console
from rich.logging import RichHandler

from qbittorrent_sync.config import ConfigError, SyncConfig, load_config
from qbittorrent_sync.sync import run_sync

console = Console()


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
    )


@click.command()
@click.option(
    "-c",
    "--config",
    "config_path",
    default="config.yaml",
    type=click.Path(),
    help="Path to YAML config file.",
    show_default=True,
)
@click.option(
    "--dry-run/--no-dry-run",
    default=None,
    help="Preview changes without applying (default: true). Use --no-dry-run to apply.",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    default=False,
    help="Enable verbose (debug) logging.",
)
@click.option(
    "--daemon",
    is_flag=True,
    default=False,
    help="Keep running, repeating the sync every N minutes (see daemon_run_interval_minutes).",
)
def main(config_path: str, dry_run: bool, verbose: bool, daemon: bool) -> None:
    """Synchronize torrents from a master qBittorrent instance to children."""
    _setup_logging(verbose)
    log = logging.getLogger("qbt-sync")

    try:
        cfg = load_config(config_path)
    except ConfigError as exc:
        console.print(f"[bold red]Configuration error:[/] {exc}")
        sys.exit(1)

    effective_dry_run = cfg.sync.dry_run if dry_run is None else dry_run
    log.debug("Loaded config: master=%s, children=%d, dry_run=%s", cfg.master.host, len(cfg.children), effective_dry_run)

    try:
        if daemon:
            _run_daemon(config_path, dry_run_override=dry_run, log=log)
        else:
            run_sync(cfg, dry_run=effective_dry_run, console=console)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/]")
        sys.exit(130)
    except Exception:
        log.exception("Unexpected error during sync")
        sys.exit(1)


def _run_daemon(config_path: str, *, dry_run_override: bool | None, log: logging.Logger) -> None:
    console.print("\n[bold]Daemon mode:[/] Press Ctrl+C to stop.\n")
    prev_sync: SyncConfig | None = None

    while True:
        try:
            cfg = load_config(config_path)
        except ConfigError as exc:
            log.error("Failed to reload config: %s — will retry next cycle", exc)
            time.sleep(60)
            continue

        if prev_sync is not None:
            _log_sync_changes(prev_sync, cfg.sync, log)
        prev_sync = cfg.sync

        dry_run = cfg.sync.dry_run if dry_run_override is None else dry_run_override
        interval = cfg.sync.daemon_run_interval_minutes

        start = time.monotonic()
        console.rule(f"[bold]Sync started at {datetime.now():%Y-%m-%d %H:%M:%S}[/]")

        try:
            run_sync(cfg, dry_run=dry_run, console=console)
        except KeyboardInterrupt:
            raise
        except Exception:
            log.exception("Sync failed — will retry next cycle")

        elapsed = time.monotonic() - start
        next_run = datetime.now() + timedelta(minutes=interval)
        console.print(
            f"\n[dim]Sync completed in {elapsed:.1f}s. "
            f"Next run at {next_run:%H:%M:%S} ({interval}m interval).[/]\n"
        )

        time.sleep(interval * 60)


def _log_sync_changes(old: SyncConfig, new: SyncConfig, log: logging.Logger) -> None:
    old_d, new_d = asdict(old), asdict(new)
    changes = {k: (old_d[k], new_d[k]) for k in old_d if old_d[k] != new_d[k]}
    if changes:
        log.info("Config reloaded — sync options changed:")
        for key, (prev, curr) in changes.items():
            log.info("  %s: %s → %s", key, prev, curr)
    else:
        log.debug("Config reloaded — no sync option changes.")
