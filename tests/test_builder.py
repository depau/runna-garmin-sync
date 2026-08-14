import pytest

from runna_garmin_sync.builder import build_workout, content_hash, describe_workout


@pytest.fixture
def workout(day, mapping):
    return build_workout(day, mapping)


def test_workout_metadata(workout):
    assert workout["workoutName"] == "[Runna] Forza di gambe e core"
    assert workout["sportType"]["sportTypeKey"] == "strength_training"
    assert workout["estimatedDurationInSecs"] == 2100
    desc = workout["description"]
    assert "Scheduled at 18:50" in desc
    assert "Part 2: Keep it controlled" in desc
    assert "https://club.runna.com/n9Tx/workout?dayId=order_plan_week_3_LEGS_AND_CORE_0&weekIndex=3" in desc


def test_app_link_base_override(day, mapping):
    w = build_workout(day, mapping, "https://club.runna.com/USER/workout")
    assert "https://club.runna.com/USER/workout?dayId=" in w["description"]


def test_single_set_part_is_not_wrapped(workout):
    warmup = workout["workoutSegments"][0]["workoutSteps"][0]
    assert warmup["type"] == "ExecutableStepDTO"
    assert warmup["stepType"]["stepTypeKey"] == "warmup"
    assert warmup["endCondition"]["conditionTypeKey"] == "time"
    assert warmup["endConditionValue"] == 30.0
    assert "childStepId" not in warmup


def test_multi_set_part_is_a_repeat_group(workout):
    group = workout["workoutSegments"][0]["workoutSteps"][1]
    assert group["type"] == "RepeatGroupDTO"
    assert group["numberOfIterations"] == 3
    assert "skipLastRestStep" not in group  # Runna performs the rest every round
    assert all(s["childStepId"] == group["childStepId"] for s in group["workoutSteps"])


def test_reps_exercise_step(workout):
    lunge = workout["workoutSegments"][0]["workoutSteps"][1]["workoutSteps"][0]
    assert (lunge["category"], lunge["exerciseName"]) == ("LUNGE", "WALKING_LUNGE")
    assert lunge["endCondition"]["conditionTypeKey"] == "reps"
    assert lunge["endConditionValue"] == 8.0  # low end of "8-12"
    assert lunge["isMinReps"] is True  # Reps+
    assert lunge["weightValue"] == 10000.0  # grams
    assert lunge["description"].startswith("Affondi in camminata")
    assert "8-12 reps per side" in lunge["description"]
    assert "Moderato" in lunge["description"]


def test_unknown_exercise_falls_back_and_is_flagged(workout, state):
    unknown = workout["workoutSegments"][0]["workoutSteps"][1]["workoutSteps"][1]
    assert (unknown["category"], unknown["exerciseName"]) == ("CORE", "CORE")
    assert unknown["endCondition"]["conditionTypeKey"] == "time"
    assert unknown["endConditionValue"] == 40.0
    assert unknown["description"].startswith("Nuovo esercizio")
    assert unknown["description"].endswith("⚠ Not mapped yet")
    assert "SOME_BRAND_NEW_MOVE" in state.load("unknown_exercises.json")


def test_rest_step(workout):
    rest = workout["workoutSegments"][0]["workoutSteps"][1]["workoutSteps"][2]
    assert rest["stepType"]["stepTypeKey"] == "rest"
    assert rest["endConditionValue"] == 90.0


def test_step_orders_unique_and_increasing(workout):
    orders = []
    for step in workout["workoutSegments"][0]["workoutSteps"]:
        orders.append(step["stepOrder"])
        orders.extend(s["stepOrder"] for s in step.get("workoutSteps", []))
    assert orders == sorted(set(orders))


def test_content_hash_changes_with_date_and_content(workout):
    h = content_hash(workout, "2026-08-17")
    assert h != content_hash(workout, "2026-08-18")
    changed = dict(workout, workoutName="other")
    assert h != content_hash(changed, "2026-08-17")


def test_describe_workout_smoke(workout):
    text = describe_workout(workout)
    assert "3 sets:" in text
    assert "8+ reps" in text
    assert "@ 10kg" in text
