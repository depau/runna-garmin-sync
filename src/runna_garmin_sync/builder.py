"""Build a Garmin Connect strength workout (plain workout-service DTO dicts)
from a Runna DayStrength payload.

DTO shapes/ids follow garminconnect.workout (pydantic isn't installed, so we
emit dicts directly and upload via Garmin.upload_workout).
"""

import hashlib
import json
import re

from .mapping import Mapping

# Deep link into the Runna app, same format as the iCal feed; the dayId query
# param doubles as our ownership marker on Garmin workouts we created. Fallback
# for when the per-user base (RunnaClient.app_link_base) can't be harvested.
RUNNA_APP_LINK = "https://club.runna.com/n9Tx/workout"

_STEP = {"warmup": 1, "interval": 3, "rest": 5, "repeat": 6}
_NO_TARGET = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}
_KG = {"unitId": 8, "unitKey": "kilogram", "factor": 1000.0}


def _step_type(key: str) -> dict:
    return {"stepTypeId": _STEP[key], "stepTypeKey": key}


def _end(kind: str, value: float) -> dict:
    cond = {"time": (2, "time"), "reps": (10, "reps"), "iterations": (7, "iterations")}[kind]
    return {
        "endCondition": {"conditionTypeId": cond[0], "conditionTypeKey": cond[1], "displayable": True},
        "endConditionValue": float(value),
    }


def _low(grade: str) -> int:
    nums = re.findall(r"\d+", grade or "")
    return int(nums[0]) if nums else 0


def _grades(exercise: dict) -> tuple[str, list[str]]:
    g = exercise.get("exerciseGrades") or {}
    vals = g.get("gradesV2") or [str(x) for x in (g.get("grades") or [])]
    return g.get("gradeType") or "REPS", [str(v) for v in vals]


def _is_warmup(exercise: dict) -> bool:
    return exercise.get("exerciseMuscleGroupBroad") == "WARM_UP" or (
        exercise.get("exerciseMuscleGroupSpecific") or ""
    ).startswith("WARMUP")


def _description(exercise: dict, m: dict, grade_type: str, grades: list[str]) -> str:
    lines = [exercise.get("exerciseTitle") or m["hint"]]
    if grades:
        per_side = " per side" if exercise.get("exerciseIsUnilateral") else ""
        unit = "reps" if grade_type == "REPS" else "s"
        if len(set(grades)) > 1:
            lines.append(f"Sets: {' / '.join(grades)} {unit}{per_side}")
        else:
            lines.append(f"{grades[0]} {unit}{per_side}")
    if exercise.get("exerciseWeight"):
        lines.append(f"Load: {exercise['exerciseWeight']}")
    if exercise.get("exerciseTip"):
        lines.append(exercise["exerciseTip"])
    if exercise.get("note"):
        lines.append(exercise["note"])
    if m["unmapped"]:
        lines.append("⚠ Not mapped yet")
    elif m["approximate"]:
        lines.append("(approximate Garmin match)")
    return "\n".join(lines)[:500]


def _exercise_step(exercise: dict, m: dict, order: int) -> dict:
    grade_type, grades = _grades(exercise)
    step = {
        "type": "ExecutableStepDTO",
        "stepOrder": order,
        "stepType": _step_type("warmup" if _is_warmup(exercise) else "interval"),
        "targetType": dict(_NO_TARGET),
        "category": m["category"],
        "exerciseName": m["exerciseName"],
        "description": _description(exercise, m, grade_type, grades),
    }
    if grade_type == "SECONDS":
        step |= _end("time", _low(grades[0]) if grades else 30)
    else:
        step |= _end("reps", _low(grades[0]) if grades else 1)
        step["isMinReps"] = True  # the UI's "Reps+" toggle (verified via a dumped UI workout)
    weight = (exercise.get("mostRecentSet") or {}).get("weightKg")
    if weight:
        step["weightValue"] = float(weight) * 1000.0  # grams
        step["weightUnit"] = dict(_KG)
    return step


def _rest_step(exercise: dict, order: int) -> dict:
    return {
        "type": "ExecutableStepDTO",
        "stepOrder": order,
        "stepType": _step_type("rest"),
        "targetType": dict(_NO_TARGET),
        **_end("time", exercise.get("timer") or 60),
    }


