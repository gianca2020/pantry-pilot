"""PantryPilot command-line interface (Typer).

Each command is thin: open a session, call a service function, print the result.
All the rules live in the service — the CLI is just the front door.
"""

from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from pantry_pilot.core.claude_cli import ClaudeCliError
from pantry_pilot.core.database import get_session, init_db
from pantry_pilot.core.spoonacular import SpoonacularError
from pantry_pilot.models.enums import BaseUnit, Category, StockStatus, TrackingMode, TxnReason
from pantry_pilot.models.schemas import RecipeFit, TrendingQuery
from pantry_pilot.pipeline.orchestrator import make_plan
from pantry_pilot.services.pantry import (
    add_ingredient,
    archive_ingredient,
    get_ingredient,
    list_ingredients,
    record_transaction,
    set_status,
)
from pantry_pilot.services.resolver import (
    ResolutionError,
    _shopping_list,
    _uncertain,
    cook,
    rank_recipes,
)
from pantry_pilot.services.synthesizer import RecipeSynthesisError, synthesize_recipe_query
from pantry_pilot.services.trending import TrendingRecipeError, find_trending

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
def suggest() -> None:
    """Turn your pantry into a recipe-search query (the Phase-2a LLM step).

    WHAT: reads your pantry, asks Claude to synthesize a structured RecipeQuery, prints it.
    WHY:  it is the one non-deterministic step in the pipeline — everything around it
          (reading the pantry, printing) is plain deterministic code. Phase 2b will feed
          the printed query to Spoonacular to fetch real, highly-rated recipes.
    """
    # Read the pantry deterministically, then close the DB session BEFORE the LLM call —
    # we don't want a database connection held open during a slow subprocess request.
    with get_session() as session:
        ingredients = list_ingredients(session)

    # The LLM step. Any failure becomes a clean one-line message + exit code 1 (never a
    # raw traceback). What can go wrong:
    #   RecipeSynthesisError - Claude returned nothing usable / schema-invalid
    #   ClaudeCliError       - claude not installed / not logged in / timeout / quota / bad output
    try:
        query = synthesize_recipe_query(ingredients)
    except (RecipeSynthesisError, ClaudeCliError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc

    # Show the validated query. In Phase 2b this becomes the input to the recipe fetch.
    console.print("[bold]Recipe query from your pantry:[/bold]")
    console.print_json(query.model_dump_json())


@app.command()
def trending(
    theme: Annotated[
        str | None, typer.Option("--theme", "-t", help="what to look for, e.g. 'chicken dinner'")
    ] = None,
    cuisine: Annotated[str | None, typer.Option("--cuisine", "-c", help="e.g. 'thai'")] = None,
    meal: Annotated[str | None, typer.Option("--meal", "-m", help="e.g. 'dinner'")] = None,
    max_minutes: Annotated[
        int | None, typer.Option("--max-minutes", help="cap on total cook time")
    ] = None,
) -> None:
    """Find recipes trending on the live web right now (Phase-2c, source #2).

    WHAT: builds a TrendingQuery from your options, asks Claude (web-enabled) to find
          currently-popular recipes on vetted free sites, and prints the validated results.
    WHY:  the agentic web search is the one non-deterministic step; the allow-list filter
          and schema validation around it are deterministic. This can take a minute or two.
    """
    query = TrendingQuery(theme=theme, cuisine=cuisine, meal_type=meal, max_minutes=max_minutes)

    console.print("[dim]Searching the live web for what's trending… (may take a minute)[/dim]")
    # The web-enabled LLM step. Any failure becomes a clean one-line message + exit 1:
    #   TrendingRecipeError - the model returned nothing usable / schema-invalid
    #   ClaudeCliError      - claude not installed / not logged in / timeout / quota / bad output
    try:
        recipes = find_trending(query)
    except (TrendingRecipeError, ClaudeCliError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc

    if not recipes:  # empty is a valid outcome, not an error
        console.print("[yellow]Nothing trending found[/yellow] — try a broader theme.")
        return

    table = Table(title="Trending recipes")
    table.add_column("Title")
    table.add_column("Ready", justify="right")
    table.add_column("Steps", justify="right")
    table.add_column("Source")
    for r in recipes:
        ready = f"{r.ready_minutes} min" if r.ready_minutes else "-"
        table.add_row(r.title, ready, str(len(r.steps or [])), r.source_url or "-")
    console.print(table)


@app.command(name="cook-ideas")
def cook_ideas(
    theme: Annotated[
        str | None, typer.Option("--theme", "-t", help="what to look for, e.g. 'chicken dinner'")
    ] = None,
    cuisine: Annotated[str | None, typer.Option("--cuisine", "-c", help="e.g. 'thai'")] = None,
    meal: Annotated[str | None, typer.Option("--meal", "-m", help="e.g. 'dinner'")] = None,
    max_minutes: Annotated[
        int | None, typer.Option("--max-minutes", help="cap on total cook time")
    ] = None,
) -> None:
    """Rank what's trending by what you can cook tonight from your pantry (Phase-3 resolver).

    WHAT: reads your pantry, finds trending recipes (source #2), asks Claude to match each
          ingredient line to a pantry item, then ranks by fewest-missing and prints a shopping
          list. Optionally 'cook' one to adjust the pantry.
    WHY:  the per-recipe match is the one non-deterministic step; the stock check, ranking, and
          state mutation around it are all deterministic. This can take a minute or two.
    """
    query = TrendingQuery(theme=theme, cuisine=cuisine, meal_type=meal, max_minutes=max_minutes)

    # Read the pantry deterministically, then close the DB session BEFORE the slow LLM calls.
    with get_session() as session:
        pantry = list_ingredients(session)

    console.print("[dim]Matching what's trending to your pantry… (may take a minute or two)[/dim]")
    # The LLM step (one match call per trending recipe). Any failure -> clean one-line + exit 1:
    #   ResolutionError - a systemic content failure surfacing from the resolver
    #   ClaudeCliError  - claude not installed / not logged in / timeout / quota / bad output
    try:
        recipes = find_trending(query)
        fits = rank_recipes(recipes, pantry)
    except (ResolutionError, ClaudeCliError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc

    if not fits:  # nothing trending, or nothing with ingredients to resolve — not an error
        console.print("[yellow]No cookable ideas found[/yellow] — try a broader theme.")
        return

    _present_ranked(fits)  # shared render + cook (R1: `plan` calls the same helper)


@app.command()
def plan(
    theme: Annotated[
        str | None, typer.Option("--theme", "-t", help="override the pantry-derived focus")
    ] = None,
    cuisine: Annotated[str | None, typer.Option("--cuisine", "-c", help="e.g. 'thai'")] = None,
    meal: Annotated[str | None, typer.Option("--meal", "-m", help="e.g. 'dinner'")] = None,
    max_minutes: Annotated[
        int | None, typer.Option("--max-minutes", help="cap on total cook time")
    ] = None,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="show the per-stage trace")
    ] = False,
) -> None:
    """Plan tonight's cooking from your pantry (Phase-4 orchestrator, the WAT 'Agent').

    WHAT: reads your pantry, synthesizes what you need, searches the trendy live web for it,
          ranks the results by what you can cook tonight, and (on confirm) adjusts the pantry.
    WHY:  the orchestration is deterministic; the LLM only reasons inside the tools it chains.
          This can take a few minutes. Flags override the pantry-derived trendy search.
    """
    # Read the pantry deterministically, then close the DB session BEFORE the slow LLM calls.
    with get_session() as session:
        pantry = list_ingredients(session)

    console.print("[dim]Planning from your pantry… this can take a few minutes.[/dim]")
    # The orchestrator (chains synthesize -> trending -> rank). Any transport error or the
    # foundational synthesis failure becomes a clean one-line message + exit 1 (never a traceback):
    #   RecipeSynthesisError - can't tell what you need (content, Stage 1)
    #   SpoonacularError     - the degraded fallback's transport failed
    #   ClaudeCliError       - claude missing / not logged in / timeout / quota / bad output
    try:
        result = make_plan(
            pantry, theme=theme, cuisine=cuisine, meal=meal, max_minutes=max_minutes
        )
    except (RecipeSynthesisError, SpoonacularError, ClaudeCliError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc

    if verbose:  # the full per-stage trace (post-hoc in v1): name · outcome · seconds · detail
        for s in result.stages:
            console.print(f"[dim]{s.name} · {s.outcome} · {s.seconds}s · {s.detail or ''}[/dim]")

    if result.degraded:  # fell back to unranked Spoonacular ideas — NO cook prompt (no lines)
        console.print(
            "[yellow]Couldn't rank by your pantry[/yellow] "
            "(no trendy recipes with ingredients) — here are some ideas."
        )
        if not result.ideas:  # both trending and fallback empty — friendly, not an error
            console.print("[yellow]Nothing found[/yellow] — try a broader theme.")
            return
        table = Table(title="Recipe ideas")
        for column in ("Title", "Ready", "Source"):
            table.add_column(column)
        for r in result.ideas:
            ready = f"{r.ready_minutes} min" if r.ready_minutes else "-"
            table.add_row(r.title, ready, r.source_url or "-")
        console.print(table)
        return

    if not result.fits:  # ranked path but nothing rankable — friendly, no prompt
        console.print("[yellow]No cookable ideas found[/yellow] — try a broader theme.")
        return

    _present_ranked(result.fits)  # shared render + cook (R1)


def _ask_cook_choice(n: int) -> int | None:
    """1-based prompt -> 0-based index, or None to skip. EOF / empty / bad input -> None."""
    try:
        raw = input(f"Cook one now? [1-{n}, or Enter to skip]: ").strip()
    except EOFError:  # no TTY / piped / CliRunner with no input
        return None
    if not raw.isdigit():
        return None
    i = int(raw)
    return i - 1 if 1 <= i <= n else None


def _present_ranked(fits: list[RecipeFit]) -> None:
    """Render the ranked table + per-recipe ⚠ notes + shopping list, then the non-blocking
    cook prompt (present-and-confirm). Shared by `cook-ideas` and `plan` (R1 — no copy-paste).

    Binding the cook return as `cook_result` (not `result`) keeps callers free to hold their own
    `result` (the `plan` command's PlanResult) without a naming collision.
    """
    table = Table(title="What can I cook tonight?")
    for column in ("Title", "Missing", "⚠", "Can make?"):
        table.add_column(column)
    for f in fits:
        table.add_row(
            f.recipe.title,
            str(len(f.missing)),
            str(len(_uncertain(f))),
            "✓" if not f.missing else "✗",  # "Can make?" is derived (not a field on RecipeFit)
        )
    console.print(table)

    for f in fits:  # per-recipe detail: link (research it) + uncertain ⚠ matches + shopping list
        console.print(f"\n[bold]{f.recipe.title}[/bold]")
        if f.recipe.source_url:
            console.print(f"  [dim]🔗 {f.recipe.source_url}[/dim]")
        for m in _uncertain(f):
            why = f" — {m.note}" if m.note else ""
            console.print(f"  [yellow]⚠[/yellow] {m.pantry_name}{why}")
        for line in _shopping_list(f):
            console.print(f"  [yellow]•[/yellow] {line}")

    choice = _ask_cook_choice(len(fits))  # EOF / empty / out-of-range -> None (skip)
    if choice is None:
        return
    _help_me_cook(fits[choice])


def _help_me_cook(fit: RecipeFit) -> None:
    """Selecting a recipe = "help me cook this dish": surface the link + numbered steps to cook
    from, then adjust the pantry (ledger-honest) as a footer. Ingredients aren't repeated —
    they're already in the shopping list above.
    """
    console.print(f"\n[bold green]Let's cook {fit.recipe.title}[/bold green]")
    if fit.recipe.source_url:
        console.print(f"  🔗 {fit.recipe.source_url}")
    if fit.recipe.steps:
        console.print("\n[bold]Steps:[/bold]")
        for n, step in enumerate(fit.recipe.steps, start=1):
            console.print(f"  {n}. {step}")
    else:
        console.print("[dim](open the link above for the full recipe)[/dim]")

    with get_session() as session:  # the pantry update, demoted to a footer
        cook_result = cook(session, fit)
    if cook_result.flipped or cook_result.to_update:
        console.print("\n[dim]Pantry updated:[/dim]")
        for flip in cook_result.flipped:
            console.print(f"  {flip}")
        if cook_result.to_update:
            used = ", ".join(f"`pantry use {n} <amt>`" for n in cook_result.to_update)
            console.print(f"  Used (update by hand): {used}")
