# ADR 0008 — Recipe retrieval via the Spoonacular complexSearch API (deterministic)

**Status:** Accepted &nbsp;·&nbsp; **Phase:** 2b &nbsp;·&nbsp; **Design:** this ADR + `workflows/02-recipe-retrieval.md`

## Context
Phase 2a produces a validated `RecipeQuery` (`pantry suggest`, the one LLM boundary via the Claude
CLI — [ADR 0007](0007-llm-boundary-via-claude-cli.md)). Phase 2b must turn that query into **real
candidate recipes**. This is the WAT **Tools** layer: deterministic, single-purpose, **no LLM**. We
need a structured recipe source and a clean, offline-testable boundary that mirrors the LLM transport
seam. Full failure matrix + step list live in the SOP; this ADR records the decision.

## Decision
- **Source (v1):** Spoonacular `GET /recipes/complexSearch`. A single, clean, structured source.
  `sort=popularity` supplies the "highly-rated" signal **deterministically** (not the LLM's job).
- **Boundary mechanism:** `services/retrieval.find_recipes(query, *, fetcher=None)` calls an injected
  `RecipeFetcher` (default `core/spoonacular.fetch_recipes`, an `httpx` GET). The **service** owns
  contract (`RecipeQuery` → params, `results` → `Recipe`); the **fetcher** owns transport (URL, apiKey
  injection, status/network mapping, JSON parse). Same testable-by-injection pattern as
  `ClaudeRunner`/`run_claude`.
- **Determinism gate:** `Recipe.model_validate()` over each result — API JSON never reaches app state
  unvalidated (CLAUDE.md determinism rule). **Rich `Recipe`** (`addRecipeInformation=true`): `id`,
  `title`, `image`, `ready_minutes`, `servings`, `source_url`; the two camelCase API keys
  (`readyInMinutes`, `sourceUrl`) map via `validation_alias`; unmodeled fields are ignored.
- **Params mapping:** include/exclude ingredients comma-joined; `keywords`→`query`; `cuisine`;
  `dish_type`→`type`; `max_ready_minutes`→`maxReadyTime`; constants `sort=popularity`, `number=5`,
  `addRecipeInformation=true`. Optional facets are omitted when unset (absent key, never `""`).
- **Secret handling:** `spoonacular_api_key` (`PANTRY_SPOONACULAR_API_KEY` / `.env`), read **inside
  transport** and injected as the `apiKey` param — kept out of the pure mapping. A blank key raises
  `SpoonacularError(kind="auth")` before any request.
- **Error taxonomy:** `SpoonacularError(Exception)` with `.kind`
  `Literal["auth","quota","rate_limit","timeout","network","bad_output","http_error"]`, mapped on **HTTP
  status** (401→auth, 402→quota, 429→rate_limit, other→http_error) + `httpx` timeout/network + JSON
  parse. Mirrors `ClaudeCliError`. Mapping on status codes is exact — an improvement over the CLI seam's
  stderr-substring heuristic.
- **Empty results are NOT an error:** `results: []` → `find_recipes` returns `[]`; the caller /
  Phase-4 orchestrator decides messaging (mirrors "empty pantry still yields a query").
- **No CLI in v1:** `find_recipes` stays a pure Tool; chaining `suggest`→`retrieve` is deferred to the
  Phase-4 orchestrator (WAT Agent layer). The live smoke uses a throwaway fixed-`RecipeQuery` snippet.

## Consequences
- Real, popularity-sorted candidate recipes, **fully offline-testable** (saved fixture + injected
  fetcher; nothing hits the network in tests).
- One small dependency (`httpx`). The "structured source via HTTP, validated at the boundary"
  architecture is self-evident from the code.
- Behaviour depends on an external contract we don't own (Spoonacular's response shape); the saved
  fixture + `model_validate` gate hedge drift — a schema change should trigger a re-check.
- `addRecipeInformation=true` costs a few extra quota points per call — acceptable for low-volume
  personal use.
- **Unverified until the live smoke:** that `sort=popularity` is an accepted `sort` value (canonical in
  Spoonacular's docs; confirm on the first real call).

## Alternatives rejected / deferred
- **Richer / agentic web retrieval** (multiple sources, scraping, agentic search) — deferred to
  **[GH #10](https://github.com/gianca2020/pantry-pilot/issues/10)**; explicitly **out of v1 scope**.
  Spoonacular is the deliberate single clean source for v1.
- **A CLI command / `suggest --fetch` flag now** — deferred; tool-chaining is the Phase-4
  orchestrator's job, and keeps the WAT Tools/Agent boundary clean.
- **Raise on empty results** — rejected; empty is a valid deterministic outcome.
- **Building a new HTTP abstraction** — rejected; a single `httpx.get` behind one injected function is
  the simplest viable design (anti-overengineering §2).
