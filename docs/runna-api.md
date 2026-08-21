# Runna API — clean-room reference for the strength-sync adapter

**Purpose.** Everything needed to read a Runna user's **strength/mobility
sessions** from Runna's private GraphQL API and map them to Garmin Connect
strength workouts. Written to be self-contained: an agent with no prior context
can build the extractor from this file alone.

**Provenance.** Reverse-engineered from the Runna Android app
`com.runbuddy.prod` v8.42.0 (Expo/React Native, Hermes bundle) and **verified
live** against `web.runna.com` / `hydra.platform.runna.com` on 2026-08-14.
Statements are verified against real responses unless marked `[?]` (inferred /
unconfirmed).

**Scope / ethics.** Personal use. Reverse-engineering Runna's API is against
their ToS — do not publish this as a service or share account data. Runna
intentionally does **not** sync non-running activities (strength/yoga/pilates)
to Garmin; this adapter fills that gap for one's own account using Garmin's
internal workout API (documented separately).

**Companion files in this folder**
- [`runna-exercise-ids.txt`](./runna-exercise-ids.txt) — the full universe of **261**
  `exerciseId` values (harvested from CDN asset names in the app bundle).
- [`runna-exercise-catalog.json`](./runna-exercise-catalog.json) /
  [`.csv`](./runna-exercise-catalog.csv) — **102** exercises with full metadata,
  harvested live across `LEGS_AND_CORE` + `FULL_BODY`. JSON has everything (incl.
  localized description/tip); CSV is the flat sheet.
- [`garmin-exercises.json`](./garmin-exercises.json) / [`.csv`](./garmin-exercises.csv)
  — Garmin's 1527 FIT strength exercises / 47 categories (target side of the mapping).
- [`runna-garmin-mapping.csv`](../src/runna_garmin_sync/runna-garmin-mapping.csv) + [`mapping.md`](./mapping.md)
  + [`build-mapping.py`](./build-mapping.py) — the Runna→Garmin exercise mapping
  (all 261, confidence-flagged) and how it's built.
- [`runna-graphql-operations.graphql`](./runna-graphql-operations.graphql) — **542**
  GraphQL operations/fragments extracted from the app bundle (218 query, 162 mutation,
  4 subscription, 158 fragment; reference for going beyond strength; input-type shapes
  not included — introspection is disabled).

---

## 0. TL;DR quickstart

```
POST https://hydra.platform.runna.com/graphql
Headers:
  authorization: <Cognito idToken JWT>     # raw, NO "Bearer " prefix
  x-rb-platform-source: rb-web
  content-type: application/json
Body: {"query": "...", "variables": {...}}
```

Read a strength workout in two calls (send your own minimal queries):

1. `getActiveOrderWeek(input:{weekIndex})` → `week.days[]`, filter
   `__typename == "DayStrength"`, take each `id`.
2. `getWorkout(input:{workoutId: <that id>})` → `DayStrength` with
   `parts[] → exercises[]`.

The `id` equals the iCal feed's `dayId` =
`{activeOrder}_plan_week_{weekIndex}_{STRENGTH_TYPE}_{ordinal}`. Map each exercise
to Garmin by its **`exerciseId`** (stable English enum).

---

## 1. Stack (context)

| Layer | What |
|---|---|
| App shell | Expo (SDK 54, EAS project `f465c800-28e7-4ef9-b41e-4ac8ad865716`), React Native new-arch, Hermes |
| Data | **Apollo Client** → **AWS AppSync** GraphQL, fronted by CloudFront |
| Auth | **AWS Cognito User Pools** (Amplify v6), region `eu-west-1` |
| Media | YouTube (exercise videos), Mux HLS (guided mobility/pilates), `cdn.runna.com` (Lottie/SVG animations) |
| OTA | Expo Updates, channel `prod`, `https://u.expo.dev/f465c800-…` — the JS bundle can change without a store update, so **the schema may drift; re-extract when the app version bumps** |

No Flutter, no certificate pinning. The web client hits the same backend, which
is the cheapest place to obtain a token.

---

## 2. Transport & endpoints

| | Value |
|---|---|
| GraphQL (web) | `https://hydra.platform.runna.com/graphql` ← **use this** |
| GraphQL (mobile app config) | `https://iny4lydltrdhjcre5oro4zmsmq.appsync-api.eu-west-1.amazonaws.com/graphql` |
| Realtime (subscriptions) | `wss://iny4lydltrdhjcre5oro4zmsmq.appsync-realtime-api.eu-west-1.amazonaws.com/graphql` |
| Method / body | `POST`, `{"operationName"?, "query", "variables"}` |
| Region | `eu-west-1` |

`hydra.platform.runna.com` is a CloudFront custom domain in front of the same
AppSync API (responses carry `x-amzn-appsync-tokensconsumed`, `x-amzn-requestid`,
`via: …cloudfront.net`). Both hosts accept the same Cognito token.

