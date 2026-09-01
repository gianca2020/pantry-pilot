"""Per-step model-tier policy for the LLM pipeline (ADR 0012).

Latency (NOT cost — inference is flat-rate on the Max subscription): use the cheapest model
that handles each step; escalate only the genuinely-hard agentic step. Version-agnostic aliases.
"""

SYNTH_MODEL = "haiku"      # pantry -> RecipeQuery: constrained, schema-validated
RESOLVE_MODEL = "haiku"    # ingredient-line match: simple, and the slow N-serial-call stage
TRENDING_MODEL = "sonnet"  # agentic web search + verbatim extraction: the one hard step
