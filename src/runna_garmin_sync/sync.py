"""Reconcile Runna strength days against Garmin Connect workouts."""

import datetime
import logging

from garminconnect import Garmin

from .builder import RUNNA_APP_LINK, build_workout, content_hash
from .mapping import Mapping
from .runna import RunnaClient
from .state import State

log = logging.getLogger(__name__)

SYNC_FILE = "sync_state.json"


def make_garmin(email: str, password: str, state: State, interactive: bool = False) -> Garmin:
    prompt = (lambda: input("Garmin MFA code: ")) if interactive else None
    g = Garmin(email=email, password=password, prompt_mfa=prompt)
    g.login(tokenstore=str(state.path("garmin_tokens")))
    return g


def _schedule_id(response: dict, garmin: Garmin, workout_id, date: str):
    sid = (response or {}).get("workoutScheduleId") or (response or {}).get("id")
    if sid:
        return sid
    # Fallback: response shape unknown — find it on the calendar month
    y, m, _ = date.split("-")
    for item in garmin.get_scheduled_workouts(int(y), int(m)) or []:
        if str(item.get("workoutId")) == str(workout_id) and item.get("date") == date:
            return item.get("scheduleId") or item.get("id")
    return None


def plan_sync(runna: RunnaClient, mapping: Mapping, state: State, refresh: bool = False) -> list[dict]:
    """Compute the actions a sync would take, without touching Garmin.

    Returns [{action, runnaId, date, workout?}] — action ∈ create/update/
    reschedule/unchanged/delete (update+date change lists both).
    """
    today = datetime.date.today().isoformat()
    tracked = state.load(SYNC_FILE, {}).get("workouts", {})
    link = runna.app_link_base()
    plan = []
    seen = set()
    for day in runna.strength_days_cached(refresh=refresh):
        rid = day["id"]
        if (day.get("date") or "") < today or day.get("skipped"):
            continue
        seen.add(rid)
        workout = build_workout(day, mapping, link)
        date = day["date"]
        rec = tracked.get(rid)
        if not rec:
            actions = ["create"]
        elif rec["hash"] != content_hash(workout, date):
            actions = ["update"] + (["reschedule"] if rec["date"] != date else [])
        else:
            actions = ["unchanged"]
        plan.append({"action": "+".join(actions), "runnaId": rid, "date": date, "workout": workout})
    for rid, rec in tracked.items():
        if rid not in seen and rec["date"] >= today:
            plan.append({"action": "delete", "runnaId": rid, "date": rec["date"]})
    return plan


def push_to_device(garmin: Garmin, workout_ids: list) -> int:
    """Push workouts to the primary training device. Returns how many succeeded."""
    if not workout_ids:
        return 0
    try:
        device_id = garmin.get_primary_training_device()["PrimaryTrainingDevice"]["deviceId"]
    except Exception as e:
        log.warning("no primary training device (%s); using last-used device", e)
        device_id = None
    pushed = 0
    for wid in workout_ids:
        try:
            garmin.push_workout_to_device(wid, device_id)
            pushed += 1
        except Exception as e:
            log.warning("device push of workout %s failed: %s", wid, e)
    return pushed


def delete_all(garmin: Garmin, state: State) -> int:
    """Delete every Garmin workout this tool created (tracked in sync_state.json)."""
    st = state.load(SYNC_FILE, {})
    tracked = st.get("workouts", {})
    deleted = 0
    for rid in list(tracked):
        rec = tracked[rid]
        if rec.get("scheduleId"):
            try:
                garmin.unschedule_workout(rec["scheduleId"])
            except Exception as e:
                log.warning("unschedule %s failed: %s", rid, e)
        try:
            garmin.delete_workout(rec["garminWorkoutId"])
        except Exception as e:
            log.warning("delete %s (garmin %s) failed: %s", rid, rec["garminWorkoutId"], e)
            continue
        del tracked[rid]
        deleted += 1
        log.info("deleted %s (garmin %s)", rid, rec["garminWorkoutId"])
        state.save(SYNC_FILE, st)
    return deleted


def full_sync(runna: RunnaClient, garmin: Garmin, mapping: Mapping, state: State, refresh: bool = False) -> dict:
    today = datetime.date.today().isoformat()
    st = state.load(SYNC_FILE, {})
    tracked = st.setdefault("workouts", {})

    link_base = runna.app_link_base() or RUNNA_APP_LINK
    desired = {}
    for day in runna.strength_days_cached(refresh=refresh):
        if (day.get("date") or "") < today or day.get("skipped"):
            continue
        workout = build_workout(day, mapping, link_base)
        info = {
            "name": day.get("strengthTitle") or "Strength",
            "link": f"{link_base}?dayId={day['id']}&weekIndex={day.get('weekIndex')}",
        }
        desired[day["id"]] = (workout, day["date"], info)

    stats = {"created": 0, "updated": 0, "rescheduled": 0, "deleted": 0, "unchanged": 0, "pushed": 0}
    changes = stats["changes"] = []
    touched = []

    for rid, (workout, date, info) in desired.items():
        h = content_hash(workout, date)
        rec = tracked.get(rid)
        if not rec:
            res = garmin.upload_workout(workout)
            gid = res["workoutId"]
            sched = garmin.schedule_workout(gid, date)
            tracked[rid] = {
                "garminWorkoutId": gid,
                "scheduleId": _schedule_id(sched, garmin, gid, date),
                "date": date,
                "hash": h,
                **info,
            }
            stats["created"] += 1
            touched.append(gid)
            changes.append({"action": "created", "date": date, **info})
            log.info("created %s → garmin %s on %s", rid, gid, date)
        elif rec["hash"] != h:
            garmin.update_workout(rec["garminWorkoutId"], workout)
            stats["updated"] += 1
            touched.append(rec["garminWorkoutId"])
            if rec["date"] != date:
                if rec.get("scheduleId"):
                    garmin.unschedule_workout(rec["scheduleId"])
                sched = garmin.schedule_workout(rec["garminWorkoutId"], date)
                rec["scheduleId"] = _schedule_id(sched, garmin, rec["garminWorkoutId"], date)
                stats["rescheduled"] += 1
            rec.update(hash=h, date=date, **info)
            changes.append({"action": "updated", "date": date, **info})
            log.info("updated %s (garmin %s)", rid, rec["garminWorkoutId"])
        else:
            stats["unchanged"] += 1
        state.save(SYNC_FILE, st)

    # Delete our Garmin workouts for future days that vanished or got skipped.
    # ponytail: past-dated entries stay tracked forever (state file stays tiny)
    for rid in list(tracked):
        rec = tracked[rid]
        if rid in desired or rec["date"] < today:
            continue
        if rec.get("scheduleId"):
            try:
                garmin.unschedule_workout(rec["scheduleId"])
            except Exception as e:
                log.warning("unschedule %s failed: %s", rid, e)
        garmin.delete_workout(rec["garminWorkoutId"])
        del tracked[rid]
        stats["deleted"] += 1
        # no link: the workout is gone from Runna, a deep link would just 404
        changes.append({"action": "deleted", "date": rec["date"], "name": rec.get("name", rid), "link": None})
        log.info("deleted %s (garmin %s)", rid, rec["garminWorkoutId"])
        state.save(SYNC_FILE, st)

    stats["pushed"] = push_to_device(garmin, touched)

    st["lastSync"] = datetime.datetime.now(datetime.UTC).isoformat()
    state.save(SYNC_FILE, st)
    log.info("sync done: %s", stats)
    return stats
