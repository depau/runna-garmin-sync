# Runna → Garmin exercise mapping

Maps **every** Runna `exerciseId` to a Garmin Connect strength exercise
`{category, exercise}` (the enum keys the workout API needs). Output:
[`src/runna_garmin_sync/runna-garmin-mapping.csv`](../src/runna_garmin_sync/runna-garmin-mapping.csv)
(shipped inside the package so it survives `pip install`). **Every row is mapped —
there are no un-mapped/dropped exercises.**

## Inputs
- Runna side: `runna-exercise-catalog.json` (102 with metadata) + `runna-exercise-ids.txt` (261 universe).
- Garmin side: `garmin-exercises.json` / `.csv` — 1527 exercises / 47 categories, from
  `python-garminconnect==0.3.10`'s FIT exercise enums.
- Builder: `build-mapping.py` (retrieval + heuristic scoring + hand-curated overrides + generic fallback).
  Regenerate: `python3 docs/build-mapping.py .` from the repo root.

## Columns
| column | meaning |
|---|---|
| `runna_exerciseId` | Runna key (join key) |
| `garmin_category`, `garmin_exercise` | Garmin enum pair to send in the workout step (**always populated**) |
| `garmin_name` | Garmin English display name (sanity check) |
| `method` | `enum-exact` (id == Garmin enum) · `curated` (hand-verified) · `heuristic` (auto) · `fallback` (generic same-muscle placeholder) |
| `confidence` | `high` / `med` / `low` — **low / `fallback` = weak match, lean on the description** |
| `runna_data` | `verified` (real API metadata) vs `inferred` (only the id text — a guess) |
| `intensity` | `weighted` / `bodyweight` / `banded` |
| `intensity_match` | `same` = Garmin target is the same load type · `relaxed` = preference dropped to keep the muscle group |
| `runna_equip`, `runna_muscleBroad` | signals used for matching |
| `description_hint` | humanized Runna name — **put this in the Garmin step's description/notes** |
| `notes` | rationale for curated/fallback rows |

## Rules applied (in priority order)
1. **Same muscle group is the hard requirement.** Candidates are drawn from the Garmin
   categories that match the Runna `muscleGroupBroad` (crosswalk: `QUADS`→`SQUAT`/`LUNGE`,
   `GLUTES`→`HIP_RAISE`, `HAMSTRING`→`LEG_CURL`/`DEADLIFT`, `CHEST`→`BENCH_PRESS`/`PUSH_UP`/`FLYE`,
   `BACK`→`ROW`/`PULL_UP`, `SHOULDERS`→`SHOULDER_PRESS`/`LATERAL_RAISE`, `CORE`→`CORE`/`PLANK`/`CRUNCH`,
   `PLYOS`→`PLYO`, `FULL_BODY`→`TOTAL_BODY`, …).
2. **Intensity is a preference, not a filter.** A weighted Runna exercise prefers a weighted
   Garmin target (so it isn't awkward to load in Connect), but if none fits the movement the
   preference is dropped — `intensity_match=relaxed` flags those. (Garmin intensity is read from
   the exercise name + category: Barbell/Dumbbell/Kettlebell/Weighted and categories like
   `OLYMPIC_LIFT`/`DEADLIFT`/`BENCH_PRESS`/`ROW` → weighted; `BANDED_EXERCISES` → banded.)
3. **Name similarity** within candidates picks the exercise; equipment refines it.
4. **Generic fallback** when no exercise name matches: the movement maps to the category's
   generic (e.g. `SQUAT/SQUAT`, `PLANK/PLANK`, `WARM_UP/WARM_UP`, `TOTAL_BODY/TOTAL_BODY`),
   `method=fallback`, `confidence=low`. Still same muscle group; the real movement is conveyed
   by `description_hint`.

Because Garmin rejects `OTHER`/`UNASSIGNED`, we never emit those — the mapping always names a
real category+exercise, and the precise Runna movement rides along in the step description.

## Coverage (last run)
- **266 rows (261-id universe ∪ catalog ∪ curated), all mapped. 71 high · 159 med · 36 low.**
- Intensity: **212 same · 54 relaxed** (relaxed = same muscle group but load type differs).
- Verified (102 real exercises): 71 high · 21 med · 10 low — these are curated/high quality.
- Inferred (164, guessed from id text only): capped at `med`; review before trusting.

## How to use in the adapter
```python
import csv
MAP = {r["runna_exerciseId"]: r for r in csv.DictReader(open("src/runna_garmin_sync/runna-garmin-mapping.csv"))}
m = MAP[ex.exerciseId]
step["category"]     = m["garmin_category"]        # always present
step["exerciseName"] = m["garmin_exercise"]
step["description"]  = m["description_hint"]        # always carry the real Runna movement
```
Treat `confidence=low` / `method=fallback` / `intensity_match=relaxed` / any `inferred` row as
"category is right, exact exercise is approximate" — the description keeps it unambiguous for the
user. Refine rows over time as the catalog grows (re-harvest → re-run the builder).
