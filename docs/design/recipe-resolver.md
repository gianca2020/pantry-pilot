# Elephant Design — Phase 3: Recipe Resolver ("what can I cook tonight?")

**Status: DRAFT v2 (design session 2026-08-30; Goldfished ×3 → seams closed). Design-of-record for Phase 3 (semantic inventory resolver + state mutation). Implementation is a SEPARATE gate (`writing-plans` → TDD) — no code from this doc alone. Depends on Phase 2c (source #2 `find_trending`, PR #14) for recipes-with-ingredients, and Phase 1 for the pantry ledger.**
Follows the format of `docs/design/trending-recipe-source.md`. Branch: `dev-feature-8-recipe-resolver`.

> Vision: given what's actually in your pantry, rank fetched recipes by **what you can cook tonight** — an
> LLM matches each recipe ingredient line to a pantry item, then deterministic code decides in-stock, ranks
> by fewest-missing, and (on "cook") adjusts the pantry. **The LLM only *matches*; the code decides
> everything that touches state.** All modules under `src/pantry_pilot/`; imports are absolute.

---

## 1. Context
Phases 2a–2c produce recipes (source #1 Spoonacular; source #2 trending web). Phase 3 closes the loop
against the **pantry**: which of these can I actually make, and what am I missing? This is **LLM boundary
#3** — semantic matching of free-text recipe ingredient lines to canonical pantry rows — plus the **state
mutation** half ("cook it" adjusts the pantry).

**Architectural decisions (locked in the 2026-08-30 design session):**
- **Presence-level**, not quantity-level: have/missing from the DB, **no cup→gram unit math** (D1).
- **Pantry-first ranking** ("what can I cook tonight?"), built on a **per-recipe resolver** (D2).
- The resolver is a **single-shot tool** (`run_claude`, tools OFF), **NOT agentic** — the planning agent is
  deferred to the Phase-4 orchestrator (D3).
- **Cook = presence-flip + nudge** (ledger-honest): flip matched PRESENCE items down; report QUANTITY items
  to update by hand; never fabricate a ledger amount (D4).
- Each match carries a **confidence flag**; uncertain matches are **highlighted (⚠), never blocked** (D5).

## 2. Ground truth — current code a fresh reader needs (verbatim)

`Recipe` (the source of the ingredient lines) — `models/schemas.py`:
```python
class Recipe(BaseModel):
    id: int | None = None
    title: str
    image: str | None = None
    ready_minutes: int | None = Field(default=None, validation_alias="readyInMinutes")
    servings: int | None = None
    source_url: str | None = Field(default=None, validation_alias="sourceUrl")
    ingredients: list[str] | None = None   # <- the free-text lines we resolve (source #2 fills these)
    steps: list[str] | None = None
```

Pantry rows + ledger — `models/tables.py`:
```python
class Ingredient(SQLModel, table=True):
    id: int | None; name: str  # name is unique + indexed (the match target)
    category: Category
    tracking_mode: TrackingMode          # QUANTITY | PRESENCE
    base_unit: BaseUnit | None            # QUANTITY: EACH | GRAM | MILLILITER
    on_hand: int | None                   # QUANTITY: cached ledger sum
    status: StockStatus | None            # PRESENCE: OUT | LOW | OK (always set for PRESENCE, per add-time guard)
    is_active: bool; ...
class PantryTransaction(SQLModel, table=True): ...  # append-only ledger; on_hand = SUM(change_amount)
```
Enums (`models/enums.py`): `StockStatus = OUT | LOW | OK`; `TrackingMode = QUANTITY | PRESENCE`;
`TxnReason = INITIAL | RESTOCK | CONSUME | DISCARD | ADJUST`.

Pantry service (state mutation reuses these verbatim) — `services/pantry.py`:
```python
list_ingredients(session, include_archived=False, category=None) -> list[Ingredient]
get_ingredient(session, name) -> Ingredient | None
set_status(session, ingredient, status) -> Ingredient          # PRESENCE only; raises on QUANTITY
record_transaction(session, ingredient, change_amount, reason, note=None) -> PantryTransaction  # QUANTITY only
```
`Session` is `from sqlmodel import Session` (the type `cook` takes; every pantry-service call needs it).

Source-#2 fetcher (the recipe source) — `services/trending.py` (Phase 2c, PR #14):
```python
class TrendingQuery(BaseModel):     # all optional; empty = "what's hot overall"
    theme: str | None; cuisine: str | None; meal_type: str | None; max_minutes: int | None
def find_trending(query: TrendingQuery, *, month=None, fetcher: ClaudeRunner | None = None) -> list[Recipe]
```
`find_trending` returns web recipes **with `ingredients` filled**. The CLI builds a `TrendingQuery` from its
flags (§4.7) — exactly as the existing `pantry trending` command does. (`--max-minutes` shapes the search
string inside `find_trending`; it is NOT a post-fetch filter here.)

LLM transport (reused verbatim, **tools OFF**) — `core/claude_cli.py`:
```python
run_claude(prompt, schema, *, system) -> dict   # argv has --tools "" (deterministic, no web)
class ClaudeRunner(Protocol): __call__(prompt, schema, *, system) -> dict[str, object]
class ClaudeCliError(Exception): kind  # not_found|auth|quota|timeout|bad_output|failed
```
Envelope keys (`--output-format json`): `is_error` (bool), `structured_output` (schema-shaped, primary),
`result` (JSON-string mirror, fallback). **Parse+validate gate pattern** (mirror `services/synthesizer.py`
and `services/trending.py`): `is_error` → raise; read `structured_output` else `json.loads(result)` (guard
`JSONDecodeError`); `Model.model_validate(payload)` (`ValidationError` → raise). Tests inject a fake runner
returning a canned **envelope dict**; nothing shells out.

## 3. Scope & non-goals (v1)
- **In:** `resolve_recipe` (the LLM match); `assess` (have/missing gate); `rank_recipes(recipes, pantry)`
  (fewest-missing) with ⚠ uncertain highlights + a per-recipe shopping list; `cook(session, fit)`
  (presence-flip + nudge); a `pantry cook-ideas` CLI over `find_trending`.
- **Out (deferred):** unit/quantity math (cup→gram) and exact quantity deduction; **agentic** substitution
  reasoning (Phase-4 / future); ranking source-#1 (Spoonacular) recipes (they carry no ingredient lines
  yet); persisting fetched recipes; the full pantry→suggest→fetch→resolve→cook **orchestration**
  (Phase-4 `pipeline/orchestrator.py`, the WAT "Agent").

## 4. Target design

### 4.1 Modules (all under `src/pantry_pilot/`)
- `models/schemas.py` — add `IngredientMatch`, `RecipeResolution`, `RecipeFit`, `CookResult` (§4.3).
- `services/resolver.py` — `resolve_recipe`, `_resolver_persona`, `_to_resolution_prompt`,
  `_parse_resolution`, `assess`, `_in_stock`, `_uncertain`, `rank_recipes`, `cook`, `_step_down`,
  `_shopping_list`, `ResolutionError` (the conceptual core; author hand-writes the gate / `assess` /
  `rank` / persona / `cook`).
- `cli.py` — `pantry cook-ideas` command (thin: `find_trending` → `rank_recipes` → print → optional cook).
- `tests/test_resolver.py`, `tests/fixtures/recipe_resolution.json`; extend `tests/test_schemas.py`,
  `tests/test_cli.py`.

### 4.2 The LLM boundary — `resolve_recipe` (single-shot, tools OFF, the ONLY non-deterministic step)
Reuses `run_claude` (NOT `run_claude_web` — no web needed).
```python
def resolve_recipe(
    recipe: Recipe, pantry_names: list[str], *, runner: ClaudeRunner | None = None
) -> list[IngredientMatch]:
    runner = runner or run_claude
    prompt = _to_resolution_prompt(recipe, pantry_names)
    envelope = runner(prompt, RecipeResolution.model_json_schema(), system=_resolver_persona())
    return _parse_resolution(envelope)
```
- `_to_resolution_prompt(recipe, pantry_names)` — deterministic + testable; **concrete template**:
```python
def _to_resolution_prompt(recipe: Recipe, pantry_names: list[str]) -> str:
    pantry = ", ".join(pantry_names) or "(pantry is empty)"
    lines = "\n".join(f"- {line}" for line in (recipe.ingredients or []))
    return (
        f"PANTRY ITEMS (match ONLY against these exact names):\n{pantry}\n\n"
        f"RECIPE: {recipe.title}\n"
        f"RECIPE INGREDIENTS (return one match per line, in order):\n{lines}"
    )
```
(A test asserts each pantry name + each ingredient line appears in the output.)
- `_resolver_persona()` — concrete string: *"Match each recipe ingredient line to exactly one pantry item
  name from the provided list, or `null` if the pantry has nothing suitable. Use the pantry names verbatim —
  NEVER invent a name that isn't in the list. When the two are similar but not clearly the same, set
  `confident` to false and say why in `note`. Return exactly one entry per recipe ingredient line."*
- `_parse_resolution(envelope) -> list[IngredientMatch]` = the §2 gate: `is_error` →
  `ResolutionError(kind="llm_failed")`; read `structured_output` else `json.loads(result)` (non-str or
  `JSONDecodeError` → `bad_output`); `RecipeResolution.model_validate(payload)` (`ValidationError` →
  `bad_output`); return `resolution.matches`. **No cardinality check** — a reply with fewer/more matches
  than recipe lines is accepted (unmatched lines just aren't assessed); only the envelope + schema *shape*
  is gated.

### 4.3 Schemas (D5 confidence)
```python
class IngredientMatch(BaseModel):
    recipe_ingredient: str              # the raw recipe line
    pantry_name: str | None = None      # matched pantry item name, or None = not stocked
    confident: bool = True              # false = judgment call (highlight, don't block)
    note: str | None = None             # optional "why", e.g. "'scallions' ≈ 'green onions'?"

class RecipeResolution(BaseModel):      # the LLM's reply; RecipeResolution.model_json_schema() -> CLI
    matches: list[IngredientMatch]

class RecipeFit(BaseModel):             # deterministic assess result (one per recipe)
    recipe: Recipe
    have: list[IngredientMatch]         # matched to a stocked pantry item (may include ⚠ uncertain)
    missing: list[IngredientMatch]      # null match, hallucinated name, or matched-but-OUT/empty

class CookResult(BaseModel):
    flipped: list[str]                  # formatted "name -> newstatus" per PRESENCE item stepped down
    to_update: list[str]                # QUANTITY names to adjust by hand (never auto-guessed)
```
The model emits `RecipeResolution` (an object top-level, as `--json-schema` needs). `IngredientMatch`
defaults keep the schema forgiving: a bare `{recipe_ingredient, pantry_name}` validates with `confident`
defaulting to `True`. `CookResult.flipped` holds **formatted strings** (`f"{name} -> {status}"`), not bare
names — the CLI prints them as-is.

### 4.4 The deterministic gate — `assess` (hallucination guard + stock check)
```python
def _in_stock(item: Ingredient) -> bool:
    if item.tracking_mode == TrackingMode.QUANTITY:
        return (item.on_hand or 0) > 0                # None or 0 -> not in stock
    return item.status != StockStatus.OUT             # PRESENCE: OK or LOW counts as have

def assess(recipe: Recipe, matches: list[IngredientMatch], pantry: list[Ingredient]) -> RecipeFit:
    by_name = {i.name: i for i in pantry}
    have: list[IngredientMatch] = []
    missing: list[IngredientMatch] = []
    for m in matches:
        item = by_name.get(m.pantry_name) if m.pantry_name else None   # guard: name must be real
        if item is not None and _in_stock(item):
            have.append(m)
        else:
            missing.append(m)          # null / hallucinated name / OUT / empty
    return RecipeFit(recipe=recipe, have=have, missing=missing)
```
**Hallucination guard:** `by_name.get(pantry_name)` returns `None` for any name the model invented that
isn't actually in the pantry → that match drops to **missing**. The persona *steers* to real names; `assess`
*enforces* it — the exact belt-and-suspenders pattern as source #2's allow-list `_filter`. Matching is
**exact on `Ingredient.name`** (the persona says "verbatim"); case/whitespace normalization is a later
refinement, not v1. **Uncertain** (`confident=false`) matches that are stocked still count as **have** —
surfaced with a ⚠, never demoted or blocked (D5).

### 4.5 Ranking + shopping list + cook
```python
def _uncertain(fit: RecipeFit) -> list[IngredientMatch]:
    return [m for m in fit.have if not m.confident]

def _shopping_list(fit: RecipeFit) -> list[str]:      # one line per missing item
    return [
        f"restock {m.pantry_name}" if m.pantry_name else f"buy: {m.recipe_ingredient}"
        for m in fit.missing
    ]

def rank_recipes(
    recipes: list[Recipe], pantry: list[Ingredient], *, runner: ClaudeRunner | None = None
) -> list[RecipeFit]:
    fits: list[RecipeFit] = []
    for r in recipes:
        if not r.ingredients:                        # can't resolve without lines (source #1) -> skip
            continue
        try:
            matches = resolve_recipe(r, [i.name for i in pantry], runner=runner)
        except ResolutionError:                      # one bad recipe (content) -> skip, keep the rest
            continue
        fits.append(assess(r, matches, pantry))
    fits.sort(key=lambda f: (len(f.missing), len(_uncertain(f)), f.recipe.title))
    return fits

def _step_down(status: StockStatus) -> StockStatus:   # OK->LOW, LOW->OUT, OUT->OUT
    return {StockStatus.OK: StockStatus.LOW, StockStatus.LOW: StockStatus.OUT}.get(status, StockStatus.OUT)

def cook(session: Session, fit: RecipeFit) -> CookResult:
    flipped: list[str] = []
    to_update: list[str] = []
    seen: set[str] = set()
    for m in fit.have:                                # only what you actually have gets consumed
        if not m.pantry_name or m.pantry_name in seen:  # consume each pantry item at most once
            continue
        seen.add(m.pantry_name)
        item = get_ingredient(session, m.pantry_name)
        if item is None:                             # archived/renamed since resolve -> skip safely
            continue
        if item.tracking_mode == TrackingMode.PRESENCE:
            new = _step_down(item.status or StockStatus.OK)
            set_status(session, item, new)
            flipped.append(f"{item.name} -> {new.value}")
        else:                                        # QUANTITY: never guess grams (D4)
            to_update.append(item.name)
    return CookResult(flipped=flipped, to_update=to_update)
```
- **Shopping list** (`_shopping_list`) is **per-recipe**: `"restock <name>"` when the item is stocked-but-OUT
  (you own it, `pantry_name` is set), else `"buy: <recipe line>"` when it's not in the pantry (`pantry_name`
  is None). Not deduped across recipes — each recipe shows its own.
- **Ranking key:** fewest **missing**, then fewest **uncertain**, then title (stable).
- **Error rule:** a `ResolutionError` (content, one recipe) is swallowed → that recipe is skipped; a
  `ClaudeCliError` (transport, systemic — claude missing/timeout/auth) **propagates** and aborts the run
  (surfaces to the CLI as exit 1).
- **Cost/latency:** N serial single-shot `run_claude` calls (tools OFF → no web, fast/cheap), N =
  #recipes-with-ingredients from `find_trending` (~3–5); no batching/parallelism in v1.
- `cook` is **ledger-honest**: PRESENCE items step down one notch (deduped by `pantry_name`); QUANTITY items
  are *reported* in `to_update` (the CLI prints "run `pantry use <name> <amt>`"), never auto-deducted with a
  fabricated amount — the event-sourced ledger stays truthful.

### 4.6 Error taxonomy — mirror the two-class split
- **Transport** → reuse `ClaudeCliError` (`.kind`, incl. `timeout`).
- **Content/validation** → new `ResolutionError` in `services/resolver.py`:
```python
ResolutionErrorKind = Literal["llm_failed", "bad_output"]
class ResolutionError(Exception):
    def __init__(self, message: str, *, kind: ResolutionErrorKind) -> None: super().__init__(message); self.kind = kind
```
- **Empty pantry / all-missing / recipe-without-ingredients are NOT errors** → `[]` / skipped.

### 4.7 CLI — `pantry cook-ideas`
```
pantry cook-ideas [-t/--theme TEXT] [-c/--cuisine TEXT] [-m/--meal TEXT] [--max-minutes N]
```
Thin front door (mirrors `suggest`/`trending`); all logic lives in the service:
1. Build the query from the flags exactly as `pantry trending` does:
   `TrendingQuery(theme=theme, cuisine=cuisine, meal_type=meal, max_minutes=max_minutes)`.
2. `pantry = list_ingredients(session)`; `recipes = find_trending(query)`;
   `fits = rank_recipes(recipes, pantry)`. Keep `fits` in memory for step 4.
3. Print a ranked table with `rich.Table` (as the `list`/`trending` commands do), one row per fit:
   **Title | Missing | ⚠ | Can make?**, where `Missing = len(fit.missing)`, `⚠ = len(_uncertain(fit))`,
   `Can make? = "✓" if not fit.missing else "✗"`. Under each recipe print its `have` (deduped by
   `pantry_name`; append a ⚠ + `note` on any `confident=false` match) and its `_shopping_list(fit)`.
4. Non-blocking prompt "Cook one now? [1-N, or Enter to skip]". The ranked `fits` stay in memory (recipes
   are NOT persisted — §3); a valid 1-based index `n` selects `fits[n-1]` → `cook(session, fits[n-1])` →
   print `flipped` (e.g. `spinach -> low`) + the `to_update` nudge. Empty input, a non-numeric/out-of-range
   answer, or **EOF / no TTY** (e.g. `CliRunner` with no input, or a piped run) → **skip cook** gracefully
   (never hang, never error).
`ClaudeCliError` / `ResolutionError` reaching the CLI → clean one-line message + exit 1 (mirror
`suggest`/`trending`). Empty `fits` (nothing trending / nothing resolvable) → friendly note, no prompt.

## 5. Eval criteria — write BEFORE code (§0A.3)
Example pantry: `chicken` (has), `rice` (has), `soy sauce` (has), `garlic` (has), `spinach` (**OUT**),
`onion` (has). Recipe = a honey-garlic chicken.

**GOOD (correct match, honest confidence):**
1. `"2 lb chicken breasts, cubed"` → `chicken`, confident
2. `"1/4 cup low sodium soy sauce"` → `soy sauce`, confident
3. `"1/3 cup honey"` → `null` (genuinely not stocked → *missing* → shopping list `"buy: 1/3 cup honey"`)
4. `"3 cloves garlic, minced"` → `garlic`, confident
5. `"1 bunch spinach"` → `spinach`, confident — but `assess` marks it **missing** (row is OUT → shopping
   list `"restock spinach"`, not "buy")
6. `"2 green onions, sliced"` → `onion`, **confident=false**, note `"green onions ≈ onion?"` → counts as
   *have* but highlighted ⚠

**BAD (+ where caught):**
1. `"1/3 cup honey"` → `soy sauce` (wrong item) — *grading only*
2. Returns `"honey"` as `pantry_name` (not in the pantry list) — ***`assess` hallucination guard → missing***
3. A garbled *shape* (reply isn't a list of `{recipe_ingredient, pantry_name}` objects) — ***schema /
   `_parse_resolution` gate***. *(A wrong **count** — fewer/more matches than lines — is **tolerated**, not
   gated: unmatched lines just aren't assessed — grading only.)*
4. `"salt and pepper to taste"` → over-claims a match / `have` it shouldn't — *grading*
5. A genuine judgment call returned with `confident=true` (hides the uncertainty) — *grading* (we want ⚠)

→ #1, #4, #5 are why **schema-valid ≠ correct** — the build spike must grade real matches against this list.

## 6. Failure modes / edge cases
| Situation | Handling |
|---|---|
| Recipe has no `ingredients` (source #1 Spoonacular) | skipped from ranking (nothing to resolve) |
| A single recipe's resolve raises `ResolutionError` (content) | `rank_recipes` skips it, ranks the rest |
| Transport failure mid-rank (`ClaudeCliError`) | propagates → CLI exit 1 (systemic, not per-recipe) |
| Empty pantry | every match `null` → all *missing* → recipe ranks last; **not** an error |
| Matched item is OUT / `on_hand == 0` (or None) | `assess` → *missing* (you stock it → "restock", not "buy") |
| Model returns a name not in the pantry list | `assess` hallucination guard → treated as null → *missing* |
| Uncertain match (`confident=false`, stocked) | counts as *have*, surfaced ⚠ (non-blocking) |
| Two recipe lines match the same pantry item | `cook` dedups by `pantry_name` → consumed once |
| `cook-ideas` run non-interactively / EOF at the prompt | cook skipped (no hang); ranking still printed |
| `cook` item archived/renamed since resolve | `get_ingredient` returns None → skipped safely |
| `is_error` envelope | `ResolutionError(kind="llm_failed")` |
| Non-JSON / unschematic / bad payload | `ResolutionError(kind="bad_output")` |
| Wrong-but-schema-valid match | NOT code-caught — grading only (§5) |

## 7. Verification / testing (verification-left, offline)
- `tests/test_resolver.py` — inject a fake `ClaudeRunner` returning a canned **envelope dict**; fixture
  `recipe_resolution.json` is the inner `structured_output` (a `RecipeResolution`). Build a small pantry as
  plain `Ingredient` objects (no DB needed for `assess`/`rank`; use the `session` fixture only for `cook`).
  Assert: `_to_resolution_prompt` includes every pantry name + ingredient line; `resolve_recipe` parses
  matches; `assess` puts stocked→have, null/OUT/`on_hand=0`→missing, and a **name-not-in-pantry→missing**
  (hallucination guard); an uncertain match stays in `have`; `_shopping_list` yields `restock <name>` for an
  OUT match and `buy: <line>` for a null match; `rank_recipes` orders by fewest-missing (then uncertain,
  then title), **skips ingredient-less recipes**, and **skips a recipe whose resolve raises
  `ResolutionError`** while keeping the rest; `cook` steps PRESENCE down (deduped), lists QUANTITY in
  `to_update`, and records **no** transaction (ledger untouched); `is_error`→`llm_failed`;
  `{"result":"<non-JSON>"}`→`bad_output`; empty pantry → all missing.
- `tests/test_schemas.py` — `IngredientMatch` defaults (`confident=True`, `note=None`, `pantry_name=None`);
  `RecipeResolution` validates a list; `RecipeFit`/`CookResult` shapes.
- `tests/test_cli.py` — monkeypatch `find_trending` + `rank_recipes` (or the resolver) + `init_db`;
  `cook-ideas` prints the ranked table + shopping list; the flag→`TrendingQuery` mapping; error → exit 1;
  the interactive cook via `CliRunner(input="1\n")`; and **no-input** (`CliRunner` default) → skips cook,
  exit 0.
- **Build spike (read-only):** grade a real `resolve_recipe` on one real `find_trending` recipe + a sample
  pantry against §5.
- **Live smoke (final):** `pantry cook-ideas --theme "chicken dinner"` against a real seeded pantry.

## 8. Decisions (resolved 2026-08-30 design session)
- **D1** precision = **presence-level** (have/missing from the DB; no cup→gram unit math).
- **D2** flow = **pantry-first ranking** ("what can I cook tonight?"), built on a per-recipe resolver.
- **D3** agency = **single-shot tool** (`run_claude`, tools OFF); the planning agent is the Phase-4
  orchestrator, NOT this step.
- **D4** cook = **presence-flip + nudge** (ledger-honest): PRESENCE steps down (deduped); QUANTITY reported,
  never auto-deducted with a guessed amount.
- **D5** each match carries **`confident` + `note`**; uncertain matches are highlighted ⚠ (non-blocking) and
  still count as *have*; a **hallucination guard** in `assess` forces any unknown `pantry_name` to *missing*.
- **D6** candidate recipes come from **source #2** (`find_trending`); source-#1 recipes (no ingredients) are
  skipped for v1.
- **D7** `rank_recipes` **skips** a recipe whose resolve raises `ResolutionError` (content) and ranks the
  rest; a `ClaudeCliError` (transport, systemic) propagates. N serial single-shot calls, no batching (N≈3–5).
- **D8** per-recipe **shopping list**: `"restock <name>"` for a stocked-but-OUT item, `"buy: <recipe line>"`
  for a not-stocked item; **Can make? = no missing**.

## 9. What approval authorizes
(1) A Goldfish pass on this doc (fresh no-context agent implements from the doc alone; fix the DOC on any
stumble — §0A.2) — **done ×3, seams closed (v2)**; (2) `writing-plans` → TDD build. Author hand-writes the
conceptual core (`_parse_resolution` gate, `assess`, `rank_recipes`, `_resolver_persona`, `cook`, the eval
rubric); Claude writes plumbing (schemas, CLI wiring, test scaffolding). **No implementation code from
design-approval alone.**