def build_workout(day: dict, mapping: Mapping, app_link_base: str | None = None) -> dict:
    """Runna DayStrength → Garmin workout-service DTO dict."""
    order = 0
    groups = []
    coach_notes = []
    for i, part in enumerate(day.get("parts") or [], start=1):
        sets = part.get("partSets") or 1
        if sets > 1:
            order += 1  # the repeat group's own stepOrder precedes its children's
        group_order = order
        steps = []
        for exercise in part.get("exercises") or []:
            order += 1
            if exercise["exerciseId"] == "TIMED_REST":
                steps.append(_rest_step(exercise, order))
            else:
                steps.append(_exercise_step(exercise, mapping.lookup(exercise), order))
        if sets <= 1:
            # single-round part: plain steps, no pointless "1 Serie" wrapper
            groups.extend(steps)
        else:
            group = {
                "type": "RepeatGroupDTO",
                "stepOrder": group_order,
                "stepType": _step_type("repeat"),
                "childStepId": i,  # links the group's children to it (mirrors UI-created workouts)
                "numberOfIterations": sets,
                "smartRepeat": False,
                "workoutSteps": steps,
                **_end("iterations", sets),
            }
            for step in steps:
                step["childStepId"] = i
            if steps and steps[-1]["stepType"]["stepTypeKey"] == "rest":
                group["skipLastRestStep"] = True
            groups.append(group)
        for text in (part.get("partCoach"), part.get("partComment")):
            if text:
                coach_notes.append(f"Part {i}: {text}")

    duration = day.get("duration") or []
    desc_lines = [f"Synced from Runna — {day.get('strengthTypeDisplay') or day.get('strengthType')}"]
    if day.get("scheduled24HourTime"):
        # Garmin's calendar schedule is date-only, so the time can only ride along here
        desc_lines.append(f"Scheduled at {day['scheduled24HourTime']}")
    if day.get("durationFormatted"):
        desc_lines.append(f"Estimated: {day['durationFormatted']}")
    if day.get("note"):
        desc_lines.append(day["note"])
    desc_lines += coach_notes
    desc_lines.append(f"{app_link_base or RUNNA_APP_LINK}?dayId={day['id']}&weekIndex={day.get('weekIndex')}")

    return {
        "workoutName": day.get("strengthTitle") or "Runna Strength",
        "description": "\n".join(desc_lines)[:1000],
        "sportType": {"sportTypeId": 5, "sportTypeKey": "strength_training"},
        "estimatedDurationInSecs": int(max(duration) if duration else 1800),
        "author": {},
        "workoutSegments": [
            {
                "segmentOrder": 1,
                "sportType": {"sportTypeId": 5, "sportTypeKey": "strength_training"},
                "workoutSteps": groups,
            }
        ],
    }


def _describe_step(step: dict, indent: str) -> list[str]:
    kind = step["stepType"]["stepTypeKey"]
    if step["type"] == "RepeatGroupDTO":
        head = f"{indent}{step['numberOfIterations']} sets"
        if step.get("skipLastRestStep"):
            head += " (skip last rest)"
        lines = [head + ":"]
        for child in step["workoutSteps"]:
            lines += _describe_step(child, indent + "  ")
        return lines
    cond = step["endCondition"]["conditionTypeKey"]
    value = int(step["endConditionValue"])
    amount = f"{value}s" if cond == "time" else f"{value}{'+' if step.get('isMinReps') else ''} reps"
    if kind == "rest":
        return [f"{indent}[rest] {amount}"]
    name = step.get("exerciseName") or step.get("category")
    line = f"{indent}[{kind}] {name} ({step.get('category')}) — {amount}"
    if step.get("weightValue"):
        line += f" @ {step['weightValue'] / 1000:g}kg"
    desc = (step.get("description") or "").splitlines()
    lines = [line] + [f"{indent}    {d}" for d in desc]
    return lines


def describe_workout(workout: dict) -> str:
    """Human-readable rendition of a built Garmin workout DTO."""
    lines = [workout["workoutName"]]
    lines += [f"  {line}" for line in workout.get("description", "").splitlines()]
    for step in workout["workoutSegments"][0]["workoutSteps"]:
        lines += _describe_step(step, "  ")
    return "\n".join(lines)


def content_hash(workout: dict, date: str) -> str:
    return hashlib.sha256(
        json.dumps({"workout": workout, "date": date}, sort_keys=True).encode()
    ).hexdigest()
