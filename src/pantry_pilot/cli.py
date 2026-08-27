"""PantryPilot command-line interface (Typer).

Each command is thin: open a session, call a service function, print the result.
All the rules live in the service — the CLI is just the front door.
"""

from typing import Annotated

import anthropic
import typer
from rich.console import Console
from rich.table import Table

from pantry_pilot.core.database import get_session, init_db
from pantry_pilot.models.enums import BaseUnit, Category, StockStatus, TrackingMode, TxnReason
from pantry_pilot.services.pantry import (
    add_ingredient,
    archive_ingredient,
    get_ingredient,
    list_ingredients,
    record_transaction,
    set_status,
)
from pantry_pilot.services.synthesizer import RecipeSynthesisError, synthesize_recipe_query

app = typer.Typer(help="PantryPilot — manage your pantry from the terminal.")
console = Console()


@app.callback()
def _ensure_db() -> None:
    """Create the database + tables if they don't exist yet (runs before every command)."""
    init_db()


@app.command()
def add(
    name: str,
    category: Annotated[Category, typer.Option("--category", "-c")],
    mode: Annotated[TrackingMode, typer.Option("--mode", "-m")] = TrackingMode.QUANTITY,
    unit: Annotated[BaseUnit | None, typer.Option("--unit", "-u")] = None,
    amount: Annotated[int | None, typer.Option("--amount", "-a")] = None,
    status: Annotated[StockStatus | None, typer.Option("--status", "-s")] = None,
) -> None:
    """Add a new ingredient to the pantry."""
    with get_session() as session:
        try:
            ingredient = add_ingredient(
                session,
                name=name,
                category=category,
                tracking_mode=mode,
                base_unit=unit,
                status=status,
            )
            if mode == TrackingMode.QUANTITY and amount:
                record_transaction(
                    session, ingredient, change_amount=amount, reason=TxnReason.INITIAL
                )
        except ValueError as exc:
            console.print(f"[red]Error:[/red] {exc}")
            raise typer.Exit(1) from exc
    console.print(f"[green]Added[/green] {name} ({category.value}, {mode.value}).")


@app.command(name="list")
def list_cmd(
    show_all: Annotated[bool, typer.Option("--all", help="include archived items")] = False,
    category: Annotated[Category | None, typer.Option("--category", "-c")] = None,
) -> None:
    """List everything in the pantry."""
    table = Table(title="Pantry")
    table.add_column("Name")
    table.add_column("Category")
    table.add_column("Mode")
    table.add_column("Stock")
    with get_session() as session:
        for item in list_ingredients(session, include_archived=show_all, category=category):
            if item.tracking_mode == TrackingMode.QUANTITY:
                unit = item.base_unit.value if item.base_unit else ""
                stock = f"{item.on_hand} {unit}".strip()
            else:
                stock = item.status.value if item.status else "-"
            table.add_row(item.name, item.category.value, item.tracking_mode.value, stock)
    console.print(table)


@app.command()
def restock(name: str, amount: int) -> None:
    """Add stock to a QUANTITY ingredient (records a RESTOCK transaction)."""
    _change_stock(name, amount, TxnReason.RESTOCK)


@app.command()
def use(name: str, amount: int) -> None:
    """Use stock from a QUANTITY ingredient (records a CONSUME transaction)."""
    _change_stock(name, -amount, TxnReason.CONSUME)


def _change_stock(name: str, change: int, reason: TxnReason) -> None:
    with get_session() as session:
        ingredient = get_ingredient(session, name)
        if ingredient is None:
            console.print(f"[red]No ingredient named[/red] '{name}'.")
            raise typer.Exit(1)
        try:
            record_transaction(session, ingredient, change_amount=change, reason=reason)
        except ValueError as exc:
            console.print(f"[red]Error:[/red] {exc}")
            raise typer.Exit(1) from exc
        unit = ingredient.base_unit.value if ingredient.base_unit else ""
        console.print(f"{name}: now [bold]{ingredient.on_hand} {unit}[/bold] on hand.".rstrip())


@app.command()
def status(
    name: str,
    level: Annotated[StockStatus, typer.Argument(help="out / low / ok")],
) -> None:
    """Set a PRESENCE ingredient's status."""
    with get_session() as session:
        ingredient = get_ingredient(session, name)
        if ingredient is None:
            console.print(f"[red]No ingredient named[/red] '{name}'.")
            raise typer.Exit(1)
        try:
            set_status(session, ingredient, level)
        except ValueError as exc:
            console.print(f"[red]Error:[/red] {exc}")
            raise typer.Exit(1) from exc
    console.print(f"{name}: status set to [bold]{level.value}[/bold].")


@app.command()
def remove(name: str) -> None:
    """Archive (soft-delete) an ingredient."""
    with get_session() as session:
        ingredient = get_ingredient(session, name)
        if ingredient is None:
            console.print(f"[red]No ingredient named[/red] '{name}'.")
            raise typer.Exit(1)
        archive_ingredient(session, ingredient)
    console.print(f"[yellow]Archived[/yellow] {name}.")


@app.command()
def suggest(
    goal: Annotated[Category, typer.Option("--goal", "-g", help="macro goal, e.g. protein")],
) -> None:
    """Turn your pantry + a macro goal into a recipe-search query (the Phase-2a LLM step).

    WHAT: reads your pantry, asks Claude to synthesize a structured RecipeQuery, prints it.
    WHY:  it is the one non-deterministic step in the pipeline — everything around it
          (reading the pantry, printing) is plain deterministic code. Phase 2b will feed
          the printed query to Spoonacular to fetch real, highly-rated recipes.
    """
    # Read the pantry deterministically, then close the DB session BEFORE the network
    # call — we don't want a database connection held open during a slow LLM request.
    with get_session() as session:
        ingredients = list_ingredients(session)

    # The LLM step. Any failure becomes a clean one-line message + exit code 1 (never a
    # raw traceback). The three things that can go wrong:
    #   RecipeSynthesisError - Claude refused or returned nothing usable
    #   RuntimeError         - no ANTHROPIC_API_KEY configured (raised by get_client)
    #   anthropic.APIError   - auth / rate-limit / network / server error from the API
    try:
        query = synthesize_recipe_query(ingredients, goal)
    except (RecipeSynthesisError, RuntimeError, anthropic.APIError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc

    # Show the validated query. In Phase 2b this becomes the input to the recipe fetch.
    console.print(f"[bold]Recipe query for a {goal.value} meal:[/bold]")
    console.print_json(query.model_dump_json())
