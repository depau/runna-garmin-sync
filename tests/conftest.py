import copy

import pytest

from runna_garmin_sync.mapping import Mapping
from runna_garmin_sync.state import State

DAY = {
    "id": "order_plan_week_3_LEGS_AND_CORE_0",
    "strengthType": "LEGS_AND_CORE",
    "strengthTypeDisplay": "Gambe e core",
    "strengthTitle": "Forza di gambe e core",
    "date": "2026-08-17",
    "weekIndex": 3,
    "scheduled24HourTime": "18:50",
    "duration": [1500, 2100],
    "durationFormatted": "25 m - 35 m",
    "note": None,
    "parts": [
        {
            "id": "p0",
            "partSets": 1,
            "exercises": [
                {
                    "id": "w0",
                    "exerciseId": "HIGH_KNEE_DRILL",
                    "exerciseTitle": "Corsa a ginocchia alte",
                    "exerciseMuscleGroupBroad": "WARM_UP",
                    "exerciseMuscleGroupSpecific": "WARMUP_EX_LOWER",
                    "exerciseGrades": {"gradeType": "SECONDS", "grades": [30], "gradesV2": ["30"]},
                }
            ],
        },
        {
            "id": "p1",
            "partSets": 3,
            "partCoach": "Keep it controlled",
            "partComment": None,
            "exercises": [
                {
                    "id": "e0",
                    "exerciseId": "WALKING_LUNGE",
                    "exerciseTitle": "Affondi in camminata",
                    "exerciseIsUnilateral": True,
                    "exerciseWeight": "Moderato",
                    "exerciseMuscleGroupBroad": "QUADS",
                    "exerciseMuscleGroupSpecific": "BILATERAL_QUAD",
                    "exerciseGrades": {"gradeType": "REPS", "grades": [6, 6, 6], "gradesV2": ["8-12", "8-12", "8-12"]},
                    "mostRecentSet": {"weightKg": 10},
                },
                {
                    "id": "e1",
                    "exerciseId": "SOME_BRAND_NEW_MOVE",
                    "exerciseTitle": "Nuovo esercizio",
                    "exerciseMuscleGroupBroad": "CORE",
                    "exerciseGrades": {"gradeType": "SECONDS", "grades": [40, 40, 40], "gradesV2": ["40", "40", "40"]},
                },
                {"id": "e2", "exerciseId": "TIMED_REST", "exerciseTitle": "90 s di riposo", "timer": 90},
            ],
        },
    ],
}


@pytest.fixture
def state(tmp_path):
    return State(tmp_path)


@pytest.fixture
def mapping(state):
    return Mapping(state)  # bundled CSV


@pytest.fixture
def day():
    return copy.deepcopy(DAY)
