"""Auth state machine: refresh-token rotation, mint-time reporting, and telling a dead
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
real_time = time.time
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
    c, calls = client(state, {"idToken": VALID, "refreshToken": "r0", "refreshedAt": time.time()}, [])
    assert c._authenticate() == VALID
    assert calls == []


def test_valid_token_is_refreshed_early_once_the_refresh_token_is_due(state):
    """The whole point: exercise the refresh token on its own ~24h clock, not the idToken's."""
    c, calls = client(
        state,
        {"idToken": VALID, "refreshToken": "r0", "refreshedAt": time.time() - 13 * 3600},
        [{"IdToken": VALID, "RefreshToken": "r1"}],
    )
    assert c._authenticate() == VALID
    assert calls == ["REFRESH_TOKEN_AUTH"]
    assert state.load(AUTH_FILE)["refreshToken"] == "r1"


def test_every_successful_refresh_records_refreshed_at_even_without_rotation(state):
    c, _ = client(state, {"idToken": EXPIRED, "refreshToken": "r0"}, [{"IdToken": VALID}])
    assert c._authenticate() == VALID
    saved = state.load(AUTH_FILE)
    assert saved["refreshedAt"] > 0  # so a non-rotating pool is not re-asked on every call
    assert "mintedAt" not in saved  # but the refresh token itself was never re-minted


def test_early_refresh_failure_falls_back_to_the_still_valid_cached_token(state):
    """A due-but-failed refresh must not throw away a session that still works."""
    for failure in (RunnaAuthInvalid("NotAuthorizedException"), RunnaError("TooManyRequestsException")):
        c, calls = client(
            state,
            {"idToken": VALID, "refreshToken": "r0", "refreshedAt": time.time() - 13 * 3600},
            [failure],
        )
        assert c._authenticate() == VALID
        assert calls == ["REFRESH_TOKEN_AUTH"]


def test_legacy_state_without_timestamps_still_works(state):
    """Pre-mintedAt state files are due immediately; a failed refresh must not break them."""
    c, calls = client(state, {"idToken": VALID, "refreshToken": "r0"}, [RunnaAuthInvalid("NotAuthorizedException")])
    assert c._authenticate() == VALID
    assert calls == ["REFRESH_TOKEN_AUTH"]


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


def test_dead_refresh_token_without_credentials_reports_reason_and_mint_time(state):
    minted = time.time() - 30 * 3600
    c, _ = client(
        state,
        {"idToken": EXPIRED, "refreshToken": "r0", "mintedAt": minted},
        [RunnaAuthInvalid("Cognito REFRESH_TOKEN_AUTH failed: NotAuthorizedException: Refresh Token has expired")],
    )
    with pytest.raises(RunnaError) as e:
        c._authenticate()
    msg = str(e.value)
    assert "Refresh Token has expired" in msg
    assert "minted " + time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(minted)) in msg
    assert "runna-garmin-sync login" in msg


def test_mint_time_is_reported_as_unknown_for_sessions_predating_minted_at(state):
    c, _ = client(state, {"idToken": EXPIRED, "refreshToken": "r0"}, [RunnaAuthInvalid("NotAuthorizedException")])
    with pytest.raises(RunnaError, match="mint time unknown"):
        c._authenticate()


def test_error_message_is_stable_over_time_so_the_daemon_can_dedup_it(state, monkeypatch):
    """The daemon dedups error notifications on the message text (__main__.py); a message
    carrying a relative age re-notified on every poll."""
    import runna_garmin_sync.runna as mod

    auth = {"idToken": EXPIRED, "refreshToken": "r0", "mintedAt": time.time() - 30 * 3600}
    seen = []
    for offset in (0, 7200):  # same failure, two hours apart
        monkeypatch.setattr(mod.time, "time", lambda o=offset: real_time() + o)
        c, _ = client(state, dict(auth), [RunnaAuthInvalid("NotAuthorizedException")])
        with pytest.raises(RunnaError) as e:
            c._authenticate()
        seen.append(str(e.value))
    assert seen[0] == seen[1]


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
