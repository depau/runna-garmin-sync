"""Auth state machine: refresh-token rotation, token-age reporting, and telling a dead
refresh token apart from a transient Cognito failure."""

import base64
import json
import time

import pytest

from runna_garmin_sync.runna import AUTH_FILE, RunnaAuthInvalid, RunnaClient, RunnaError


def jwt(exp: float) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).rstrip(b"=").decode()
    return f"header.{payload}.sig"


EXPIRED, VALID = jwt(time.time() - 1), jwt(time.time() + 86400)
APP_LINK = "https://club.runna.com/n9Tx/workout"


def client(state, auth, responses, email=None, password=None):
    """RunnaClient with a stubbed _cognito that pops from `responses` (dict or exception)."""
    state.save(AUTH_FILE, auth)
    c = RunnaClient(email, password, state)
    calls = []

    def fake(flow, params):
        calls.append(flow)
        res = responses.pop(0)
        if isinstance(res, Exception):
            raise res
        return res

    c._cognito = fake
    return c, calls


def test_cached_token_is_reused_without_hitting_cognito(state):
    c, calls = client(state, {"idToken": VALID, "refreshToken": "r0"}, [])
    assert c._authenticate() == VALID
    assert calls == []


def test_rotated_refresh_token_is_persisted(state):
    c, calls = client(
        state,
        {"idToken": EXPIRED, "refreshToken": "r0", "appLinkBase": APP_LINK},
        [{"IdToken": VALID, "RefreshToken": "r1"}],
    )
    assert c._authenticate() == VALID
    assert calls == ["REFRESH_TOKEN_AUTH"]
    saved = state.load(AUTH_FILE)
    assert saved["refreshToken"] == "r1"
    assert saved["mintedAt"] > 0
    assert saved["appLinkBase"] == APP_LINK


def test_refresh_without_rotation_keeps_the_stored_token(state):
    c, _ = client(state, {"idToken": EXPIRED, "refreshToken": "r0"}, [{"IdToken": VALID}])
    assert c._authenticate() == VALID
    saved = state.load(AUTH_FILE)
    assert saved["refreshToken"] == "r0"
    assert "mintedAt" not in saved  # no rotation happened, so nothing was re-minted


def test_dead_refresh_token_without_credentials_reports_reason_and_age(state):
    c, _ = client(
        state,
        {"idToken": EXPIRED, "refreshToken": "r0", "mintedAt": time.time() - 30 * 3600},
        [RunnaAuthInvalid("Cognito REFRESH_TOKEN_AUTH failed: NotAuthorizedException: Refresh Token has expired")],
    )
    with pytest.raises(RunnaError) as e:
        c._authenticate()
    msg = str(e.value)
    assert "Refresh Token has expired" in msg
    assert "was 30.0h old" in msg
    assert "runna-garmin-sync login" in msg


def test_age_is_reported_as_unknown_for_sessions_predating_minted_at(state):
    c, _ = client(state, {"idToken": EXPIRED, "refreshToken": "r0"}, [RunnaAuthInvalid("NotAuthorizedException")])
    with pytest.raises(RunnaError, match="was of unknown age"):
        c._authenticate()


def test_transient_cognito_failure_propagates_instead_of_looking_like_a_dead_session(state):
    c, calls = client(
        state,
        {"idToken": EXPIRED, "refreshToken": "r0"},
        [RunnaError("Cognito REFRESH_TOKEN_AUTH failed: TooManyRequestsException: slow down")],
        email="a@b.c",
        password="pw",  # creds available, but a blip must not burn a password login either
    )
    with pytest.raises(RunnaError) as e:
        c._authenticate()
    assert "TooManyRequestsException" in str(e.value)
    assert "no credentials" not in str(e.value)
    assert calls == ["REFRESH_TOKEN_AUTH"]


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (400, {"__type": "NotAuthorizedException", "message": "Refresh Token has expired"}, RunnaAuthInvalid),
        (400, {"__type": "TooManyRequestsException", "message": "slow down"}, RunnaError),
        (503, {"__type": "InternalErrorException", "message": "boom"}, RunnaError),
    ],
)
def test_cognito_only_flags_notauthorized_as_invalid(state, monkeypatch, status, body, expected):
    import runna_garmin_sync.runna as mod

    resp = type("R", (), {"status_code": status, "json": lambda self: body})()
    monkeypatch.setattr(mod.requests, "post", lambda *a, **k: resp)
    with pytest.raises(expected) as e:
        RunnaClient(None, None, state)._cognito("REFRESH_TOKEN_AUTH", {})
    assert body["message"] in str(e.value)
    if expected is RunnaError:
        assert not isinstance(e.value, RunnaAuthInvalid)  # transient, must stay retryable


def test_cognito_network_failure_is_transient(state, monkeypatch):
    import runna_garmin_sync.runna as mod

    def boom(*a, **k):
        raise mod.requests.ConnectionError("dns")

    monkeypatch.setattr(mod.requests, "post", boom)
    with pytest.raises(RunnaError) as e:
        RunnaClient(None, None, state)._cognito("REFRESH_TOKEN_AUTH", {})
    assert not isinstance(e.value, RunnaAuthInvalid)
    assert "unreachable" in str(e.value)


def test_password_relogin_preserves_app_link_base(state):
    c, calls = client(
        state,
        {"idToken": EXPIRED, "refreshToken": "r0", "appLinkBase": APP_LINK},
        [RunnaAuthInvalid("NotAuthorizedException"), {"IdToken": VALID, "RefreshToken": "r9"}],
        email="a@b.c",
        password="pw",
    )
    assert c._authenticate() == VALID
    assert calls == ["REFRESH_TOKEN_AUTH", "USER_PASSWORD_AUTH"]
    saved = state.load(AUTH_FILE)
    assert saved["appLinkBase"] == APP_LINK
    assert saved["refreshToken"] == "r9"
    assert saved["mintedAt"] > 0
