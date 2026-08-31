# Workflow 03 — Trending Recipe Retrieval ("what's hot right now")

**Stage:** Phase 2c &nbsp;·&nbsp; **Tool:** `services/trending.py` &nbsp;·&nbsp; **Transport:** `core/claude_web.py` &nbsp;·&nbsp; **Command:** *(none in v1)*

## Intent
Find recipes that are **genuinely trending right now** (roughly the last month) off vetted **free** web
sources, and validate each into a `Recipe` **with ingredients + steps**. Source #2 — an **agentic LLM**
step (contrast source #1's deterministic Spoonacular tool, ADR 0008). See ADR 0009 + the design doc.

## Trigger
No CLI command in v1. Invoked programmatically — `find_trending(query)` — by the future Phase-4
orchestrator, or by a fixed `TrendingQuery` in the live smoke. (A CLI / source-picker is the orchestrator's
job; keeps the WAT Tools/Agent boundary clean.)

## Inputs
- A **`TrendingQuery`** — all optional: `theme` (free-text, e.g. "chicken dinner"; empty = "what's hot
  overall"), `cuisine`, `meal_type`, `max_minutes`.
- An authenticated Claude Code CLI (subscription). **No `ANTHROPIC_API_KEY`** — it's scrubbed so billing
  can only hit the subscription ($0 marginal).
- Network access (the model uses `WebSearch`/`WebFetch`).

## Steps
1. *(deterministic)* `_to_search_terms(query, month=…)` builds the web-search string (e.g.
   `"best chicken dinner trending 2026-08"`); `month` is injected so tests are deterministic.
2. *(deterministic)* `_persona()` builds the system prompt: find genuinely-trending recipes, use ONLY the
   allow-listed free sites for `source_url`, never paywalled sites, copy ingredients + numbered steps
   **verbatim**, always include `source_url`, **do NOT output an `id`**, omit anything unreadable.
3. *(transport)* the injected fetcher (`run_claude_web`) runs `claude -p` with web tools ON
   (`--tools "WebSearch,WebFetch"` + `--allowedTools "WebSearch WebFetch"`), the prompt on stdin, the
   `TrendingResults` JSON schema, 180 s timeout; maps failures to `ClaudeCliError`.
4. *(validation gate — the sacred boundary)* `_parse_trending(envelope)` reads `structured_output`
   (fallback: the `result` JSON-string), then `TrendingResults.model_validate` → `list[Recipe]`.
5. *(deterministic allow-gate)* `_filter` keeps a recipe only if it has `source_url` **and** `steps`
   **and** its `_domain(source_url)` is in `ALLOW_DOMAINS`.
6. Returns `list[Recipe]` (empty is fine).

## Output
A `list[Recipe]` — each with `title`, `source_url`, verbatim `ingredients` + `steps`, and (when present)
`ready_minutes`/`servings`/`image`. `id` is `None` (web has none; identity = `source_url`).

## Determinism boundary
The web search/extraction is the one non-deterministic step; **everything app state relies on is
deterministic**: `TrendingResults.model_validate` (schema gate) + `_filter` (allow-list gate). The persona
*steers* the model to vetted sources; `_filter` *enforces* it.

## Edge cases / failure modes
| Situation | Detection | Result |
|---|---|---|
| Nothing trending / nothing allow-listed survives | `_filter` empties | `[]` — **not** an error |
| Agentic call times out (>180 s) / binary missing | `subprocess` | `ClaudeCliError` (`timeout`/`not_found`) |
| Not authenticated / rate-limited | non-zero exit + stderr | `ClaudeCliError` (`auth`/`quota`) |
| `is_error` envelope | `_parse_trending` | `TrendingRecipeError` (`llm_failed`) |
| Non-JSON / unschematic / bad payload | `_parse_trending` | `TrendingRecipeError` (`bad_output`) |
| Page blocks the fetch (bot-wall) | model skips it | omitted; if all fail → `[]` |
| Fabricated-but-schema-valid recipe / off-page paraphrase | — | **NOT** code-caught — grading only (design §5) |

## Tests
- `tests/test_claude_web.py` — offline, fakes `subprocess.run`: argv turns web tools on
  (`--tools`/`--allowedTools`), prompt on stdin, `ANTHROPIC_API_KEY` scrubbed, 180 s timeout, each failure
  → the shared `ClaudeCliError.kind`.
- `tests/test_trending.py` — offline, injects a fake `ClaudeRunner` + saved fixture
  (`tests/fixtures/trending_results.json`): `_to_search_terms` full/empty, `_persona` invariants,
  `_parse_trending` (structured_output + result fallback + `is_error`→`llm_failed` + non-JSON/unschematic
  →`bad_output`), `_filter` drops non-allow-listed **and** steps-less, `find_trending` end-to-end + empty
  →`[]` + transport-error propagation.
- `tests/test_recipe_sources.py` — allow/block disjoint, bare-lowercase-no-`www.` invariants.

## Build spikes / live smoke
- **Build spikes (read-only, 2026-08-30):** confirmed the web-tool flag combo on the installed CLI (the
  design's single `--tools "WebSearch WebFetch"` did NOT enable web; needs comma-list + `--allowedTools`);
  measured ~120 s / ~14 turns / $0 real; graded real output vs design §5 (GOOD).
- **Live smoke ✅ (2026-08-30):** `find_trending(TrendingQuery(theme="chicken dinner"))` → 3 validated
  `Recipe`s with real steps from allow-listed sources (pinchofyum ×2, halfbakedharvest); ~108 s / $0. A
  WebFetch spot-check confirmed one URL exists with byte-identical steps. GOOD vs the design §5 rubric.
