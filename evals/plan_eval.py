"""Tier-2 eval harness for the Phase-4 orchestrator `make_plan` (design §5/§7, D8).

WHAT  A small, real-LLM harness (NOT part of `uv run pytest` — `testpaths=["tests"]` excludes
      this file). For each fixed scenario it runs `make_plan` for real, auto-grades the
      DETERMINISTIC criteria, and prints the SUBJECTIVE ones for a human spot-check.
WHY   Schema-valid != good end-to-end. The unit tests prove the wiring; this proves the *result*
      on real data — is the intent sensible, are the recipes genuinely-trendy + allow-listed, are
      the matches honest, is the ranking correct? No LLM-as-judge in v1 (deferred).
HOW   `grade(pantry, result)` is a PURE function (offline-checkable) returning
      (criterion, passed, detail) rows; `run_scenario` does the (slow) real call + printing.

MODEL-CONFIG A/B (ADR 0012 D5): each scenario also runs under two model CONFIGS —
      `tiered` (the wired Haiku/Haiku/Sonnet defaults, no runners injected) vs `all-opus`
      (every step forced to Opus) — so a human can compare the deterministic scorecard
      (quality) AND the per-stage wall-clock (speed) side by side. `grade()` itself is
      config-agnostic; only which runners `make_plan` gets changes.

Run:  uv run python evals/plan_eval.py                    # all scenarios, both configs
      uv run python evals/plan_eval.py ranked              # just the named scenario(s)
      uv run python evals/plan_eval.py --config tiered     # just one config
      uv run python evals/plan_eval.py ranked --config tiered
"""

from __future__ import annotations

import sys
import urllib.parse
from dataclasses import dataclass

from pantry_pilot.core.claude_cli import claude_runner
from pantry_pilot.core.claude_web import claude_web_runner
from pantry_pilot.core.recipe_sources import ALLOW_DOMAINS
from pantry_pilot.models.enums import BaseUnit, Category, StockStatus, TrackingMode
from pantry_pilot.models.schemas import PlanResult
from pantry_pilot.models.tables import Ingredient
from pantry_pilot.pipeline.orchestrator import make_plan
from pantry_pilot.services.resolver import _shopping_list, _uncertain

# --- the two model configs under test (ADR 0012 D5): runner-kwargs for `make_plan` ---

CONFIGS: dict[str, dict[str, object]] = {
    "tiered": {},  # no runners -> make_plan uses the wired defaults (Haiku/Haiku/Sonnet)
    "all-opus": {
        "synth_runner": claude_runner("opus"),
        "trending_fetcher": claude_web_runner("opus"),
        "rank_runner": claude_runner("opus"),
    },
}

# --- fixtures: the §5 seeded pantry, built as plain Ingredient objects (no DB) ---


def _presence(name: str, category: Category, status: StockStatus) -> Ingredient:
    return Ingredient(name=name, category=category, tracking_mode=TrackingMode.PRESENCE,
                      status=status)


def _quantity(name: str, category: Category, unit: BaseUnit, on_hand: int) -> Ingredient:
    return Ingredient(name=name, category=category, tracking_mode=TrackingMode.QUANTITY,
                      base_unit=unit, on_hand=on_hand)


def _seed_pantry() -> list[Ingredient]:
    """The design §5 pantry: chicken, rice, soy sauce, garlic, spinach (OUT), onion."""
    return [
        _quantity("chicken", Category.PROTEIN, BaseUnit.GRAM, 800),
        _quantity("rice", Category.STAPLE, BaseUnit.GRAM, 1000),
        _presence("soy sauce", Category.STAPLE, StockStatus.OK),
        _presence("garlic", Category.STAPLE, StockStatus.OK),
        _presence("spinach", Category.GREEN, StockStatus.OUT),  # OUT -> should be "restock"
        _quantity("onion", Category.GREEN, BaseUnit.EACH, 3),
    ]


@dataclass
class Scenario:
    name: str
    pantry: list[Ingredient]
    note: str
    theme: str | None = None
    cuisine: str | None = None
    meal: str | None = None
    max_minutes: int | None = None


def _scenarios() -> list[Scenario]:
    return [
        Scenario("ranked", _seed_pantry(),
                 "§5 GOOD ranked path: pantry-derived intent -> trendy -> ranked fewest-missing."),
        Scenario("degrade", _seed_pantry(),
                 "§5 GOOD degraded path: a nonsense theme should yield 0 trending -> Spoonacular "
                 "fallback ideas (needs a Spoonacular key; else the fallback transport errors).",
                 theme="asdfqwer nonexistent dish zzzz"),
        Scenario("empty-pantry", [],
                 "§5 #8: empty pantry is NOT an error — the plan still renders (all missing)."),
    ]