**Required request headers** (only these are CORS-allowed from the browser —
`access-control-allow-headers` does **not** include `accept-language` or custom
`x-*locale` headers, so you cannot force response language via headers):

```
authorization: <idToken>            # raw JWT, no "Bearer"
x-rb-platform-source: rb-web        # web value; the app sends its own source
content-type: application/json
```

---

## 3. Authentication

**Scheme:** Cognito User Pools. The AppSync `authorization` header is the raw
Cognito **idToken** JWT (`token_use: "id"`), **no `Bearer` prefix**.

| Cognito config (not secret — ships in every client) | Value |
|---|---|
| Issuer | `https://cognito-idp.eu-west-1.amazonaws.com/eu-west-1_JnmTqFzZt` |
| User Pool ID | `eu-west-1_JnmTqFzZt` |
| App Client ID (`aud`) | `3ge3jbid1uosi52ki4kjhrp747` (public SPA client, **no** client secret) |
| Federated IdP | Strava (OIDC); email/password is the base login |
| idToken lifetime (observed) | **24h** (`exp − iat = 86400`) |
| Refresh-token lifetime (observed) | **web client: ~24h, never rotated** by `REFRESH_TOKEN_AUTH` — a headless session dies ~24h after the last password auth no matter how often the idToken is refreshed. Lifetimes are per app client: use the **mobile client** `2lfq5ub9movh0sfr47g1dff0nd` headlessly (the phone app relies on it to stay logged in; exact lifetime/rotation not yet measured). |

**Token storage on `web.runna.com`** (Amplify v6 cookieStorage — cookies, not
localStorage):

```
CognitoIdentityServiceProvider.3ge3jbid1uosi52ki4kjhrp747.LastAuthUser
CognitoIdentityServiceProvider.3ge3jbid1uosi52ki4kjhrp747.<username>.idToken
CognitoIdentityServiceProvider.3ge3jbid1uosi52ki4kjhrp747.<username>.accessToken
CognitoIdentityServiceProvider.3ge3jbid1uosi52ki4kjhrp747.<username>.refreshToken
```

Read the idToken in-page:
```js
const idToken = decodeURIComponent(
  document.cookie.match(/CognitoIdentityServiceProvider\.[^;]*\.idToken=([^;]+)/)[1]);
```

### 3.1 Getting a token for a **headless server** — USER_PASSWORD_AUTH (chosen approach)

Fully non-interactive: authenticate with email + password directly against Cognito,
no browser. **Verified enabled:** `InitiateAuth(USER_PASSWORD_AUTH)` on both app clients
returns `NotAuthorizedException: Incorrect username or password` for bad creds (not
"flow not enabled"), and works **without a `SECRET_HASH`** → both clients are public
(no secret) and `USER_PASSWORD_AUTH` is on.

```python
import boto3
c = boto3.client("cognito-idp", region_name="eu-west-1")          # no AWS creds needed
r = c.initiate_auth(
    ClientId="2lfq5ub9movh0sfr47g1dff0nd",                        # mobile client — the web client's refresh token dies in ~24h
    AuthFlow="USER_PASSWORD_AUTH",
    AuthParameters={"USERNAME": EMAIL, "PASSWORD": PASSWORD})
tok = r["AuthenticationResult"]
id_token, refresh_token = tok["IdToken"], tok["RefreshToken"]     # IdToken -> AppSync authorization header
# later, headless refresh (no password):
r = c.initiate_auth(ClientId="2lfq5ub9movh0sfr47g1dff0nd", AuthFlow="REFRESH_TOKEN_AUTH",
                    AuthParameters={"REFRESH_TOKEN": refresh_token})
id_token = r["AuthenticationResult"]["IdToken"]
```
Raw HTTP equivalent: `POST https://cognito-idp.eu-west-1.amazonaws.com/`,
`X-Amz-Target: AWSCognitoIdentityProviderService.InitiateAuth`,
`Content-Type: application/x-amz-json-1.1`, body as above. Store only the
`RefreshToken`; mint a fresh `IdToken` per run.

**Prerequisite — set a native password (one-time).** A Strava-federated account has no
Cognito password. Set one with the standard reset, delivered to the Strava-linked email:
```
c.forgot_password(ClientId="3ge3jbid1uosi52ki4kjhrp747", Username=EMAIL)          # emails a code
c.confirm_forgot_password(ClientId="3ge3jbid1uosi52ki4kjhrp747", Username=EMAIL,
                          ConfirmationCode=CODE, Password=NEW_PASSWORD)
```
> ✅ **Verified working** on a Strava-signup (federation-created) account: `forgot_password`
> to the Strava email delivered a code, `confirm_forgot_password` set a native password, and
> `USER_PASSWORD_AUTH` then authenticates headlessly. (Pure external-IdP users occasionally
> can't reset — if `forgot_password` errors on some account, fall back to §3.2. Possible
> first-sign-in follow-ups: a `NEW_PASSWORD_REQUIRED` challenge via `respond_to_auth_challenge`,
> or MFA.)

