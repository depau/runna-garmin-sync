"""Reconcile Runna strength days against Garmin Connect workouts."""

import datetime
import logging

from garminconnect import Garmin

from .builder import build_workout, content_hash
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


def plan_sync(runna: RunnaClient, mapping: Mapping, state: State) -> list[dict]:
    """Compute the actions a sync would take, without touching Garmin.

    Returns [{action, runnaId, date, workout?}] — action ∈ create/update/
    reschedule/unchanged/delete (update+date change lists both).
    """
    today = datetime.date.today().isoformat()
    tracked = state.load(SYNC_FILE, {}).get("workouts", {})
    link = runna.app_link_base()
    plan = []
    seen = set()
    for day in runna.strength_days_cached():
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


def full_sync(runna: RunnaClient, garmin: Garmin, mapping: Mapping, state: State, refresh: bool = False) -> dict:
    today = datetime.date.today().isoformat()
    st = state.load(SYNC_FILE, {})
    tracked = st.setdefault("workouts", {})

    link = runna.app_link_base()
    desired = {}
    for day in runna.strength_days_cached(refresh=refresh):
        if (day.get("date") or "") < today or day.get("skipped"):
            continue
        workout = build_workout(day, mapping, link)
        desired[day["id"]] = (workout, day["date"])

    stats = {"created": 0, "updated": 0, "rescheduled": 0, "deleted": 0, "unchanged": 0}

    for rid, (workout, date) in desired.items():
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
            }
            stats["created"] += 1
            log.info("created %s → garmin %s on %s", rid, gid, date)
        elif rec["hash"] != h:
            garmin.update_workout(rec["garminWorkoutId"], workout)
            stats["updated"] += 1
            if rec["date"] != date:
                if rec.get("scheduleId"):
                    garmin.unschedule_workout(rec["scheduleId"])
                sched = garmin.schedule_workout(rec["garminWorkoutId"], date)
                rec["scheduleId"] = _schedule_id(sched, garmin, rec["garminWorkoutId"], date)
                stats["rescheduled"] += 1
            rec.update(hash=h, date=date)
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
        log.info("deleted %s (garmin %s)", rid, rec["garminWorkoutId"])
        state.save(SYNC_FILE, st)

    st["lastSync"] = datetime.datetime.now(datetime.UTC).isoformat()
    state.save(SYNC_FILE, st)
    log.info("sync done: %s", stats)
    return stats
