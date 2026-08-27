"""One place to build the Anthropic client, so every LLM-backed service shares it."""

from anthropic import Anthropic

from pantry_pilot.core.config import Settings


def get_client() -> Anthropic:
    """Return an Anthropic client built from settings; fail loudly if no key is set."""
    settings = Settings()
    if not settings.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Export it (export ANTHROPIC_API_KEY=sk-ant-...) "
            "or add it to a local .env file."
        )
    return Anthropic(api_key=settings.anthropic_api_key)