### 3.2 Fallbacks

- **Lift from browser** (manual, ~24h): log in to `web.runna.com`, read the `…idToken`
  cookie (above), send it as `authorization`. Good for one-off runs.
- **Auth-code + PKCE via Strava** (one interactive login, then refresh forever): drive
  `https://prod.auth.runna.com/oauth2/authorize?response_type=code&identity_provider=Strava&…`
  (see §3.3), capture the redirect `code`, exchange at `…/oauth2/token`
  (`grant_type=authorization_code`, `code_verifier`, no secret) → refresh token. Use this
  if the password reset can't set a native password.

### 3.3 Cognito Hosted UI facts (for §3.2)

- Custom domain: `https://prod.auth.runna.com` — endpoints `/oauth2/authorize`,
  `/oauth2/token`, `/oauth2/userInfo`, `/oauth2/revoke`, `/login`, `/logout`.
- Mobile app client: `2lfq5ub9movh0sfr47g1dff0nd` (public, PKCE), callback
  `com.runbuddy.prod://app/sso-strava`. Web client: `3ge3jbid1uosi52ki4kjhrp747`
  (`https://` callback — browser-followable, easier to script with Playwright).
- Federated IdPs via `identity_provider=` (e.g. `Strava`, also Google/Apple).
- `redirect_uri` is **exact-match enforced** against the client's registered callbacks
  (an unregistered URI → `redirect_mismatch`).
- **No device-code flow.** The pool's OIDC discovery
  (`…/eu-west-1_JnmTqFzZt/.well-known/openid-configuration`) lists only
  `response_types_supported: ["code","token"]` and **no `device_authorization_endpoint`** —
  Cognito does not implement RFC 8628, so a device/CLI grant is impossible regardless of config.

> **Security note (worth knowing, keep private):** the Runna idToken's custom
> claims embed the user's **Strava OAuth tokens** (`custom:strava_access_token`,
> `custom:strava_refresh_token`, `custom:strava_scopes`, `custom:strava_email`)
> and a nested `custom:id_token`. Anyone holding the Runna idToken also holds
> live Strava credentials. Treat the token as highly sensitive; never log or
> commit it.

---

## 4. GraphQL access patterns

You control the query — **send minimal queries**, don't reproduce the app's fat
fragments. Union day types are resolved with inline fragments on `__typename`.

**Day union members:** `Day` (run), `DayStrength`, `DayMobility`, `DayPilates`,
`DayStretch`, `Rest`.

### 4.1 Identify the plan and current position

```graphql
query Me {
  userProfile { id name activeOrder unitOfMeasurementV2 languageSettings { locale } }
  getActiveOrderDetails { id planId }   # [?] fields vary; probe as needed
}
```
`userProfile.activeOrder` is the plan UUID used in every workout id.
`languageSettings.locale` is what localizes all display strings (see §7).

### 4.2 List a week's days → workout ids

```graphql
query W($weekIndex: Int!) {
  getActiveOrderWeek(input: { weekIndex: $weekIndex }) {
    week {
      weekIndex
      days {
        __typename
        ... on DayStrength { id strengthType strengthTitle date day completed }
        ... on DayMobility { id }
        ... on DayPilates  { id }
      }
    }
  }
}
```
`weekIndex` is a plain 0-based int. Walk `0..N` to cover the plan (a marathon
plan had strength in weeks 2–11; empty/out-of-range weeks just return no days).

### 4.3 Workout id == iCal `dayId` (CONFIRMED)

```
{activeOrder}_plan_week_{weekIndex}_{STRENGTH_TYPE}_{ordinal}
e.g. ca16fe55-…-869c5d1471b3_plan_week_3_LEGS_AND_CORE_0
```
Byte-identical to the iCal feed's `dayId`, so an iCal `dayId` feeds straight into
`getWorkout`. Child ids extend it deterministically: `…_PART_1`,
`…_PART_1_EXERCISE_0`.

### 4.4 Fetch full strength detail

