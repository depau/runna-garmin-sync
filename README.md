# Runna → Garmin strength workout sync 

Headless daemon that mirrors [Runna](https://runna.com) strength workouts to
Garmin Connect as scheduled strength workouts (Runna only syncs runs). Personal
use only — it talks to Runna's private GraphQL API (see `docs/runna-api.md`).

## Setup

```sh
uv sync
uv run runna-garmin-sync login   # one-time interactive login: prompts for Runna +
                                 # Garmin credentials and the Garmin MFA code
```

`login` seeds long-lived sessions on disk (`STATE_DIR`), so no credentials are
needed afterwards — every other command just reuses them. For non-interactive
deployments you can instead provide `RUNNA_EMAIL`/`RUNNA_PASSWORD` and
`GARMIN_EMAIL`/`GARMIN_PASSWORD` (or the matching flags), but the interactive
login is the recommended path: nothing sensitive ends up in your shell history
or unit files, and Garmin MFA needs a prompt anyway. The Runna password is the
Cognito native password (docs/runna-api.md §3.1).

## Run

```sh
uv run runna-garmin-sync sync            # one-shot sync (-n/--dry-run to preview, --no-cache to refetch)
uv run runna-garmin-sync daemon          # poll calendar ETag, sync on change
```

### Docker

```sh
docker compose run --rm sync login   # one-time interactive login into the state volume
docker compose up -d                 # daemon
```

See `docker-compose.yaml`; state persists in `./state`. Images are published to
`ghcr.io/depau/runna-garmin-sync` — use `:latest` for the latest stable release,
`:edge` for the latest development snapshot from the `main` branch.

Other commands: `dump` (print Runna strength days as JSON), `push` (push all
synced workouts to the primary training device), `garmin-workout <id>` (dump a
Garmin workout DTO, for debugging).

## Configuration (env vars — every one is also a CLI flag, see `--help`)

| Var | Default | |
|---|---|---|
| `STATE_DIR` | `$XDG_CONFIG_HOME/runna-garmin-sync` (`~/.config/…`) | token + sync state directory |
| `RUNNA_EMAIL` / `RUNNA_PASSWORD` | — | only needed if there is no session from `login` |
| `GARMIN_EMAIL` / `GARMIN_PASSWORD` | — | only needed if there is no session from `login` |
| `POLL_INTERVAL` | `60` | seconds between calendar ETag polls (a 304 is a few bytes) |
| `FORCE_SYNC_HOURS` | `6` | full sync even without a calendar change |
| `NOTIFY_URL` | — | [Apprise URL](https://appriseit.com/url-builder/) for daemon sync notifications |
| `NOTIFY_ERROR_URL` | — | Apprise URL for daemon error notifications (falls back to `NOTIFY_URL`) |
| `MAPPING_CSV` | CSV bundled in the package | exercise mapping override |

## State (all plain files in `STATE_DIR`)

- `garmin_tokens/` — Garmin OAuth tokens (garth, ~1 year, auto-refreshed)
- `runna_auth.json` — Cognito refresh + id token
- `runna_cache.json` — cached plan payloads, invalidated by the calendar ETag (periodic forced syncs bypass it to pick up logged weights)
- `sync_state.json` — calendar ETag + map of Runna workout id → Garmin workout id / schedule / content hash
- `unknown_exercises.json` — Runna exercises missing from the mapping CSV, captured for curation

## How it works

Poll the per-user Runna iCal feed with `If-None-Match` (a 304 is free). On
change (or every `FORCE_SYNC_HOURS`), walk the plan weeks via GraphQL, fetch
each `DayStrength`, build a Garmin strength workout (one repeat group per
Runna part, reps/time/rest steps, warmup phase when Runna marks it, original
exercise name + rep range + load + tips in the step notes), then reconcile:
create + schedule new days, update/reschedule changed ones, delete ours when a
future day disappears or is skipped, and push changes to the primary training
device. Only workouts this tool created are touched (tracked in
`sync_state.json`; the Runna deep link in each description carries the `dayId`).

Tests: `uv run pytest` · Lint/format: ruff via pre-commit (`uv run pre-commit install` once)
