# Workflow 02 — Recipe Retrieval

**Stage:** Phase 2b &nbsp;·&nbsp; **Tool:** `services/retrieval.py` &nbsp;·&nbsp; **Transport:** `core/spoonacular.py` &nbsp;·&nbsp; **Command:** *(none in v1)*

## Intent
Turn a validated `RecipeQuery` (from Phase 2a) into **real, highly-rated candidate recipes** from the
Spoonacular `complexSearch` API. A **deterministic** WAT Tool — **no LLM** (see ADR 0008). The
"highly-rated" signal is a fixed `sort=popularity`, applied here in code, not by the model.

## Trigger
No CLI command in v1. Invoked programmatically — `find_recipes(query)` — by the future Phase-4
orchestrator, or by a fixed `RecipeQuery` in the live smoke. (CLI chaining `suggest`→`retrieve` is
deferred to the orchestrator; keeps the WAT Tools/Agent boundary clean.)

## Inputs
- A validated **`RecipeQuery`** (`include_ingredients`, plus optional `exclude_ingredients`, `keywords`,
  `cuisine`, `dish_type`, `max_ready_minutes`).
- **`PANTRY_SPOONACULAR_API_KEY`** set (env / `.env`).
- Network access to `api.spoonacular.com`.

## Steps
1. *(deterministic)* `_query_to_params()` maps the `RecipeQuery` onto complexSearch params and adds the
   constants `sort=popularity`, `number=5`, `addRecipeInformation=true`. Optional facets are omitted
   when unset (absent key, never `""`).
2. *(transport)* the injected fetcher (`fetch_recipes`) does `httpx.get(complexSearch, params=…)` with
   the **apiKey injected here** (secret stays out of the pure mapping), maps HTTP/network failures to
   `SpoonacularError`, and returns the parsed JSON dict.
3. *(validation gate — the sacred boundary)* `_parse_recipes()` runs `Recipe.model_validate()` over the
   body's `results`; unmodeled API fields are dropped; a malformed item raises `SpoonacularError`.
4. Returns `list[Recipe]`.

## Output
A `list[Recipe]` — each `Recipe`: `id`, `title`, `image`, `ready_minutes` (`readyInMinutes`),
`servings`, `source_url` (`sourceUrl`). The two camelCase API keys map via `validation_alias`.

## Determinism boundary
Entirely deterministic — no non-deterministic step. The only guard app state relies on is
`Recipe.model_validate()` per result. `sort=popularity` is a fixed constant applied in
`_query_to_params`, **not** the model's job.

## Edge cases / failure modes
Transport/contract failures raise `SpoonacularError` (carrying a `.kind`). An **empty** result set is
**not** an error.

| Situation | Detection | Raised (`.kind`) |
|---|---|---|
| API key blank / not set | pre-request check | `SpoonacularError` (`auth`) |
| Invalid API key | HTTP 401 | `SpoonacularError` (`auth`) |
| Daily quota / points exhausted | HTTP 402 | `SpoonacularError` (`quota`) |
| Rate limited | HTTP 429 | `SpoonacularError` (`rate_limit`) |
| Other non-2xx | HTTP 4xx/5xx | `SpoonacularError` (`http_error`) |
| Timeout (>15s) | `httpx.TimeoutException` | `SpoonacularError` (`timeout`) |
| Connection / DNS failure | `httpx.RequestError` | `SpoonacularError` (`network`) |
| Non-JSON / not an object / missing `results` | parse | `SpoonacularError` (`bad_output`) |
| Malformed result item (e.g. missing `id`) | `Recipe.model_validate` | `SpoonacularError` (`bad_output`) |
| **Zero matches** (`results: []`) | — | *none* — returns `[]`; caller decides |

*(Confirmed on the live smoke 2026-08-30: `sort=popularity` is accepted; 5 real results validated against
`Recipe`.)*

## Tests
- `tests/test_spoonacular.py` — offline, fakes `httpx.get`: params/URL assembly, apiKey injection, each
  HTTP status → `.kind`, timeout/network, non-JSON / non-object → `bad_output`.
- `tests/test_retrieval.py` — offline, injects a fake `RecipeFetcher` + saved fixture
  (`tests/fixtures/spoonacular_complexsearch.json`): parses every result in order, camelCase→snake_case
  mapping, missing optionals → `None`, empty → `[]`, missing/malformed → `bad_output`, transport error
  propagation, and the full `_query_to_params` mapping (incl. `sort=popularity`, omitted optionals).