```graphql
query D($workoutId: String) {
  getWorkout(input: { workoutId: $workoutId }) {
    __typename
    ... on DayStrength {
      id strengthType strengthTypeV2 strengthTypeDisplay strengthTitle
      strengthPhase date day weekIndex duration durationFormatted completed skipped note
      parts {
        id partSets partCoach partComment
        exercises {
          id exerciseId exerciseTitle
          exerciseRequires exerciseRequires2 exerciseRequiresDisplay exerciseIsUnilateral
          exerciseDescription exerciseTip exerciseVideo exerciseLottie
          exerciseMuscleGroupBroad exerciseMuscleGroupSpecific
          timer note
          exerciseGrades { gradeType grades gradesV2 }
          mostRecentSet { weightKg }
        }
      }
    }
  }
}
```
(App's own equivalent: `query GetStrengthPartsOverview($workoutId: String)`.)

---

## 5. Strength data model (verified)

Shape: **`DayStrength` → `parts[]` (circuits) → `exercises[]`**. A `part` is a
circuit performed `partSets` times. Rest is a pseudo-exercise, not a field.

```jsonc
{
  "__typename": "DayStrength",
  "id": "{orderId}_plan_week_3_LEGS_AND_CORE_0",
  "strengthType": "LEGS_AND_CORE",          // enum, see §6
  "strengthTypeV2": "LEGS_AND_CORE",
  "strengthTypeDisplay": "Gambe e core",    // localized label
  "strengthTitle": "Forza di gambe e core", // localized
  "strengthPhase": "STRENGTH",
  "date": "2026-08-17",                     // ISO date (the scheduled day)
  "day": "MONDAY",                          // weekday enum
  "weekIndex": 3,
  "duration": [1500, 2100],                 // [min,max] SECONDS (a tuple, not scalar)
  "durationFormatted": "25 m - 35 m",
  "completed": null,                        // true once done
  "skipped": null,
  "note": null,
  "parts": [
    {
      "id": "…_PART_1",
      "partSets": 3,                        // circuit repeats 3× → Garmin numberOfIterations
      "partCoach": null,                    // optional coaching text for the block
      "partComment": null,
      "exercises": [
        {
          "id": "…_PART_1_EXERCISE_0",
          "exerciseId": "WALKING_LUNGE",    // STABLE English key — the mapping join key
          "exerciseTitle": "Affondi in camminata",   // localized display (do NOT match on this)
          "exerciseRequires": "BW",         // equipment code, §6
          "exerciseRequires2": null,        // secondary equipment
          "exerciseRequiresDisplay": "Peso corporeo", // localized equipment label
          "exerciseIsUnilateral": false,    // per-side movement (reps are per side)
          "exerciseDescription": "…",       // long localized how-to text
          "exerciseTip": "…",               // localized coaching cue
          "exerciseVideo": "vYfp2t4XgqQ",   // YouTube video ID, §9
          "exerciseLottie": "WALKING_LUNGE",// animation asset code (usually == exerciseId), §9
          "exerciseMuscleGroupBroad": "QUADS",       // §6
          "exerciseMuscleGroupSpecific": "BILATERAL_QUAD",
          "timer": null,                    // set only for TIMED_REST (seconds)
          "note": null,
          "exerciseGrades": {               // prescription, ONE ENTRY PER SET (length == partSets)
            "gradeType": "REPS",            // REPS | SECONDS
            "grades":   [6, 6, 6],          // number[] baseline
            "gradesV2": ["8-12","8-12","8-12"] // string[]; the displayed value, can be a RANGE
          },
          "mostRecentSet": { "weightKg": null }  // user's last logged load, else null
        },
        {
          "id": "…_PART_1_EXERCISE_2",
          "exerciseId": "TIMED_REST",       // REST = pseudo-exercise
          "exerciseTitle": "90 s di riposo",
          "timer": 90,                      // rest seconds
          "exerciseGrades": null,
          "mostRecentSet": null
        }
      ]
    }
  ]
}
```

### Field → Garmin encoding

| Concept | Runna field | Garmin |
|---|---|---|
| Schedule date | `DayStrength.date` | `/workout-service/schedule/{id}` |
| Workout name | `strengthTitle` | workout name |
| Est. duration | `duration` = `[min,max]` sec | info only |
| Circuit / block | `parts[]` | one `RepeatGroupDTO` per part |
| Rounds | `parts[].partSets` | `numberOfIterations` + `skipLastRestStep` |
| Exercise identity | **`exerciseId`** (English) | alias → `category` + `exerciseName` |
| Rep work | `gradeType:"REPS"` + `gradesV2[i]`/`grades[i]` | `endCondition reps` (id 10) |
| Time work | `gradeType:"SECONDS"` + `grades[i]` | `endCondition time` (id 2) |
| Rest | `exerciseId:"TIMED_REST"`, `timer` | `stepTypeId 5 "rest"` |
| Load (prescribed) | `exerciseWeight` = **intensity label** (Light/Moderate/Heavy, localized), not kg | put in step `description` |
| Load (actual) | `mostRecentSet.weightKg` (user's last logged kg, else null) | `weightValue` + `weightUnit` |
| Per side | `exerciseIsUnilateral` | reps are per side |
| Fallback text | `exerciseTitle`, `exerciseTip`, `note`, `partCoach` | step `description` |

**Reps encoding:** `grades` is a numeric baseline; `gradesV2` is what the user
sees and may be a **range** (`"8-12"`). Prefer `gradesV2`; when it's a range,
pick a policy (low end / midpoint) for Garmin's single `reps`. For `SECONDS` the
two agree (`grades:[40,40] == gradesV2:["40","40"]`).

**Weight:** `exerciseWeight` is a **localized qualitative intensity** — `""`
(bodyweight), or e.g. `"Moderato"`/`"Pesante"` (= Moderate/Heavy) for weighted
moves — **not a number**. The only numeric load is `mostRecentSet.weightKg`, the
user's last logged actual weight (null until they log one). So Garmin gets a
numeric weight only if the user has history; otherwise carry the intensity label
in the description.

**Other non-run day types** are *not* structured like strength:
- **`DayStretch`** (id `…_plan_week_{w}_STRETCH`, no ordinal): a single guided
  video. Fields: `id, day, date, duration ([sec] tuple), durationFormatted,
  completed, skipped, stretchTitle, stretchDescription, videoId`. **No exercise
  list, no reps** — `videoId` is a series label (e.g. `"Episode 1"`), not a
  YouTube id. Maps to Garmin at best as a timed generic session, not sets/reps.
- **`DayMobility`** / **`DayPilates`**: summaries carry `mobilityTitle`/
  `pilatesTitle`, `…Description`, `duration`, dates — also video-led (Mux HLS via
  `WarmCoolRoutine.muxUrl`), not structured strength. This account has none.

---

## 6. Enums & code tables

**`gradeType`** — `REPS`, `SECONDS`. (Rest carries no grade; it's `TIMED_REST`.)

**`strengthType` / `strengthTypeV2`** (session type). Confirmed live:
`LEGS_AND_CORE`, `FULL_BODY` (switching the plan's strength focus changes which
you get). `UPPER_BODY` seen in the bundle; full set not enumerable statically.
`strengthTypeDisplay` is the localized label. `strengthPhase` seen: `STRENGTH`.

**`day`** — `MONDAY … SUNDAY`.

**Equipment (`exerciseRequires`, primary; `exerciseRequires2`, secondary)** —
short codes, confirmed live: `BW` (bodyweight), `DB` (dumbbell), `BARBELL`,
`KB` (kettlebell), `BAND` (resistance band), `STEP`, `BOX`, `PUB` (pull-up bar),
`SWISSBALL`. `exerciseRequires2` seen: `BAND`, `BENCH`, `BOX`. Bundle also hints
at `WALL` `[?]`. `exerciseRequiresDisplay` is the localized label (e.g.
`BW`→"Peso corporeo", `DB`→"Manubri", `BARBELL`→"Bilanciere").

**`exerciseWeight`** (prescribed intensity, localized) — `""` (bodyweight),
`"Moderato"`, `"Pesante"` (= Moderate/Heavy); `"Leggero"` (Light) `[?]`. Server
enum likely `LIGHT/MODERATE/HEAVY` but returned localized.

**`exerciseMuscleGroupBroad`** (confirmed live): `WARM_UP`, `QUADS`, `GLUTES`,
`HAMSTRING`, `CALVES`, `CORE`, `CHEST`, `BACK`, `SHOULDERS`, `PLYOS`,
`FULL_BODY`, `EXTRA`. (`BICEPS`/`TRICEPS`/`ABDUCTORS` `[?]` — not yet observed;
triceps moves like `TRICEP_DIP` were tagged `CHEST`.)

**`exerciseMuscleGroupSpecific`** (confirmed live): pattern
`{BILATERAL|UNILATERAL}_{QUAD|GLUTE|HAM|CALF|CO|FB|CHEST|BACK|SHLD}`, plus
`PLYOS`, `EXTRA`, `WARMUP_EX_LOWER`, `WARMUP_EX_UPPER`. (`CO`=core, `FB`=full
body, `HAM`=hamstring, `SHLD`=shoulders.)

**Special `exerciseId`:** `TIMED_REST` = rest step (use `timer` seconds).

---

## 7. Localization — solved by `exerciseId`

`exerciseTitle`, `exerciseDescription`, `exerciseTip`, `strengthTitle`,
`strengthTypeDisplay`, `exerciseRequiresDisplay` are **localized to the account's
`languageSettings.locale`** (Italian in the sample). You **cannot** force
English via request headers (CORS blocks `accept-language`); changing it means
changing the user's profile locale — don't.

You don't need localized names: **`exerciseId` is a stable English
`UPPER_SNAKE_CASE` key** (`WALKING_LUNGE`, `STEP_UP`, `SINGLE_LEG_GLUTE_BRIDGE`).
Humanize it for the English name; map Garmin off it. The localized title is
reference only.

---

## 8. Exercise catalog & Garmin mapping strategy

### 8.1 What we have
- **Universe:** 261 `exerciseId`s (`runna-exercise-ids.txt`), from CDN asset
  names in the bundle — the complete list of movements Runna ships.
- **Verified metadata:** **102 exercises** (`runna-exercise-catalog.json` / `.csv`),
  harvested live across two strength focuses (`LEGS_AND_CORE` + `FULL_BODY`) —
  now covers bodyweight, band, dumbbell, barbell, kettlebell, box, swissball and
  pull-up-bar movements (legs, core, glutes, hams, calves, chest, back,
  shoulders, full-body, plyos). The remaining ~159 need plans that schedule them
  (no catalog endpoint exists — metadata only arrives embedded in a workout).

### 8.2 Mapping signals, in priority order
1. **`exerciseId`** → curated alias table → Garmin `{category, exerciseName}`.
   This is the primary key; build the table by hand/LLM against Garmin's FIT
   exercise enums (English only).
2. **`exerciseMuscleGroupBroad` / `Specific`** → when no exact Garmin match,
   pick a Garmin exercise in the same muscle category. Rough crosswalk:
   `QUADS→SQUAT/LUNGE`, `GLUTES→HIP_RAISE`, `HAMSTRING→CURL/HIP_RAISE`,
   `CALVES→CALF_RAISE`, `CORE→PLANK/CRUNCH`, `FULL_BODY→TOTAL_BODY`,
   `PLYOS→PLYO`, `WARM_UP→WARM_UP/CARDIO`.
3. **`exerciseRequires`** (equipment) → disambiguate candidates (e.g. `BW` vs
   `DUMBBELL` variants of a lunge).
4. **`exerciseIsUnilateral`** → pick the single-leg/arm Garmin variant, and treat
   reps as per-side.
5. **Degradation path:** no Garmin equivalent → omit `category` (Garmin rejects
   `OTHER`/`UNASSIGNED` with 400), leave `exerciseName` empty, and put the human
   name (`exerciseId` humanized + `exerciseTitle`) in the step **description**.
   Known no-FIT-equivalent movements: `HAMSTRING_WALKOUT`, `FLOATING_HEEL_DROP`,
   `DIAGONAL_TOE_TAP`, `HIP_DROP`, `FIRE_HYDRANTS`, `POGO_JUMPS`.

### 8.3 The catalog files

The exercise catalog lives in **`runna-exercise-catalog.json`** (full metadata,
including localized `description`/`tip`) and **`runna-exercise-catalog.csv`**
(flat mapping sheet). Per-exercise fields: `exerciseId, title_it, requires,
requires2, unilateral, muscleGroupBroad, muscleGroupSpecific, gradeTypes,
youtubeId, lottie, strengthTypes`. 102 rows so far; add a `garminCategory` /
`garminExerciseName` column there when building the mapping. Regenerate/extend
with the Appendix B harvester — it dedupes by `exerciseId` and merges.

---

## 9. Media assets

- **Exercise video** — `exerciseVideo` is a **YouTube video ID**. Watch:
  `https://www.youtube.com/watch?v={exerciseVideo}`; thumbnail
  `https://img.youtube.com/vi/{exerciseVideo}/hqdefault.jpg`. (The app embeds via
  `react-native-youtube-iframe`.)
- **Exercise animation** — `exerciseLottie` is an asset code (usually equal to
  `exerciseId`). Served from `cdn.runna.com` as `…/exerciseLotties/{code}.svg`
  (static) and `.lottie` (animated). **Exact base path `[?]`** — anonymous
  requests to the guessed paths return S3 `403`, so the app uses a signed URL or
  a base not recovered statically. Not needed for Garmin sync.
- **Guided mobility/pilates** — `WarmCoolRoutine` / mobility use `videoId`
  (YouTube) **and** `muxUrl` (Mux HLS `.m3u8` stream). `WarmCoolExerciseDetails`
  fields: `name, description, tip, videoId, lottieCode, unilateral`.

---

## 10. Idempotency & sync

- **Stable ids:** `DayStrength.id` (= `workoutId`) is deterministic from
  `{orderId, weekIndex, strengthType, ordinal}`; part/exercise ids are
  deterministic too. Use `id` as the adapter's Runna-side key.
- **Change signals:** `completed`, `skipped`, `date` on `DayStrength`. **No
  explicit `updatedAt`/version on `DayStrength`** `[?]` (the run `Day` type has
  `garminUpdatedAt`/`corosUpdatedAt`/`suuntoUpdatedAt`; strength does not).
  Detect edits by diffing the fetched payload against the last stored one.
- **Adapter state:** keep a local map `Runna workoutId → Garmin workoutId`. On
  re-poll: create new, update when content changed, delete when the day
  disappears or turns `skipped`. Runna rolls ~2 weeks ahead and mutates on plan
  changes, so poll and reconcile rather than one-shot.

### 10.1 Detecting plan changes (sync trigger)

**No real-time push exists.** The only AppSync subscriptions in the app are
`RealtimeUserLevelsPointsChanged`, `RealtimeLevelsPlanCompleted`,
`RealtimePaceInsightSubscription`, `RealtimeUserFeatureInteractionCreated` —
none fire on plan/workout edits. So detection is **poll-based**. Plans change
only a few times a day (weekly roll, edits, skips), so a 15–30 min poll is
"ASAP" enough — do **not** poll every minute.

**Primary trigger — `planVersion` (cheapest & most precise).**
```graphql
query PlanVersion { getActiveOrderDetails { id planId planVersion } }
```
`planVersion` is a small integer that bumps on structural plan edits (observed:
`60`). One tiny authed GQL call; when it changes, run the full sync (§4). It may
**not** bump for a single workout `completed`/`skipped` toggle — so still diff
per-workout `completed`/`skipped`/`date` during the sync pass (§10 above). A
cheap middle layer if needed: hash `GetWeekSummaries` (week list, no exercises).

**Auth-free alternative — the iCal feed.** Obtained via GraphQL:
```graphql
query Cal { userProfile { id iCalendarUrl } }   # also under calendarSyncing { iCalendarUrl }
```
Returns `https://cal.runna.com/{32-hex-token}.ics` — a **per-user, token-in-path
feed that needs no Cognito auth** (the token is a stable per-user secret; treat
it as one). Good if you'd rather not keep a token warm: poll the `.ics` with a conditional
`GET` and only spin up the authed GQL sync when it changes. **Verified:** the feed
is an S3 object behind CloudFront (`Server: AmazonS3`) that returns both
`ETag: "…"` and `Last-Modified` and **no `Cache-Control`**, so
`If-None-Match: <etag>` (or `If-Modified-Since`) yields `304 Not Modified` until
the plan changes, then `200` with a new ETag and body (`Last-Modified` tracks
regeneration). A 304 is a few bytes — poll it every few minutes for free.
Trade-off: the ICS is **coarser** (no reps/weights/load — see §5), so it's only a
*change signal*; the real data still comes from GQL. Given `planVersion` is
already a negligible call, either works — pick ICS if you want an auth-free
heartbeat, `planVersion` if you're already authed.

**Recommended loop:** refresh idToken (§3.1) → poll `planVersion` every ~15–30
min → on change, walk weeks (§4.2) + fetch `DayStrength` detail (§4.4) → map
(§8) → reconcile against Garmin (§10). Runna device-sync timestamps
(`garminUpdatedAt` etc.) are **run-only**; they don't reflect strength, so ignore
them for this adapter.

---

## 11. Other findings (not needed for sync, but discovered)

- **Integrations:** run `Day` carries `garminUpdatedAt`, `corosUpdatedAt`,
  `suuntoUpdatedAt` (device push state, running only). Queries
  `GetAppleFitnessWorkouts`, `GetAppleWatchWorkouts`; mutation
  `SyncActivityToStrava`. This confirms Garmin push exists for **runs** but not
  strength — the exact gap this project fills.
- **Subscription / billing:** `userProfile.subscriptionStatusV2` (`PREMIUM`),
  `subscriptionType` (`MONTHLY`), `subscriptionStore` (`PADDLE`). RevenueCat:
  `GET https://api.revenuecat.com/v1/subscribers/cognito%7C{sub}`. Stripe +
  Paddle both present.
- **Analytics / experiments:** Statsig (`experiments.platform.runna.com`, client
  key `client-sJrqn6vu0ap2sMcDYEalfBcAR32wxRUeGTXBw9PwRxR`), Snowplow, Sentry
  (`org runna`, `project rb-app`), AppsFlyer, Embrace, Mixpanel, VWO, plus an
  `ip-api.com` geolocation call. None gate the GraphQL API.
- **iCal feed:** per-user calendar (app → Connected Apps → Calendars). Gives
  date, title, ordered exercise names, block/set counts, estimated duration —
  but **no reps/weights** (those live in `exerciseGrades`, §5). Its `dayId` = the
  GraphQL `workoutId`. Useful as a lightweight fallback source.
- **Community:** spaces, reactions, comments, polls (many `Community*` types) —
  irrelevant here.
- **GraphQL surface:** ~567 operations in the bundle. Strength-relevant reads:
  `GetStrengthPartsOverview`, `GetWorkout`, `GetWeekForDayDetail`,
  `PrefetchDayDetail`, `GetWeekSummaries`, `GetCurrentPlanData`,
  `GetStrengthExerciseWeightHistory($exerciseId, $startDate, $endDate)` (past
  logged loads). Strength writes (adapter is read-only, reference only):
  `SaveStrengthActivity`, `UpdateStrength`, `UpdateStrengthExerciseNotes`.

---

## 12. Enumerating the full exercise library — dead-ends & the viable path

**Goal:** metadata (name, muscle group, equipment, video) for all 261
`exerciseId`s, not just the 102 an account happens to be scheduled.

**Confirmed dead-ends (do not re-investigate):**
- **No catalog / library / browse / swap / alternatives / substitute operation.**
  Runna strength is coach/algorithm-generated; the app exposes no exercise
  picker or custom-workout builder. The only strength root ops are
  `GetStrengthPartsOverview`, `GetStrengthExerciseWeightHistory` (takes an
  `exerciseId` but returns only weight history, no metadata),
  `SaveStrengthActivity`, `UpdateStrength`, `UpdateStrengthExerciseNotes`.
- **Introspection disabled.** `__schema` → WAF `403 Forbidden`; `__type{…}` →
  AppSync strips the `__Type` fields (`FieldUndefined` validation errors). Raw
  AppSync host also 403s. No schema/enum dump obtainable.
- **"Instant workouts", recommendations, "additional workouts"** are all
  *running* content, not a strength library.
- Metadata only arrives embedded in a scheduled `DayStrength`; coverage is capped
  by what the account's plan schedules (leg/core-only for a running plan).

**Viable paths, in order of preference:**
1. **Derive from `exerciseId`** (no API). The id is English and self-describing
   (`BARBELL_BENCH_PRESS` → equipment=barbell, group=chest/push). Reproduce
   name + muscle group + equipment for all 261 from the id text, validated
   against the 102 API-verified rows. Sufficient for Garmin mapping, which also
   keys on English exercise names. **Recommended.**
2. **Switch strength focus to expose more (PROVEN).** Changing the plan's
   strength type regenerates the sessions with that focus's exercise set.
   Confirmed: `LEGS_AND_CORE` → 42 exercises; switching to `FULL_BODY` added 52
   new ones (barbell/dumbbell/kettlebell/band/box/swissball/pull-up movements) →
   102 total. Cycling through the remaining types (e.g. `UPPER_BODY`) and
   re-harvesting would extend coverage further. It mutates the live plan, so do
   it deliberately/with consent; coverage is still bounded by what types a
   running plan offers.
3. **Opportunistic harvest** — re-run the Appendix B harvester as the plan rolls
   forward week to week. Slow, incremental.

## 13. Remaining minor unknowns

- Full `strengthType` enum (§6) — enumerate by walking varied plans.
- Non-empty `exerciseWeight` shape (§5) — capture from a weighted session.
- `DayStrength` change-detection field (§10) — else rely on payload diffing.
- Exercise CDN base path (§9) — only if animations are ever needed.

---

## Appendix A — reproduction recipe (clean room)

**Static (structure, endpoints, enums, exercise-id universe):**
```bash
unzip -p com.runbuddy.prod-8.42.0.apk assets/index.android.bundle > bundle.hbc
# Hermes magic c6 1f bc 03 → bytecode, but the string table is plaintext.
strings -n 5 bundle.hbc                                   # hosts, enums, config keys
# GraphQL docs: regex (query|mutation|fragment) NAME ...{ balanced braces }
# exercise ids: grep -oE '[A-Z][A-Z0-9_]{2,}\.svg' | sed 's/\.svg//' | sort -u
```

**Live (auth, grades shape, dayId==workoutId, catalog):** log in to
`web.runna.com`; in the page console read the idToken cookie (§3) and POST
minimal queries to `https://hydra.platform.runna.com/graphql` with
`authorization: <idToken>` and `x-rb-platform-source: rb-web`. Walk weeks with
§4.2, fetch details with §4.4, dedupe exercises by `exerciseId` to rebuild the
catalog.

## Appendix B — minimal harvester (browser console)

```js
const idToken = decodeURIComponent(
  document.cookie.match(/CognitoIdentityServiceProvider\.[^;]*\.idToken=([^;]+)/)[1]);
const gql = (query, variables) => fetch('https://hydra.platform.runna.com/graphql', {
  method:'POST',
  headers:{'content-type':'application/json','authorization':idToken,'x-rb-platform-source':'rb-web'},
  body: JSON.stringify({query, variables})
}).then(r=>r.json());

const weekQ = `query W($w:Int!){getActiveOrderWeek(input:{weekIndex:$w}){week{days{__typename ... on DayStrength{id}}}}}`;
const detQ  = `query D($id:String){getWorkout(input:{workoutId:$id}){... on DayStrength{
  id strengthType date duration parts{partSets exercises{
    exerciseId exerciseTitle exerciseRequires exerciseIsUnilateral
    exerciseMuscleGroupBroad exerciseMuscleGroupSpecific timer exerciseVideo
    exerciseGrades{gradeType grades gradesV2} mostRecentSet{weightKg}}}}}}`;

const ids = [];
for (let w=0; w<=20; w++) {
  const r = await gql(weekQ,{w});
  for (const d of (r?.data?.getActiveOrderWeek?.week?.days||[]))
    if (d.__typename==='DayStrength') ids.push(d.id);
}
const workouts = [];
for (const id of ids) workouts.push((await gql(detQ,{id})).data.getWorkout);
console.log(JSON.stringify(workouts, null, 2));
```
