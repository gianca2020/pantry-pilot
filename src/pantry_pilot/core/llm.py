"""One place to build the Anthropic client, so every LLM-backed service shares it."""

from anthropic import Anthropic

from pantry_pilot.core.config import Settings


def get_client() -> Anthropic:
    """Return an Anthropic client.

    If ANTHROPIC_API_KEY is configured, pass it explicitly. Otherwise build a bare
    client and let the SDK resolve credentials itself — e.g. an OAuth login created
    by `ant auth login`, which uses your Claude subscription instead of API billing.
    """
    settings = Settings()
    if settings.anthropic_api_key:
        return Anthropic(api_key=settings.anthropic_api_key)
    return Anthropic()
