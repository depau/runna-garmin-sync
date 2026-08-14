# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Headless daemon that mirrors Runna strength workouts to Garmin Connect as scheduled strength
workouts (Runna only syncs runs to Garmin). Personal use; it talks to Runna's **private,
reverse-engineered** GraphQL API — the clean-room reference in `docs/runna-api.md` is the
authoritative source for everything Runna-side (auth, queries, data model, enums, sync strategy).
Do not publish this as a service; never log or commit tokens (the Runna idToken embeds live
Strava credentials).

## Commands

```sh
uv sync                                   # install (uv project, python >=3.13)
uv run pytest                             # all tests
uv run pytest tests/test_sync.py -k date  # single file / -k pattern
uv run pre-commit run --all-files         # ruff check --fix + ruff format (hooks: pre-commit install)
uv run runna-garmin-sync --help           # CLI (console script; also python -m runna_garmin_sync)
uv run runna-garmin-sync sync -n          # dry run: prints the reconcile plan, never touches Garmin
uv run runna-garmin-sync sync -n --json   # same, as JSON with the exact Garmin DTOs
python3 docs/build-mapping.py .           # regenerate the exercise-mapping CSV (deterministic)
```

Live commands (`login`, `dump`, `sync`, `daemon`, `garmin-workout <id>`) hit the user's real
Runna/Garmin accounts; sessions live in `~/.config/runna-garmin-sync` (override `STATE_DIR`).
`dump`, `sync -n`, and `garmin-workout` are read-only and safe to run for debugging; a plain
`sync` writes to the real Garmin calendar — don't run it unprompted.

## Architecture

Pipeline in `src/runna_garmin_sync/` (src layout, uv_build backend):

- `runna.py` — Cognito auth (raw HTTP, no boto3; password → cached refresh token → cached idToken
  in `runna_auth.json`), minimal GraphQL client, plan-week walker, iCal conditional GET (ETag) as
  the cheap change signal, per-user app-link base harvested from the iCal feed.
- `mapping.py` — Runna `exerciseId` → Garmin `{category, exerciseName}` from the **vendored**
  `runna-garmin-mapping.csv` (inside the package so pip installs keep it). Unknown ids are
  persisted to `unknown_exercises.json` for curation and mapped to a same-muscle-group generic.
- `builder.py` — Runna `DayStrength` → plain Garmin workout-service DTO dicts (pydantic is not
  installed, so garminconnect's typed models are unusable stubs; we upload dicts via
  `upload_workout`). One `RepeatGroupDTO` per multi-set Runna part (single-set parts emit bare
  steps), Reps+ = `isMinReps: true`, weights in grams, localized exercise titles + rep ranges +
  tips in step descriptions, Runna deep link + scheduled time in the workout description.
- `sync.py` — reconciler keyed on Runna workout id + content hash of the built DTO: create+schedule,
  update in place (PUT keeps the Garmin id so schedules survive), reschedule on date change, delete
  only tool-created future workouts (tracked in `sync_state.json`). `plan_sync()` is the read-only
  twin used by dry-run. Garmin calendar scheduling is date-only.
- `__main__.py` — click CLI. Credential flags/env vars are optional everywhere: commands reuse
  cached sessions and fail with "run login" when there is none; only `login` prompts (incl. Garmin
  MFA, needed once — tokens last ~1 year).

State is plain JSON files in one directory, written atomically; there is deliberately no database.

## Hard-won API facts (don't rediscover)

- Runna GraphQL: raw idToken in `authorization` (no "Bearer"), `x-rb-platform-source: rb-web`;
  out-of-range weeks return `"week": null` (not missing); the workout-time field is
  `scheduled24HourTime`; localized strings can't be forced to English — map via the stable
  English `exerciseId`.
- Garmin workout DTOs: verified by round-tripping UI-created workouts (`garmin-workout` command);
  Reps+ is `isMinReps`, not `endConditionCompare`; repeat groups link children via `childStepId`;
  Garmin rejects category `OTHER`/`UNASSIGNED` with 400 — the mapping always names a real pair.

## Exercise mapping workflow

`docs/build-mapping.py` generates `src/runna_garmin_sync/runna-garmin-mapping.csv` from the id
universe + harvested catalog + `CURATED` overrides (all in `docs/`). To fix a bad mapping, edit
`CURATED` in the script and regenerate — don't hand-edit the CSV. Output must stay deterministic
(no set-iteration-order dependence) and fully mapped (`all mapped: True` in its output).

## Conventions

- Ruff (line length 120) via pre-commit; conventional-commit style messages; the user commits —
  never commit or push yourself.
- Tests use fakes (`FakeRunna`/`FakeGarmin` in `tests/test_sync.py`) — never hit real APIs from
  tests. Shared sample day payload lives in `tests/conftest.py`.
