"""Runna client: Cognito auth, GraphQL reads, iCal change detection.

See docs/runna-api.md for the reverse-engineered API reference.
"""

import base64
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from .state import State

log = logging.getLogger(__name__)

CACHE_FILE = "runna_cache.json"

COGNITO_URL = "https://cognito-idp.eu-west-1.amazonaws.com/"
# Mobile app client. The web SPA client (3ge3jbid1uosi52ki4kjhrp747) mints refresh tokens
# that live only ~24h and are never rotated, so a headless daemon dies daily; the mobile
# client is what the phone app relies on to stay logged in.
CLIENT_ID = "2lfq5ub9movh0sfr47g1dff0nd"
GRAPHQL_URL = "https://hydra.platform.runna.com/graphql"

AUTH_FILE = "runna_auth.json"
# Refresh the idToken on its own clock, not only when it expires: if the pool rotates
# refresh tokens, each early refresh persists the rotated one and extends the session.
REFRESH_EVERY = 12 * 3600

WEEK_QUERY = """query W($weekIndex: Int!) {
  getActiveOrderWeek(input: { weekIndex: $weekIndex }) {
    week { weekIndex days { __typename ... on DayStrength { id date } } }
  }
}"""

DETAIL_QUERY = """query D($workoutId: String) {
  getWorkout(input: { workoutId: $workoutId }) {
    __typename
    ... on DayStrength {
      id strengthType strengthTypeDisplay strengthTitle
      date day weekIndex duration durationFormatted completed skipped note
      scheduled24HourTime
      parts {
        id partSets partCoach partComment
        exercises {
          id exerciseId exerciseTitle
          exerciseRequires exerciseRequires2 exerciseIsUnilateral
          exerciseTip exerciseVideo exerciseWeight
          exerciseMuscleGroupBroad exerciseMuscleGroupSpecific
          timer note
          exerciseGrades { gradeType grades gradesV2 }
          mostRecentSet { weightKg }
        }
      }
    }
  }
}"""

ICAL_QUERY = "query { userProfile { id iCalendarUrl } }"


class RunnaError(Exception):
    pass


class RunnaAuthInvalid(RunnaError):
    """Cognito rejected the credential itself (NotAuthorizedException) — as opposed to a
    transient failure. Only this warrants falling back to a password login."""


def _jwt_exp(token: str) -> float:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))["exp"]


def _minted(minted_at: float | None) -> str:
    """When a stored token was minted. Absolute, not a relative age: the daemon dedups error
    notifications on the message text, so a value that drifts with the clock would re-notify
    on every poll. The log line's own timestamp still gives the age by subtraction."""
    if not minted_at:
        return "mint time unknown"
    return "minted " + time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(minted_at))


