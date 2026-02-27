"""CLI entrypoint using click."""

from __future__ import annotations

import logging
import sys

import click
from rich.console import Console
from rich.logging import RichHandler

from qbittorrent_sync.config import ConfigError, load_config
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
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show what would be done without making changes.",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    default=False,
    help="Enable verbose (debug) logging.",
)
def main(config_path: str, dry_run: bool, verbose: bool) -> None:
    """Synchronize torrents from a master qBittorrent instance to children."""
    _setup_logging(verbose)
    log = logging.getLogger("qbt-sync")

    try:
        cfg = load_config(config_path)
    except ConfigError as exc:
        console.print(f"[bold red]Configuration error:[/] {exc}")
        sys.exit(1)

    log.debug("Loaded config: master=%s, children=%d", cfg.master.host, len(cfg.children))

    try:
        run_sync(cfg, dry_run=dry_run, console=console)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/]")
        sys.exit(130)
    except Exception:
        log.exception("Unexpected error during sync")
        sys.exit(1)
