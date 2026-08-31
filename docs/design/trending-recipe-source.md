# Elephant Design — Source #2: "What's Hot Right Now" (agentic web recipe retrieval)

**Status: ACCEPTED 2026-08-30 — locked after 3 Goldfish passes (45% → 75% → 80%, then the reused transport plumbing embedded + `id` handling pinned; residual notes cosmetic). Design-of-record for source #2. Implementation is a SEPARATE gate (`writing-plans` → TDD) — no code from approval alone. Depends on Phase 2b (PR #11) for the `Recipe`/seam it extends.**
Follows the format of `docs/design/cli-llm-boundary.md`. Branch: `dev-feature-5-trending-source`.

> Vision: surface recipes that are **hot/modern right now**, not a static catalog. Self-sufficient by design.
> **All modules live under `src/pantry_pilot/`; imports are absolute** (`from pantry_pilot.core.claude_cli import …`).

---

## 1. Context
Phase 2b built a deterministic Spoonacular tool (**source #1**): fast/free/offline, but a food-blog catalog
ranked by an internal score — no idea what's trending *now*. This adds **source #2**: an agentic fetcher that
finds current/trending recipes off the live web and validates them into `Recipe`s. **Architectural decision:**
source #2 does NOT reuse `find_recipes(RecipeQuery, …)`; it is a **parallel** entry point
`find_trending(TrendingQuery, …) -> list[Recipe]`. Only the **output type (`list[Recipe]`) is shared.**

## 2. Ground truth — current code a fresh reader needs (verbatim)

Current `Recipe` (plain Pydantic `BaseModel`, NOT SQLModel) — `models/schemas.py`:
```python
class Recipe(BaseModel):
    id: int
    title: str
    image: str | None = None
    ready_minutes: int | None = Field(default=None, validation_alias="readyInMinutes")
    servings: int | None = None
    source_url: str | None = Field(default=None, validation_alias="sourceUrl")
```

Transport seam we reuse — `core/claude_cli.py`:
```python
CLAUDE_TIMEOUT_S = 120
ClaudeErrorKind = Literal["not_found","auth","quota","timeout","bad_output","failed"]
class ClaudeCliError(Exception):
    def __init__(self, message: str, *, kind: ClaudeErrorKind) -> None: super().__init__(message); self.kind = kind
class ClaudeRunner(Protocol):
    def __call__(self, prompt: str, schema: dict[str, object], *, system: str) -> dict[str, object]: ...
def _repo_root() -> Path:                 # walk up to the dir with pyproject.toml, else cwd
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file(): return parent
    return Path.cwd()
def _scrubbed_env() -> dict[str, str]:    # os.environ minus ANTHROPIC_API_KEY (billing guard)
    env = os.environ.copy(); env.pop("ANTHROPIC_API_KEY", None); return env
def run_claude(prompt, schema, *, system) -> dict:        # the EXACT failure→kind mapping, verbatim:
    try:
        result = subprocess.run(argv, input=prompt, capture_output=True, text=True,
            timeout=CLAUDE_TIMEOUT_S, cwd=_repo_root(), env=_scrubbed_env(), check=False)
    except FileNotFoundError as e:          raise ClaudeCliError("...", kind="not_found") from e
    except subprocess.TimeoutExpired as e:  raise ClaudeCliError("...", kind="timeout") from e
    if result.returncode != 0:
        low = (result.stderr or "").strip().lower()       # case-insensitive substring match; auth checked before quota
        if "auth" in low or "login" in low or "log in" in low:              raise ClaudeCliError("...", kind="auth")
        if "rate limit" in low or "quota" in low or "overloaded" in low:    raise ClaudeCliError("...", kind="quota")
        raise ClaudeCliError("...", kind="failed")
    try: envelope = json.loads(result.stdout)
    except json.JSONDecodeError as e:       raise ClaudeCliError("...", kind="bad_output") from e
    if not isinstance(envelope, dict):      raise ClaudeCliError("...", kind="bad_output")
    return envelope
```
`run_claude_web` reuses this body verbatim — only `argv` differs (§4.2). So the `.kind` mapping is byte-identical.
Envelope keys (`--output-format json`): `is_error` (bool), `structured_output` (schema-shaped object, primary),
`result` (JSON string mirror, fallback). **`run_claude`'s failure → `.kind` mapping (reused verbatim by `run_claude_web`):**

| Failure condition | `ClaudeCliError.kind` |
|---|---|
| `FileNotFoundError` (binary missing) | `not_found` |
| `subprocess.TimeoutExpired` | `timeout` |
| return≠0 & stderr has `auth`/`login` | `auth` |
| return≠0 & stderr has `rate limit`/`quota`/`overloaded` | `quota` |
| return≠0 otherwise | `failed` |
| stdout not JSON / not a dict | `bad_output` |

Parse+validate gate (`services/synthesizer.py`): `is_error`→raise; `structured_output` else `json.loads(result)`
(guard `JSONDecodeError`); `Model.model_validate(payload)` (`ValidationError`→raise). Tests inject a fake runner
returning a canned **envelope dict**; assert parsed result + error `.kind`. Nothing shells out.

## 3. Scope & non-goals (v1)
- **In:** `find_trending(TrendingQuery)` → recipes **trending ~the last month** from **vetted free/readable**
  sources, each validated into a `Recipe` **with ingredients + steps**.
- **Out (deferred):** TikTok/social **video** (GH #12); paywalled sources; retro UI / `.md` export;
  pantry-personalization; a CLI command / source-picker (Phase-4 orchestrator).

## 4. Target design

### 4.1 Modules (all under `src/pantry_pilot/`)
- `models/schemas.py` — add `TrendingQuery`, `TrendingResults`; **extend `Recipe`** (§4.4).
- `core/claude_web.py` — **NEW** transport `run_claude_web` (§4.2); **reuses** `ClaudeRunner`, `ClaudeCliError`,
  `_scrubbed_env`, `_repo_root` (imported from `claude_cli`).
- `core/recipe_sources.py` — the `ALLOW_DOMAINS` / `BLOCK_DOMAINS` constants (§4.8).
- `services/trending.py` — `find_trending`, `_to_search_terms`, `_persona`, `_parse_trending`, `_filter`,
  `_domain`, `TrendingRecipeError` (the conceptual core; author hand-writes).
- `tests/test_claude_web.py`, `tests/test_trending.py`, `tests/fixtures/trending_results.json`.

### 4.2 Transport `run_claude_web` — reuse the `ClaudeRunner` seam
The injected fetcher **is** a `ClaudeRunner` (same Protocol; no new interface). Only the argv differs from `run_claude`:
```python
CLAUDE_WEB_TIMEOUT_S = 180   # agentic: multiple tool turns

def run_claude_web(prompt: str, schema: dict[str, object], *, system: str) -> dict[str, object]:
    argv = ["claude","-p","--output-format","json","--json-schema", json.dumps(schema),
            "--model","opus","--append-system-prompt", system,
            "--tools","WebSearch,WebFetch",                     # tools AVAILABLE (comma-list)
            "--allowedTools","WebSearch WebFetch"]              # tools AUTO-APPROVED in headless -p
    # subprocess.run(argv, input=prompt, capture_output=True, text=True,
    #   timeout=CLAUDE_WEB_TIMEOUT_S, env=_scrubbed_env(), cwd=_repo_root(), check=False)
    # failure -> ClaudeCliError using the EXACT mapping table in §2; json.loads(stdout) -> envelope dict
```

**Build-spike findings (2026-08-30, confirmed on the installed CLI — implemented in `core/claude_web.py`):**
`--tools "WebSearch WebFetch"` (space-*joined*, one arg) does NOT enable web — the CLI reads it as a
single bogus tool name (0 web requests). And in headless `-p` mode `--tools` alone only makes tools
*available*; the permission layer still auto-DENIES them (still 0 web requests → empty results). The
confirmed-working combo is `--tools "WebSearch,WebFetch"` (comma-list) **plus**
`--allowedTools "WebSearch WebFetch"` (auto-approve). Measured: ~120 s, ~14 turns, **$0 real** on the
subscription (`ANTHROPIC_API_KEY` scrubbed); `CLAUDE_WEB_TIMEOUT_S = 180` is adequate. Claude Code's
`WebSearch`/`WebFetch` are **client-side** tools (never touch `server_tool_use`), which is why the
offline fake-`ClaudeRunner` test strategy is fully faithful.

### 4.3 Output JSON contract
```python
class TrendingResults(BaseModel):
    recipes: list[Recipe]
```
- Schema handed to the CLI = `TrendingResults.model_json_schema()` (an object top-level, as `--json-schema` needs).
- The LLM fills `Recipe` **minus `id`** (web has none → `None`; identity = `source_url`). The **persona explicitly
  tells the model not to output an `id`** (§4.6); since `id` is optional, validation passes and it stays `None`
  either way — no post-processing. `model_json_schema()` emits `Recipe`'s aliases, so the model emits
  `readyInMinutes`/`sourceUrl`; `model_validate` reads them.
- **`_parse_trending(envelope) -> list[Recipe]`** = the §2 gate: `is_error` → `TrendingRecipeError("...", kind="llm_failed")`;
  read `structured_output` else `json.loads(result)` (non-str or `JSONDecodeError` → `bad_output`);
  `TrendingResults.model_validate(payload)` (`ValidationError` → `bad_output`); return `results.recipes`.

### 4.4 `Recipe` schema evolution (D2)
- `id: int | None = None` (was `int`) — **identity becomes `source_url`.**
- add `ingredients: list[str] | None = None`, `steps: list[str] | None = None`.
- existing `title`, `image`, `ready_minutes`, `servings`, `source_url` unchanged. Plain Pydantic → no migration.
- **Phase-2b impact:** Spoonacular still sends `id`, so `test_retrieval.py` stays green; one assertion may touch
  the now-optional `id` type. Cooking-detail fields stay `None` for Spoonacular results.

### 4.5 Error taxonomy — mirror the two-class split
- **Transport** → reuse **`ClaudeCliError`** (`.kind` incl. `timeout`; mapping in §2).
- **Content/validation** → **`TrendingRecipeError`** in `services/trending.py`:
```python
TrendingErrorKind = Literal["llm_failed", "bad_output"]
class TrendingRecipeError(Exception):
    def __init__(self, message: str, *, kind: TrendingErrorKind) -> None: super().__init__(message); self.kind = kind
```
- **Empty is NOT an error** → `[]`.

### 4.6 `TrendingQuery`, `_to_search_terms`, `_persona`
```python
class TrendingQuery(BaseModel):        # own input, NOT RecipeQuery
    theme: str | None = None           # empty -> "what's hot overall"
    cuisine: str | None = None
    meal_type: str | None = None
    max_minutes: int | None = None

def _to_search_terms(query: TrendingQuery, *, month: str) -> str:   # month e.g. "2026-08", injected (testable)
    parts = [f"best {query.theme or 'recipes'}"]
    if query.cuisine:   parts.append(query.cuisine)
    if query.meal_type: parts.append(query.meal_type)
    parts.append(f"trending {month}")
    if query.max_minutes: parts.append(f"under {query.max_minutes} minutes")
    return " ".join(parts)

def _persona() -> str:                 # concrete string; domains comma-joined, sorted for determinism
    allow = ", ".join(sorted(ALLOW_DOMAINS))
    block = ", ".join(sorted(BLOCK_DOMAINS))
    return (
        "You find recipes that are genuinely popular or trending right now (roughly the last month). "
        f"Use ONLY these free, readable sites for source_url: {allow}. "
        f"NEVER use paywalled sites, especially: {block}. "
        "Copy the real ingredients and numbered steps from the page verbatim — do NOT invent or paraphrase. "
        "Always include the exact source_url; do NOT output an id. Omit any recipe you cannot read in full."
    )
```
`_to_search_terms(TrendingQuery(), month="2026-08")` → `"best recipes trending 2026-08"`. Domains live in the
persona, NOT the search string.

### 4.7 Entry point + the deterministic gate `_filter`
```python
def _domain(url: str) -> str:
    return urllib.parse.urlparse(url).netloc.removeprefix("www.")   # (suffix-matching is a later refinement)

def _filter(recipes: list[Recipe]) -> list[Recipe]:
    out: list[Recipe] = []
    for r in recipes:
        if not r.source_url or not r.steps:            # eval BAD #4: uncookable / uncreditable
            continue
        if _domain(r.source_url) not in ALLOW_DOMAINS:  # THE GATE: only vetted free/readable sources survive
            continue
        out.append(r)
    return out

def find_trending(
    query: TrendingQuery,
    *,
    month: str | None = None,
    fetcher: ClaudeRunner | None = None,
) -> list[Recipe]:
    fetcher = fetcher or run_claude_web                  # DI seam (mirrors synthesizer's `runner = runner or …`)
    month = month or date.today().strftime("%Y-%m")     # injectable for deterministic tests
    prompt = _to_search_terms(query, month=month)
    envelope = fetcher(prompt, TrendingResults.model_json_schema(), system=_persona())
    return _filter(_parse_trending(envelope))
```
**Allow-list vs block-list (resolves Goldfish B3):** `_filter` **enforces `ALLOW_DOMAINS`** — a recipe survives
only if its domain is on the vetted list. This is belt-and-suspenders: the **persona steers** the model to those
sites; **`_filter` enforces** it (same spirit as native `--json-schema` + our `model_validate`). Trade-off: a good
recipe from an *unlisted free* site is dropped — accepted for v1 because the list is **config-driven + trivially
extensible** (§4.8). `BLOCK_DOMAINS` is a **persona steering hint** (names the famous paywalls to avoid); `_filter`
needs only the allow-check, since anything not allowed (including any paywalled site) is already dropped.
Empty after filtering → `[]`.

### 4.8 Source constants — `core/recipe_sources.py`
```python
ALLOW_DOMAINS: frozenset[str] = frozenset({
    "seriouseats.com","cookwell.com","budgetbytes.com","cookieandkate.com","simplyrecipes.com",
    "thekitchn.com","onceuponachef.com","damndelicious.net","loveandlemons.com","minimalistbaker.com",
    "recipetineats.com","smittenkitchen.com","pinchofyum.com","halfbakedharvest.com","skinnytaste.com",
    "tasty.co","delish.com","allrecipes.com","foodnetwork.com","bbcgoodfood.com",
})
BLOCK_DOMAINS: frozenset[str] = frozenset({
    "cooking.nytimes.com","nytimes.com","americastestkitchen.com","cooksillustrated.com",
    "washingtonpost.com","bonappetit.com","epicurious.com",
})
```
Bare lowercased netlocs (no `www.`). Extensible: add a domain here to widen coverage; promote a `BLOCK` → `ALLOW`
only alongside auth wiring (paywalled sites need a logged-in session).

## 5. Eval criteria — write BEFORE code (§0A.3). Query: `TrendingQuery(theme="chicken dinner")`.
**GOOD**: real currently-popular dish; source_url on an **allow-listed** site; complete honest ingredients +
numbered steps copied (not invented); sane ready time.
1. `{title:"Creamy Garlic Parmesan Chicken", source_url:"https://www.seriouseats.com/...", ingredients:[8 real], steps:[6 real], readyInMinutes:30}`
2. `{title:"Bang Bang Chicken Bake", source_url:"https://budgetbytes.com/...", steps:[...]}`

**BAD** (+ where caught): (1) invented/paraphrased steps — *grading only*; (2) `source_url` on a non-allowed site
(e.g. nytimes.com) — ***`_filter` (allow-gate)***; (3) not trending ~a month — *grading*; (4) missing
`steps`/`source_url` — ***`_filter`***; (5) recipe doesn't exist at that URL — *grading*.
→ #1 and #5 are why **schema-validity ≠ correctness**: the build spike must grade real output against this list.

## 6. Failure modes / edge cases
| Situation | Handling |
|---|---|
| Nothing trending / nothing allow-listed survives | `_filter` empties → `[]` (not an error) |
| Agentic call times out / binary missing | `ClaudeCliError(kind="timeout"/"not_found")` (reused) |
| `is_error` envelope | `TrendingRecipeError(kind="llm_failed")` |
| Non-JSON / unschematic / bad payload | `TrendingRecipeError(kind="bad_output")` |
| Page blocks the fetch (bot-wall) | model skips it; if all fail → `[]` |
| Fabricated-but-schema-valid recipe | NOT caught by code — grading only (§5) |

## 7. Verification / testing (verification-left, offline)
- `tests/test_trending.py` — inject a fake `ClaudeRunner` returning a canned **envelope dict**. Fixture
  `trending_results.json` is the inner `structured_output` (a `TrendingResults`); the test wraps it as
  `{"is_error": False, "structured_output": <fixture>}`. Assert: N `Recipe`s with ingredients+steps; `_filter`
  drops a non-allow-listed `source_url` **and** a steps-less entry; empty results → `[]`; `is_error` → `llm_failed`;
  `{"result": "<non-JSON>"}` → `bad_output`; `_to_search_terms` full vs empty query (fixed `month="2026-08"` →
  `"best recipes trending 2026-08"`). Mirrors `test_synthesizer.py`/`test_retrieval.py`.
- `tests/test_claude_web.py` — monkeypatch `subprocess.run`; assert argv has `--tools "WebSearch WebFetch"`,
  prompt on stdin, `ANTHROPIC_API_KEY` scrubbed, and each failure → the §2 `ClaudeCliError.kind`.
- **Build spikes (read-only):** confirm the `--tools` web flag on the installed CLI; measure token cost/latency;
  grade real output vs §5.
- **Live smoke (final):** `find_trending(TrendingQuery(theme="chicken dinner"))` → validated `Recipe`s with real
  steps from allow-listed sources; graded against §5.

## 8. Decisions (resolved 2026-08-30)
- **D1** boundary = `run_claude_web` (web-enabled `claude -p`, zero-dollar; slower/agentic accepted).
- **D2** extend one `Recipe` (optional `ingredients`/`steps`; `id` optional; identity = `source_url`).
- **D3** own `TrendingQuery` (theme + optional filters; empty = "what's hot overall"). Input-UX default: free-text
  theme + filters — *pending final user nod.*
- **D4** freshness = trending ~last month (persona preference + graded in spike; relax to ~3 mo if too sparse).
- **Allow-list is the deterministic gate** (§4.7); sources config-driven + extensible (§4.8).

## 9. What approval authorizes
(1) Goldfish pass 3 on v3; (2) persist to `docs/design/trending-recipe-source.md` on `dev-feature-5-trending-source`;
(3) `writing-plans` → TDD build (author hand-writes the core — `_parse_trending` gate, `_filter`, `_to_search_terms`,
`_persona`, eval rubric; Claude writes plumbing — `run_claude_web`, `recipe_sources`, test scaffolding). No
implementation code from design-approval alone.
