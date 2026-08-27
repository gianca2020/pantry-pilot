# Workflow 01 — Recipe-Query Synthesis

**Stage:** Phase 2a &nbsp;·&nbsp; **Tool:** `services/synthesizer.py` &nbsp;·&nbsp; **Command:** `pantry suggest`

## Intent
Turn a snapshot of the pantry plus a macro goal into a structured, schema-validated recipe-search
query (`RecipeQuery`) that Phase 2b can hand to Spoonacular.

## Trigger
```
pantry suggest --goal <protein|carb|green|staple>
```

## Inputs
- The **active pantry** (all non-archived ingredients), read via `list_ingredients(session)`.
- A macro **`goal`** (`Category`).
- Config: `ANTHROPIC_API_KEY`.

## Steps
1. *(deterministic)* CLI reads the active pantry, then closes the DB session.
2. *(deterministic)* `_format_pantry()` renders the pantry + goal into a plain-text prompt.
3. *(LLM — the only non-deterministic step)* `synthesize_recipe_query()` calls
   `client.messages.parse(..., output_format=RecipeQuery)`.
4. *(validation)* `messages.parse` validates the reply against the `RecipeQuery` schema.
5. *(deterministic)* CLI prints the validated query as JSON.

## Output
A `RecipeQuery`: `include_ingredients`, `exclude_ingredients`, `keywords`, `cuisine`, `dish_type`,
`max_ready_minutes`. These map onto Spoonacular `complexSearch` parameters (consumed in Phase 2b).

## Determinism boundary
Only step 3 is non-deterministic, and its output is schema-validated (step 4) before anything trusts
it. "Highly-rated" is **not** the model's job — it is a fixed `sort=popularity` applied
deterministically in the Phase-2b retrieval call.

## Edge cases / failure modes
| Situation | Behaviour |
|---|---|
| No `ANTHROPIC_API_KEY` | `get_client` raises `RuntimeError` → CLI prints a clear message, exit 1 |
| Model refusal (`stop_reason == "refusal"`) or empty parse | `RecipeSynthesisError` → exit 1 |
| Auth / rate-limit / network / server error | `anthropic.APIError` (SDK retries transient ones) → exit 1 |
| Empty pantry | Still yields a query (`include_ingredients` may be empty); Phase 2b decides handling |

## Tests
- `tests/test_synthesizer.py` — offline, fake client (happy path, refusal, prompt contents).
- `tests/test_cli.py` — offline smoke test of the `suggest` command.
