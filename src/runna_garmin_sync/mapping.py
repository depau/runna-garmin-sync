"""Runna exerciseId → Garmin {category, exerciseName} mapping.

Backed by runna-garmin-mapping.csv shipped inside this package (regenerate with
docs/build-mapping.py). Unknown exerciseIds are persisted to
unknown_exercises.json (with the metadata Runna sent) for later curation and
mapped to a same-muscle-group generic in the meantime.
"""

import csv
import logging
from importlib import resources
from pathlib import Path

from .state import State

log = logging.getLogger(__name__)

UNKNOWN_FILE = "unknown_exercises.json"

# Broad muscle group → generic Garmin (category, exercise); all pairs verified
# to exist in docs/garmin-exercises.csv.
FALLBACK = {
    "WARM_UP": ("WARM_UP", "WARM_UP"),
    "QUADS": ("SQUAT", "SQUAT"),
    "GLUTES": ("HIP_RAISE", "HIP_RAISE"),
    "HAMSTRING": ("LEG_CURL", "LEG_CURL"),
    "CALVES": ("CALF_RAISE", "CALF_RAISE"),
    "CORE": ("CORE", "CORE"),
    "CHEST": ("BENCH_PRESS", "BENCH_PRESS"),
    "BACK": ("ROW", "ROW"),
    "SHOULDERS": ("SHOULDER_PRESS", "SHOULDER_PRESS"),
    "PLYOS": ("PLYO", "PLYO"),
    "FULL_BODY": ("TOTAL_BODY", "TOTAL_BODY"),
    "EXTRA": ("TOTAL_BODY", "TOTAL_BODY"),
}
DEFAULT_FALLBACK = ("TOTAL_BODY", "TOTAL_BODY")


def humanize(exercise_id: str) -> str:
    return exercise_id.replace("_", " ").title()


class Mapping:
    def __init__(self, state: State, csv_path: str | Path | None = None):
        src = Path(csv_path) if csv_path else resources.files(__package__) / "runna-garmin-mapping.csv"
        with src.open(newline="") as f:
            self.rows = {r["runna_exerciseId"]: r for r in csv.DictReader(f)}
        self.state = state

    def lookup(self, exercise: dict) -> dict:
        """Return {category, exerciseName, hint, approximate} for a Runna exercise."""
        ex_id = exercise["exerciseId"]
        row = self.rows.get(ex_id)
        if row:
            return {
                "category": row["garmin_category"],
                "exerciseName": row["garmin_exercise"],
                "hint": row["description_hint"] or humanize(ex_id),
                "approximate": row["confidence"] == "low" or row["method"] == "fallback",
                "unmapped": False,
            }
        self._record_unknown(exercise)
        broad = exercise.get("exerciseMuscleGroupBroad") or ""
        cat, name = FALLBACK.get(broad, DEFAULT_FALLBACK)
        return {
            "category": cat,
            "exerciseName": name,
            "hint": humanize(ex_id),
            "approximate": True,
            "unmapped": True,
        }

    def _record_unknown(self, exercise: dict) -> None:
        unknown = self.state.load(UNKNOWN_FILE, {})
        ex_id = exercise["exerciseId"]
        if ex_id not in unknown:
            log.warning("Unknown Runna exercise %s — recorded to %s", ex_id, UNKNOWN_FILE)
        unknown[ex_id] = {
            k: exercise.get(k)
            for k in (
                "exerciseTitle",
                "exerciseRequires",
                "exerciseRequires2",
                "exerciseIsUnilateral",
                "exerciseMuscleGroupBroad",
                "exerciseMuscleGroupSpecific",
                "exerciseVideo",
            )
        }
        self.state.save(UNKNOWN_FILE, unknown)
