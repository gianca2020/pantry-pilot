"""Invariants for the vetted recipe-source domain lists (source #2, §4.8)."""

from pantry_pilot.core.recipe_sources import ALLOW_DOMAINS, BLOCK_DOMAINS


def test_allow_and_block_are_disjoint() -> None:
    # A domain can't be both vetted and blocked — that would be an editing mistake.
    assert ALLOW_DOMAINS.isdisjoint(BLOCK_DOMAINS)


def test_domains_are_bare_lowercase_no_www() -> None:
    # _domain() produces bare, lowercased netlocs with no "www.", so the sets must match.
    for d in ALLOW_DOMAINS | BLOCK_DOMAINS:
        assert d == d.lower()
        assert not d.startswith("www.")
        assert "/" not in d and " " not in d


def test_known_members_present() -> None:
    assert "seriouseats.com" in ALLOW_DOMAINS
    assert "cooking.nytimes.com" in BLOCK_DOMAINS
