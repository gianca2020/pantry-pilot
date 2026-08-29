# ADR 0006 — The LLM boundary: structured-output validation for recipe-query synthesis

**Status:** Superseded by [ADR 0007](0007-llm-boundary-via-claude-cli.md) — the boundary moved from the
Anthropic Messages API SDK to the Claude Code CLI (`claude -p`) on the subscription. The determinism
principle below still holds; only the *mechanism* changed.

## Context
Phase 2 adds PantryPilot's first LLM step. The determinism rule (CLAUDE.md) says persistent
state and schemas stay deterministic, and the LLM is used *only* for genuinely non-deterministic
work — here, synthesizing a recipe-search query from the pantry. Its output must be validated
before anything downstream trusts it. This also fulfils ADR 0005's deferred "separate schema for
untrusted output" — `RecipeQuery` is that schema.

Recipes will ultimately come from **Spoonacular** (a structured recipe API with a real
popularity/rating signal), not RAG or a raw web-scrape — so the synthesizer's output shape is the
query-shape of that API.

## Decision
- **LLM does one thing:** `synthesize_recipe_query(pantry, goal) -> RecipeQuery`. Reading the
  pantry, building the prompt, and printing are plain deterministic Python.
- **Validation at the boundary:** call `client.messages.parse(..., output_format=RecipeQuery)`,
  which constrains and validates the reply into a `RecipeQuery` instance before return.
- **Model:** `claude-opus-4-8`.
- **"Highly-rated" is deterministic, not the model's job:** it is a fixed `sort=popularity` applied
  in the Phase-2b retrieval call — never a field the LLM fills in.
- **Testability by injection:** the Anthropic client is a keyword-only parameter; tests pass a fake
  and never touch the network.
- **Failure handling:** `RecipeSynthesisError` on refusal / empty output; the CLI maps that plus
  missing-key (`RuntimeError`) and `anthropic.APIError` to a one-line message and exit code 1.

## Consequences
- Malformed or refused LLM output cannot reach downstream code — it is caught at the boundary.
- The whole feature is testable fully offline and deterministically.
- New surface area: the `anthropic` SDK dependency and the `ANTHROPIC_API_KEY` secret.
- `RecipeQuery` becomes the typed contract shared with Phase 2b (Spoonacular `complexSearch` params).

## Alternatives rejected / deferred
- **RAG** — wrong tool: it searches a private corpus you own, not the live web, and carries no
  rating signal. Deferred to a possible future "search my saved recipes" feature.
- **Agentic web-search tool** (let Claude search the web) — blurs synthesis and retrieval into one
  less-deterministic call. Rejected in favour of the clean LLM→validate→deterministic-API boundary.
- **Free-form LLM text (no schema)** — rejected; it violates the determinism rule outright.
- **Retrieval in Phase 2a** — deferred to Phase 2b to keep this slice to the single LLM boundary.