# --- the deterministic grader (PURE: offline-checkable, no LLM) ---


def _domain(url: str) -> str:
    return urllib.parse.urlparse(url).netloc.removeprefix("www.")


def grade(pantry: list[Ingredient], result: PlanResult) -> list[tuple[str, bool, str]]:
    """Auto-grade the deterministic §5/§7 criteria. Returns (criterion, passed, detail) rows."""
    rows: list[tuple[str, bool, str]] = []
    known = {"synthesize", "trending", "fallback", "rank"}

    names = [s.name for s in result.stages]
    rows.append(("stages present & labeled",
                 len(result.stages) >= 2 and all(n in known for n in names), str(names)))

    if result.degraded:
        rows.append(("degraded -> ideas present", bool(result.ideas), f"{len(result.ideas)} ideas"))
        rows.append(("degraded -> no fits", result.fits == [], f"{len(result.fits)} fits"))
        return rows

    # ranked path
    rows.append(("source_used == trending", result.source_used == "trending", result.source_used))

    order = [(len(f.missing), len(_uncertain(f)), f.recipe.title) for f in result.fits]
    rows.append(("fits sorted (missing, uncertain, title)", order == sorted(order), str(order)))

    bad_domain = [f.recipe.source_url for f in result.fits
                  if not f.recipe.source_url or _domain(f.recipe.source_url) not in ALLOW_DOMAINS]
    rows.append(("every recipe allow-listed", not bad_domain, str(bad_domain)))

    pantry_names = {i.name for i in pantry}
    hallucinated = [m.pantry_name for f in result.fits for m in f.have
                    if m.pantry_name not in pantry_names]
    rows.append(("no hallucinated pantry_name in have", not hallucinated, str(hallucinated)))

    malformed = [ln for f in result.fits for ln in _shopping_list(f)
                 if not (ln.startswith("restock ") or ln.startswith("buy: "))]
    rows.append(("shopping-list lines well-formed", not malformed, str(malformed)))
    return rows


# --- the (slow) real run + printing ---


def _print_subjective(result: PlanResult) -> None:
    """Print the criteria a human must eyeball (intent quality, trendiness, match correctness)."""
    print("  INTENT:", result.intent.model_dump())
    if result.degraded:
        for r in result.ideas:
            print(f"    idea: {r.title}  ({r.source_url})")
        return
    for f in result.fits:
        have = ", ".join(f"{m.recipe_ingredient}->{m.pantry_name}" for m in f.have) or "-"
        print(f"    #{f.recipe.title}  ({f.recipe.source_url})")
        print(f"       missing={len(f.missing)} uncertain={len(_uncertain(f))}  have: {have}")


def run_scenario(sc: Scenario, config_name: str, runners: dict[str, object]) -> bool:
    print(f"\n=== SCENARIO: {sc.name}  [config: {config_name}] ===\n  {sc.note}")
    result = make_plan(sc.pantry, theme=sc.theme, cuisine=sc.cuisine,  # real LLM + web
                       meal=sc.meal, max_minutes=sc.max_minutes, **runners)
    rows = grade(sc.pantry, result)
    met = sum(1 for _, ok, _ in rows if ok)
    for crit, ok, detail in rows:
        print(f"  [{'PASS' if ok else 'FAIL'}] {crit}  ({detail})")
    _print_subjective(result)
    print(f"  SCORE [{config_name}]: {met}/{len(rows)} deterministic criteria met")
    for s in result.stages:
        print(f"  STAGE [{config_name}] {s.name}: {s.seconds:.1f}s ({s.outcome})")
    return met == len(rows)


def main(argv: list[str]) -> int:
    args = argv[1:]
    config_filter: set[str] | None = None
    if "--config" in args:
        i = args.index("--config")
        config_filter = {args[i + 1]}
        del args[i:i + 2]
    wanted = set(args)

    scenarios = [s for s in _scenarios() if not wanted or s.name in wanted]
    configs = {n: r for n, r in CONFIGS.items() if not config_filter or n in config_filter}

    ok = True
    for sc in scenarios:
        for config_name, runners in configs.items():
            try:
                ok = run_scenario(sc, config_name, runners) and ok
            except Exception as exc:  # a harness: report the failure, don't crash the whole run
                print(f"  ERROR in scenario {sc.name} [{config_name}]: {type(exc).__name__}: {exc}")
                ok = False
    print(f"\n{'ALL GOOD' if ok else 'SOME CRITERIA FAILED — inspect above'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