class RunnaClient:
    def __init__(self, email: str, password: str, state: State):
        self.email = email
        self.password = password
        self.state = state
        self._id_token: str | None = None

    # -- auth ---------------------------------------------------------------

    def _cognito(self, flow: str, params: dict) -> dict:
        try:
            r = requests.post(
                COGNITO_URL,
                headers={
                    "content-type": "application/x-amz-json-1.1",
                    "x-amz-target": "AWSCognitoIdentityProviderService.InitiateAuth",
                },
                json={"ClientId": CLIENT_ID, "AuthFlow": flow, "AuthParameters": params},
                timeout=30,
            )
            body = r.json()
        except (requests.RequestException, ValueError) as e:
            # network blip / non-JSON body: transient, not "the token is dead"
            raise RunnaError(f"Cognito {flow} unreachable: {type(e).__name__}: {e}") from e
        if r.status_code != 200:
            msg = f"Cognito {flow} failed: {body.get('__type')}: {body.get('message')}"
            # only NotAuthorizedException means the credential itself is bad; throttles and
            # 5xx are transient and must not be mistaken for a dead session
            raise (RunnaAuthInvalid if body.get("__type") == "NotAuthorizedException" else RunnaError)(msg)
        if "AuthenticationResult" not in body:
            # e.g. NEW_PASSWORD_REQUIRED / MFA challenge — needs manual handling
            raise RunnaError(f"Cognito {flow} returned challenge: {body.get('ChallengeName')}")
        return body["AuthenticationResult"]

    def _authenticate(self, force: bool = False) -> str:
        auth = self.state.load(AUTH_FILE, {})
        tok = auth.get("idToken")
        usable = not force and bool(tok) and _jwt_exp(tok) > time.time() + 300
        # refreshedAt tracks every successful refresh; mintedAt only advances when Cognito
        # hands back a new refresh token, so it still dates the refresh token itself.
        due = time.time() - auth.get("refreshedAt", auth.get("mintedAt", 0)) > REFRESH_EVERY
        if usable and not due:
            return tok
        reason = "no cached refresh token"
        if auth.get("refreshToken"):
            try:
                res = self._cognito("REFRESH_TOKEN_AUTH", {"REFRESH_TOKEN": auth["refreshToken"]})
                auth["idToken"] = res["IdToken"]
                auth["refreshedAt"] = time.time()
                if res.get("RefreshToken"):  # present only when the pool rotates refresh tokens
                    auth["refreshToken"] = res["RefreshToken"]
                    auth["mintedAt"] = time.time()
                self.state.save(AUTH_FILE, auth)
                log.info("Runna: refreshed idToken%s", " (rotated refresh token)" if res.get("RefreshToken") else "")
                return auth["idToken"]
            except RunnaAuthInvalid as e:
                reason = f"{e}, refresh token {_minted(auth.get('mintedAt'))}"
                log.warning("Runna: refresh failed (%s), falling back to password", reason)
            except RunnaError as e:
                if not usable:
                    raise  # transient, and no fallback session — let the caller retry next poll
                log.warning("Runna: early refresh failed (%s); cached idToken still valid", e)
        if usable:
            return tok  # the early refresh did not work out, but the cached token has life left
        if not self.email or not self.password:
            raise RunnaError(
                f"no valid Runna session ({reason}) and no credentials; "
                "run `runna-garmin-sync login` or set RUNNA_EMAIL/RUNNA_PASSWORD"
            )
        res = self._cognito("USER_PASSWORD_AUTH", {"USERNAME": self.email, "PASSWORD": self.password})
        auth |= {"idToken": res["IdToken"], "refreshToken": res["RefreshToken"], "mintedAt": time.time()}
        self.state.save(AUTH_FILE, auth)
        log.info("Runna: logged in with password")
        return auth["idToken"]

    def ensure_auth(self, force: bool = False) -> None:
        """Authenticate now; force=True mints a fresh idToken even if the cached one is valid."""
        self._id_token = self._authenticate(force)

    # -- GraphQL ------------------------------------------------------------

    def gql(self, query: str, variables: dict | None = None) -> dict:
        tok = self._id_token or self._authenticate()
        self._id_token = tok
        for attempt in (1, 2):
            r = requests.post(
                GRAPHQL_URL,
                headers={
                    "authorization": tok,  # raw JWT, no "Bearer"
                    "x-rb-platform-source": "rb-web",
                    "content-type": "application/json",
                },
                json={"query": query, "variables": variables or {}},
                timeout=60,
            )
            if r.status_code == 401 and attempt == 1:
                tok = self._id_token = self._authenticate(force=True)
                continue
            break
        if r.status_code != 200:
            raise RunnaError(f"GraphQL HTTP {r.status_code}: {r.text[:500]}")
        body = r.json()
        if body.get("errors"):
            raise RunnaError(f"GraphQL errors: {body['errors']}")
        return body["data"]

    # -- reads --------------------------------------------------------------

    def ical_url(self) -> str:
        return self.gql(ICAL_QUERY)["userProfile"]["iCalendarUrl"]

    def app_link_base(self) -> str | None:
        """Per-user Runna app-link base (e.g. https://club.runna.com/n9Tx/workout),
        harvested once from the iCal feed and cached."""
        auth = self.state.load(AUTH_FILE, {})
        if not auth.get("appLinkBase"):
            try:
                ics = requests.get(self.ical_url(), timeout=30).text
                m = re.search(r"https://club\.runna\.com/[^/\s]+/workout", ics)
                if not m:
                    return None
                auth = self.state.load(AUTH_FILE, {})
                auth["appLinkBase"] = m.group(0)
                self.state.save(AUTH_FILE, auth)
            except requests.RequestException as e:
                log.warning("could not harvest app link from iCal: %s", e)
                return None
        return auth["appLinkBase"]

    def ical_changed(self, url: str, etag: str | None) -> tuple[bool, str | None]:
        """Conditional GET on the calendar feed. Returns (changed, new_etag)."""
        headers = {"If-None-Match": etag} if etag else {}
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code == 304:
            return False, etag
        r.raise_for_status()
        return True, r.headers.get("ETag")

    def _week_day_ids(self, week_index: int) -> tuple[bool, list[str]]:
        """Returns (week_has_days, strength ids) for one week."""
        try:
            data = self.gql(WEEK_QUERY, {"weekIndex": week_index})
            days = ((data.get("getActiveOrderWeek") or {}).get("week") or {}).get("days") or []
        except RunnaError as e:
            log.debug("week %d: %s", week_index, e)
            days = []
        return bool(days), [d["id"] for d in days if d.get("__typename") == "DayStrength"]

    def strength_day_ids(self, max_weeks: int = 60) -> list[str]:
        """Walk plan weeks (parallel chunks) and collect all DayStrength ids."""
        self._id_token = self._authenticate()  # mint once, not from 8 threads at once
        ids: list[str] = []
        with ThreadPoolExecutor(max_workers=8) as ex:
            for start in range(0, max_weeks, 8):
                chunk = list(ex.map(self._week_day_ids, range(start, start + 8)))
                for _, week_ids in chunk:
                    ids.extend(week_ids)
                if not any(has_days for has_days, _ in chunk):  # a whole empty chunk = past the end
                    break
        return ids

    def get_workout(self, workout_id: str) -> dict:
        day = self.gql(DETAIL_QUERY, {"workoutId": workout_id})["getWorkout"]
        if not day or day.get("__typename") != "DayStrength":
            raise RunnaError(f"{workout_id} is not a DayStrength")
        return day

    def strength_days_cached(self, refresh: bool = False) -> list[dict]:
        """All strength days, cached in runna_cache.json keyed on the iCal ETag.

        A 304 on the calendar feed means the plan hasn't changed, so the cached
        payloads are reused (fast path: one conditional GET, zero GraphQL).
        `refresh=True` bypasses the cache — use periodically to pick up changes
        the iCal can't reflect (e.g. newly logged weights in mostRecentSet).
        """
        cache = self.state.load(CACHE_FILE, {})
        changed, etag = self.ical_changed(self.ical_url(), cache.get("etag"))
        if not refresh and not changed and cache.get("days") is not None:
            log.info("Runna: calendar unchanged, using cached plan (%d days)", len(cache["days"]))
            return cache["days"]
        self._id_token = self._authenticate()
        with ThreadPoolExecutor(max_workers=8) as ex:
            days = list(ex.map(self.get_workout, self.strength_day_ids()))
        self.state.save(CACHE_FILE, {"etag": etag, "days": days})
        return days
