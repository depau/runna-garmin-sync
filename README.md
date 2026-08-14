# runna-garmin-strength-workout-sync

Headless daemon that mirrors [Runna](https://runna.com) strength workouts to
Garmin Connect as scheduled strength workouts (Runna only syncs runs). Personal
use only — it talks to Runna's private GraphQL API (see `docs/runna-api.md`).

## Setup

```sh
uv sync
export RUNNA_EMAIL=… RUNNA_PASSWORD=…       # Cognito native password (docs/runna-api.md §3.1)
export GARMIN_EMAIL=… GARMIN_PASSWORD=…
uv run python -m runna_garmin_sync login    # one-time: prompts for Garmin MFA code, seeds tokens
```

## Run

```sh
uv run python -m runna_garmin_sync sync     # one-shot sync
uv run python -m runna_garmin_sync daemon   # poll calendar ETag, sync on change
```

Other commands: `dump` (print upcoming Runna strength days as JSON),
`garmin-workout <id>` (dump a Garmin workout DTO, for debugging).

## Configuration (env vars — every one is also a CLI flag, see `--help`)

| Var | Default | |
|---|---|---|
| `STATE_DIR` | `$XDG_CONFIG_HOME/runna-garmin-sync` (`~/.config/…`) | token + sync state directory |
| `POLL_INTERVAL` | `300` | seconds between calendar ETag polls |
| `FORCE_SYNC_HOURS` | `6` | full sync even without a calendar change |
| `MAPPING_CSV` | CSV bundled in the package | exercise mapping override |

## State (all plain files in `STATE_DIR`)

- `garmin_tokens/` — Garmin OAuth tokens (garth, ~1 year, auto-refreshed)
- `runna_auth.json` — Cognito refresh + id token
- `sync_state.json` — calendar ETag + map of Runna workout id → Garmin workout id / schedule / content hash
- `unknown_exercises.json` — Runna exercises missing from the mapping CSV, captured for curation

## How it works

Poll the per-user Runna iCal feed with `If-None-Match` (a 304 is free). On
change (or every `FORCE_SYNC_HOURS`), walk the plan weeks via GraphQL, fetch
each `DayStrength`, build a Garmin strength workout (one repeat group per
Runna part, reps/time/rest steps, warmup phase when Runna marks it, original
exercise name + rep range + load + tips in the step notes), then reconcile:
create + schedule new days, update/reschedule changed ones, delete ours when a
future day disappears or is skipped. Only workouts this tool created (tracked
in `sync_state.json`, also marked `runna:<id>` in the description) are touched.

Tests: `uv run pytest`
