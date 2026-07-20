"""Typer CLI entry point for teamgym-watcher."""

from __future__ import annotations

import logging
from typing import Annotated

import typer

from teamgym_watcher import __version__
from teamgym_watcher.log import setup_logging

app = typer.Typer(
    name="teamgym-watcher",
    help=(
        "Watches an import directory, matches photos to TeamGym competition tags by "
        "capture time, and moves them into a Zenfolio-ready folder hierarchy."
    ),
    no_args_is_help=True,
)

logger = logging.getLogger(__name__)

NOT_IMPLEMENTED_EXIT_CODE = 1

_VerboseOption = Annotated[bool, typer.Option("--verbose", "-v", help="Enable debug logging.")]


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"teamgym-watcher {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
) -> None:
    """teamgym-watcher: sort TeamGym competition photos into a Zenfolio-ready folder hierarchy."""


def _not_implemented(command: str, verbose: bool) -> None:
    setup_logging(verbose=verbose)
    logger.error("'%s' is not implemented yet.", command)
    raise typer.Exit(code=NOT_IMPLEMENTED_EXIT_CODE)


@app.command()
def run(verbose: _VerboseOption = False) -> None:
    """Watch the import directory forever, matching and moving photos as they arrive."""
    _not_implemented("run", verbose)


@app.command("process-once")
def process_once(verbose: _VerboseOption = False) -> None:
    """Drain the import directory once and exit."""
    _not_implemented("process-once", verbose)


@app.command()
def auth(verbose: _VerboseOption = False) -> None:
    """Run the GitHub device-flow authentication against the teamgym-sorter API."""
    _not_implemented("auth", verbose)


if __name__ == "__main__":
    app()
