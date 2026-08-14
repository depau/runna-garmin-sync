from runna_garmin_sync.mapping import humanize


def test_humanize():
    assert humanize("WALKING_LUNGE") == "Walking Lunge"


def test_known_exercise(mapping):
    m = mapping.lookup({"exerciseId": "WALKING_LUNGE"})
    assert m["category"] and m["exerciseName"]
    assert m["unmapped"] is False


def test_unknown_exercise_recorded_with_metadata(mapping, state):
    ex = {
        "exerciseId": "MADE_UP_MOVE",
        "exerciseTitle": "Titolo",
        "exerciseMuscleGroupBroad": "GLUTES",
        "exerciseVideo": "abc123",
    }
    m = mapping.lookup(ex)
    assert m["unmapped"] is True and m["approximate"] is True
    assert (m["category"], m["exerciseName"]) == ("HIP_RAISE", "HIP_RAISE")  # broad-group fallback
    rec = state.load("unknown_exercises.json")["MADE_UP_MOVE"]
    assert rec["exerciseTitle"] == "Titolo"
    assert rec["exerciseVideo"] == "abc123"


def test_unknown_without_muscle_group_uses_default(mapping):
    m = mapping.lookup({"exerciseId": "MYSTERY"})
    assert (m["category"], m["exerciseName"]) == ("TOTAL_BODY", "TOTAL_BODY")
