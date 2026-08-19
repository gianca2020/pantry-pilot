"""Smoke test: the package imports and advertises a version.

This is the smallest possible "is the wiring correct?" test. If it fails,
nothing else can pass — so it's the first thing we make green. It also proves
the src-layout package is importable under `uv run pytest`.
"""

import pantry_pilot


def test_package_exposes_version() -> None:
    assert pantry_pilot.__version__ == "0.1.0"
