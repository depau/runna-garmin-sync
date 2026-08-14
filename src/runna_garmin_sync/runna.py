"""Runna client: Cognito auth, GraphQL reads, iCal change detection.

See docs/runna-api.md for the reverse-engineered API reference.
"""

import base64
import json
import logging
import re
import time

import requests

from .state import State

log = logging.getLogger(__name__)

COGNITO_URL = "https://cognito-idp.eu-west-1.amazonaws.com/"
CLIENT_ID = "3ge3jbid1uosi52ki4kjhrp747"
GRAPHQL_URL = "https://hydra.platform.runna.com/graphql"

AUTH_FILE = "runna_auth.json"

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


def _jwt_exp(token: str) -> float:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))["exp"]


class RunnaClient:
    def __init__(self, email: str, password: str, state: State):
        self.email = email
        self.password = password
        self.state = state
        self._id_token: str | None = None

    # -- auth ---------------------------------------------------------------

    def _cognito(self, flow: str, params: dict) -> dict:
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
        if r.status_code != 200:
            raise RunnaError(f"Cognito {flow} failed: {body.get('__type')}: {body.get('message')}")
        if "AuthenticationResult" not in body:
            # e.g. NEW_PASSWORD_REQUIRED / MFA challenge — needs manual handling
            raise RunnaError(f"Cognito {flow} returned challenge: {body.get('ChallengeName')}")
        return body["AuthenticationResult"]

    def _authenticate(self, force: bool = False) -> str:
        auth = self.state.load(AUTH_FILE, {})
        tok = auth.get("idToken")
        if not force and tok and _jwt_exp(tok) > time.time() + 300:
            return tok
        if auth.get("refreshToken"):
            try:
                res = self._cognito("REFRESH_TOKEN_AUTH", {"REFRESH_TOKEN": auth["refreshToken"]})
                auth["idToken"] = res["IdToken"]
                self.state.save(AUTH_FILE, auth)
                log.info("Runna: refreshed idToken")
                return auth["idToken"]
            except RunnaError as e:
                log.warning("Runna: refresh failed (%s), falling back to password", e)
        if not self.email or not self.password:
            raise RunnaError(
                "no valid Runna session and no credentials; run `runna-garmin-sync login` "
                "or set RUNNA_EMAIL/RUNNA_PASSWORD"
            )
        res = self._cognito("USER_PASSWORD_AUTH", {"USERNAME": self.email, "PASSWORD": self.password})
        auth = {"idToken": res["IdToken"], "refreshToken": res["RefreshToken"]}
        self.state.save(AUTH_FILE, auth)
        log.info("Runna: logged in with password")
        return auth["idToken"]

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

    def strength_day_ids(self, max_weeks: int = 60) -> list[str]:
        """Walk plan weeks and collect all DayStrength ids."""
        ids: list[str] = []
        empty_streak = 0
        for w in range(max_weeks):
            try:
                data = self.gql(WEEK_QUERY, {"weekIndex": w})
                days = ((data.get("getActiveOrderWeek") or {}).get("week") or {}).get("days") or []
            except RunnaError as e:
                log.debug("week %d: %s", w, e)
                days = []
            strength = [d["id"] for d in days if d.get("__typename") == "DayStrength"]
            ids.extend(strength)
            empty_streak = 0 if days else empty_streak + 1
            if empty_streak >= 4:  # ponytail: plan length unknown; 4 empty weeks = past the end
                break
        return ids

    def get_workout(self, workout_id: str) -> dict:
        day = self.gql(DETAIL_QUERY, {"workoutId": workout_id})["getWorkout"]
        if not day or day.get("__typename") != "DayStrength":
            raise RunnaError(f"{workout_id} is not a DayStrength")
        return day
