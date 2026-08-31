# ADR 0009 — Source #2 "what's hot right now": agentic web recipe retrieval (LLM)

**Status:** Accepted &nbsp;·&nbsp; **Phase:** 2c &nbsp;·&nbsp; **Design:** `docs/design/trending-recipe-source.md` + `workflows/03-trending-retrieval.md`

## Context
Source #1 (Spoonacular, [ADR 0008](0008-recipe-retrieval-spoonacular.md)) is fast/free/offline but a
static catalog ranked by an internal score — it has no idea what's *trending now*. Source #2 adds a
second recipe source that finds currently-popular dishes off the **live web** and validates them into
`Recipe`s **with ingredients + steps**. This fuses the deferred "richer/agentic retrieval" (GH #10) with
the planned Phase-3 LLM parsing into one agentic step. Full design + eval rubric live in the design doc;
this ADR records the decisions.

## Decision
- **Parallel entry point, not a reuse of `find_recipes`:** `services/trending.find_trending(query, *,
  month=None, fetcher=None) -> list[Recipe]`. Only the **output type** (`list[Recipe]`) is shared with
  source #1 (D1).
- **Boundary = web-enabled `claude -p` (D1):** new transport `core/claude_web.run_claude_web` reuses the
  `ClaudeRunner` Protocol + `ClaudeCliError` + `_scrubbed_env`/`_repo_root` from `core/claude_cli.py`; the
  subprocess + failure→`.kind` body is shared byte-identically via extracted `_invoke_claude`. Only the
  argv differs. **Subscription-billed = zero extra $.**
- **Web-tool flag (build-spike–confirmed):** `--tools "WebSearch,WebFetch"` (availability) **plus**
  `--allowedTools "WebSearch WebFetch"` (headless auto-approval). `--tools` alone leaves the tools
  auto-denied in `-p` mode. `CLAUDE_WEB_TIMEOUT_S = 180` (agentic, multi-turn; measured ~120 s).
- **One shared `Recipe` (D2):** `id` becomes optional (**identity = `source_url`**); adds optional
  `ingredients`/`steps` (None for Spoonacular). Plain Pydantic → no migration. Model emits `Recipe` minus
  `id`; the persona forbids an `id`.
- **Own input `TrendingQuery` (D3):** free-text `theme` + optional `cuisine`/`meal_type`/`max_minutes`;
  empty = "what's hot overall". `_to_search_terms` builds the search string (month injected for testable
  determinism); domain steering lives in the **persona**, not the query.
- **Two-class error taxonomy:** transport failures reuse `ClaudeCliError(.kind)`; content/validation
  failures raise `TrendingRecipeError(.kind ∈ {"llm_failed","bad_output"})`. **Empty is NOT an error → `[]`.**
- **Allow-list is the deterministic gate:** the **persona steers** the model to vetted free sites; the
  code **`_filter` enforces** `ALLOW_DOMAINS` (`core/recipe_sources.py`) — a recipe survives only if it has
  `source_url` + `steps` **and** its domain is allow-listed. `BLOCK_DOMAINS` is a persona steering hint
  (the famous paywalls); `_filter` needs only the allow-check. Config-driven + trivially extensible.
- **Determinism gate:** `_parse_trending` reads `structured_output` (fallback: the `result` JSON-string),
  then `TrendingResults.model_validate` — no un-validated web JSON reaches app state (CLAUDE.md rule).
- **Freshness (D4):** persona asks for "trending ~the last month" (graded, not code-enforced; relax to
  ~3 mo if too sparse).

## Consequences
- Real, currently-trending recipes with honest copied ingredients + steps, credited by `source_url`,
  **fully offline-testable** (injected fake `ClaudeRunner` + saved fixture; nothing hits the network).
- Schema-validity ≠ correctness: a fabricated-but-schema-valid recipe or an off-page paraphrase is **not**
  caught by code — that's a **grading-only** concern (design §5), verified in the live smoke.
- Slower/costlier per call than Spoonacular (agentic, ~2 min) but $0 marginal on the subscription.
- A good recipe from an *unlisted free* site is dropped — accepted for v1 (the list is config-driven).
- Depends on an external contract we don't own (the live web + the CLI's tool behavior); the flag combo is
  pinned from a build spike and a CLI change should trigger a re-check.

## Alternatives rejected / deferred
- **Reusing `find_recipes(RecipeQuery)`** — rejected; source #2's intent + I/O differ enough to warrant a
  parallel entry point behind the same output type.
- **TikTok / social VIDEO recipes** — deferred to **GH #12** (recipe trapped in video/audio/overlays;
  ToS + heavy multimodal).
- **Paywalled sources** (NYT Cooking, ATK, WaPo, Bon Appétit, Epicurious) — excluded; can't fetch full
  text without a logged-in session. Promote a `BLOCK`→`ALLOW` only alongside auth wiring.
- **A CLI command / source-picker** — deferred to the Phase-4 orchestrator (WAT Agent layer); `find_trending`
  stays a pure Tool. Live smoke uses a throwaway fixed `TrendingQuery`.
- **`--json-schema` post-processing to strip `id`** — unneeded; `id` is optional and the persona forbids it.
