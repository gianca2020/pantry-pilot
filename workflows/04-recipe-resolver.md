# Workflow 04 — Recipe Resolver ("what can I cook tonight?")

**Stage:** Phase 3 &nbsp;·&nbsp; **Tool:** `services/resolver.py` &nbsp;·&nbsp; **Transport:** `core/claude_cli.py` (tools OFF) &nbsp;·&nbsp; **Command:** `pantry cook-ideas`

## Intent
Given what's actually in the pantry, rank fetched (trending) recipes by **what you can cook tonight**: an LLM
matches each free-text recipe ingredient line to a canonical pantry item, then deterministic code decides
in-stock, ranks by fewest-missing, and (on "cook") adjusts the pantry. **The LLM only *matches*; the code
decides everything that touches state.** See ADR 0010 + the design doc.

## Trigger
`pantry cook-ideas [-t/--theme] [-c/--cuisine] [-m/--meal] [--max-minutes N]` — a thin front door (mirrors
`pantry suggest`/`pantry trending`), or programmatically `rank_recipes(recipes, pantry)`. Unifying sources #1/#2
and chaining suggest→fetch→resolve→cook remains the Phase-4 orchestrator's job; this command is a direct front
door over source #2.

## Inputs
- The **pantry** — `list_ingredients(session)` → `list[Ingredient]` (the canonical match targets; `name` unique).
- **Recipes with ingredient lines** — from `find_trending(TrendingQuery(...))` (source #2). Recipes without
  `ingredients` (source #1) are skipped.
- An authenticated Claude Code CLI (subscription). **No `ANTHROPIC_API_KEY`** — scrubbed so billing can only hit
  the subscription ($0 marginal). **No network** (tools OFF — this is pure text matching).

## Steps
1. *(deterministic)* `_to_resolution_prompt(recipe, pantry_names)` builds the user prompt: the pantry item names
   to match against + the recipe's ingredient lines (one per line). A test asserts every pantry name + every
   line appears.
2. *(deterministic)* `_resolver_persona()` builds the system prompt: match each line to exactly one pantry name
   **verbatim** or `null`; never invent a name not in the list; when similar-but-not-clearly-the-same set
   `confident=false` + explain in `note`; one entry per line.
3. *(transport)* the injected `runner` (`run_claude`, tools **OFF**) runs `claude -p` with the prompt on stdin,
   the `RecipeResolution` JSON schema, 120 s timeout; maps failures to `ClaudeCliError`.
4. *(validation gate — the sacred boundary)* `_parse_resolution(envelope)` reads `structured_output` (fallback:
   the `result` JSON-string), then `RecipeResolution.model_validate` → `list[IngredientMatch]`. **No cardinality
   check** — only the envelope + schema *shape* is gated.
5. *(deterministic gate)* `assess(recipe, matches, pantry)` splits into **have** vs **missing**: a match is
   *have* only if its `pantry_name` is a **real** pantry row **and** `_in_stock` (QUANTITY `on_hand > 0`;
   PRESENCE not OUT). A **hallucinated** name (not in the pantry) is normalized to `null` and drops to *missing*
   (design §6 "treated as null"). Uncertain-but-stocked stays *have* (⚠).
6. *(deterministic)* `rank_recipes` skips ingredient-less recipes, swallows a single recipe's `ResolutionError`
   (content), and sorts `RecipeFit`s by `(len(missing), len(uncertain), title)`.
7. *(deterministic, on demand)* `cook(session, fit)` steps matched PRESENCE items down one notch (deduped by
   `pantry_name`, via `_step_down`), reports matched QUANTITY items in `to_update`, records **no** ledger txn.

## Output
- `rank_recipes` → `list[RecipeFit]` (each: the `recipe`, its `have` and `missing` matches), ordered best-first.
- `_shopping_list(fit)` → per-recipe lines: `"restock <name>"` (owned but OUT) / `"buy: <recipe line>"` (not
  stocked). `_uncertain(fit)` → the ⚠ subset of `have`.
- `cook(session, fit)` → `CookResult(flipped=["<name> -> <status>"], to_update=["<name>"])`.

## Determinism boundary
The per-recipe match is the one non-deterministic step; **everything app state relies on is deterministic**:
`RecipeResolution.model_validate` (schema gate) + `assess`'s pantry lookup (hallucination guard) + the stock
check + the ranking + the presence-flip. The persona *steers* the model to real names; `assess` *enforces* it.

## Edge cases / failure modes
| Situation | Detection | Result |
|---|---|---|
| Recipe has no `ingredients` (source #1) | `rank_recipes` | skipped (nothing to resolve) |
| One recipe's resolve raises `ResolutionError` (content) | `rank_recipes` | skipped; the rest still rank |
| Transport failure mid-rank (`ClaudeCliError`) | `run_claude` | propagates → CLI exit 1 (systemic) |
| Empty pantry | `assess` | every match `null` → all *missing* → recipe ranks last; **not** an error |
| Matched item OUT / `on_hand == 0`/None | `_in_stock` | *missing* → shopping list `"restock <name>"` |
| Model returns a name not in the pantry | `assess` guard | normalized to null → *missing* → `"buy: <line>"` |
| Uncertain match (`confident=false`, stocked) | `_uncertain` | counts as *have*, surfaced ⚠ (non-blocking) |
| Two recipe lines match the same pantry item | `cook` `seen` set | consumed once (deduped by `pantry_name`) |
| `cook-ideas` run non-interactively / EOF at prompt | `_ask_cook_choice` | cook skipped (no hang); ranking still printed |
| `cook` item archived/renamed since resolve | `get_ingredient` → None | skipped safely |
| `is_error` envelope | `_parse_resolution` | `ResolutionError` (`llm_failed`) |
| Non-JSON / unschematic / bad payload | `_parse_resolution` | `ResolutionError` (`bad_output`) |
| Wrong-but-schema-valid match | — | **NOT** code-caught — grading only (design §5) |

## Tests
- `tests/test_resolver.py` — offline, injects a fake `ClaudeRunner` + saved fixture
  (`tests/fixtures/recipe_resolution.json`, the inner `structured_output`); pantry as plain `Ingredient`
  objects (`session` fixture only for `cook`). Covers: `_to_resolution_prompt` includes every name + line;
  `_resolver_persona` invariants; `_parse_resolution` (structured_output + result fallback +
  `is_error`→`llm_failed` + non-JSON/unschematic→`bad_output`); `assess` (stocked→have, null/OUT/hallucinated→
  missing, uncertain stays have); `_shopping_list` restock-vs-buy; `_step_down`; `rank_recipes` (fewest-missing,
  skips ingredient-less, skips a ResolutionError recipe); `cook` (presence-flip deduped, QUANTITY in `to_update`,
  **ledger untouched**).
- `tests/test_schemas.py` — `IngredientMatch` defaults, `RecipeResolution` list, `RecipeFit`/`CookResult` shapes.
- `tests/test_cli.py` — monkeypatch `find_trending`/`rank_recipes`/`init_db`; `cook-ideas` prints the ranked
  table, the flag→`TrendingQuery` mapping, error → exit 1; `_ask_cook_choice` parse/guard (1-based→0-based,
  empty/non-numeric/out-of-range/EOF → skip).

## Build spikes / live smoke
- **Build spike (read-only):** grade a real `resolve_recipe` on one real `find_trending` recipe + a seeded
  sample pantry against design §5 (correct matches, honest `confident`, `null` when not stocked, no hallucinated
  names).
- **Live smoke (final):** seed a small pantry (`pantry add …`), then `pantry cook-ideas --theme "chicken dinner"`
  → a ranked table + shopping list; optionally cook #1 and confirm a PRESENCE item flipped + the QUANTITY nudge,
  with the ledger untouched.
