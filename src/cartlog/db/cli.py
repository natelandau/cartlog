"""`cartlog db` commands: database seeding and maintenance."""

from __future__ import annotations

import typer
from rich.console import Console

from cartlog.bootstrap import prepare_runtime
from cartlog.config import get_settings

db_app = typer.Typer(help="Database maintenance commands.", no_args_is_help=True)
console = Console()


@db_app.command()
def seed() -> None:
    """Ensure the schema exists, then add any categories missing from the fixture.

    Runs the same migrate-then-seed path as startup, so it works on a brand-new database as
    well as an existing one. Both steps are idempotent and seeding never removes rows.
    """
    prepare_runtime(get_settings())
    console.print("Categories seeded from the fixture.")
