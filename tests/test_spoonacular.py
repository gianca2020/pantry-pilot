"""Tests for the Spoonacular transport seam (fakes httpx.get; never hits the network).

Mirrors tests/test_claude_cli.py: the runner owns transport (build request, call the
network, map failures to a typed error), so we fake the network and assert on argv/params,
the injected secret, and each error `.kind`.
"""

import json

import httpx
import pytest

from pantry_pilot.core import spoonacular
from pantry_pilot.core.spoonacular import SpoonacularError, fetch_recipes

# A representative caller params dict (what services/retrieval._query_to_params will build).
_PARAMS: dict[str, str] = {"includeIngredients": "chicken", "sort": "popularity"}
# A minimal happy-path complexSearch body.
_OK_BODY: dict[str, object] = {"results": [{"id": 1, "title": "Garlic Chicken"}], "totalResults": 1}


class _FakeGet:
    """Records the httpx.get call and returns a canned Response (or raises)."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        json_body: object = None,
        text_body: str | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.urls: list[str] = []
        self.params_seen: list[dict[str, object]] = []
        self._status = status_code
        self._json = json_body
        self._text = text_body
        self._raises = raises

    def __call__(self, url: str, **kwargs: object) -> httpx.Response:
        self.urls.append(url)
        params = kwargs.get("params")
        assert isinstance(params, dict)  # transport always passes params=
        self.params_seen.append(params)
        if self._raises is not None:
            raise self._raises
        if self._text is not None:
            return httpx.Response(self._status, text=self._text)
        return httpx.Response(self._status, json=self._json)


def _install(monkeypatch: pytest.MonkeyPatch, fake: _FakeGet, *, key: str = "test-key") -> None:
    # Explicit env var beats any local .env, so tests are hermetic.
    monkeypatch.setenv("PANTRY_SPOONACULAR_API_KEY", key)
    # Patch httpx.get on the module object spoonacular calls (same import).
    monkeypatch.setattr(httpx, "get", fake)


def test_returns_parsed_body_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeGet(json_body=_OK_BODY)
    _install(monkeypatch, fake)
    body = fetch_recipes(_PARAMS)
    assert body["results"] == [{"id": 1, "title": "Garlic Chicken"}]


def test_injects_api_key_and_forwards_caller_params(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeGet(json_body=_OK_BODY)
    _install(monkeypatch, fake, key="secret-key")
    fetch_recipes(_PARAMS)
    assert fake.urls[0] == spoonacular.COMPLEX_SEARCH_URL
    sent = fake.params_seen[0]
    assert sent["apiKey"] == "secret-key"           # secret injected by transport
    assert sent["includeIngredients"] == "chicken"  # caller params forwarded verbatim
    assert sent["sort"] == "popularity"


def test_blank_key_raises_auth_without_calling_network(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeGet(json_body=_OK_BODY)
    _install(monkeypatch, fake, key="")
    with pytest.raises(SpoonacularError) as exc:
        fetch_recipes(_PARAMS)
    assert exc.value.kind == "auth"
    assert fake.urls == []  # short-circuits before any request


@pytest.mark.parametrize(
    ("status", "expected_kind"),
    [(401, "auth"), (402, "quota"), (429, "rate_limit"), (500, "http_error")],
)
def test_http_status_maps_to_kind(
    monkeypatch: pytest.MonkeyPatch, status: int, expected_kind: str
) -> None:
    fake = _FakeGet(status_code=status, json_body={"message": "nope"})
    _install(monkeypatch, fake)
    with pytest.raises(SpoonacularError) as exc:
        fetch_recipes(_PARAMS)
    assert exc.value.kind == expected_kind


def test_timeout_maps_to_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeGet(raises=httpx.TimeoutException("slow"))
    _install(monkeypatch, fake)
    with pytest.raises(SpoonacularError) as exc:
        fetch_recipes(_PARAMS)
    assert exc.value.kind == "timeout"


def test_connection_error_maps_to_network(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeGet(raises=httpx.ConnectError("no route to host"))
    _install(monkeypatch, fake)
    with pytest.raises(SpoonacularError) as exc:
        fetch_recipes(_PARAMS)
    assert exc.value.kind == "network"


def test_non_json_body_raises_bad_output(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeGet(text_body="upstream 502 gateway error")  # HTTP 200 but not JSON
    _install(monkeypatch, fake)
    with pytest.raises(SpoonacularError) as exc:
        fetch_recipes(_PARAMS)
    assert exc.value.kind == "bad_output"


def test_json_body_that_is_not_an_object_raises_bad_output(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeGet(json_body=[1, 2, 3])  # valid JSON, but a list, not the expected object
    _install(monkeypatch, fake)
    with pytest.raises(SpoonacularError) as exc:
        fetch_recipes(_PARAMS)
    assert exc.value.kind == "bad_output"


def test_json_decode_error_is_the_stdlib_one() -> None:
    # Guard: fetch_recipes catches json.JSONDecodeError (what httpx's .json() raises).
    assert issubclass(json.JSONDecodeError, ValueError)
