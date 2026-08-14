import copy
import datetime

import pytest
from conftest import DAY

from runna_garmin_sync.sync import SYNC_FILE, full_sync, plan_sync

TOMORROW = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
NEXT_WEEK = (datetime.date.today() + datetime.timedelta(days=7)).isoformat()
YESTERDAY = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()


def make_day(rid: str, date: str, **overrides):
    day = copy.deepcopy(DAY)
    day["id"] = rid
    day["date"] = date
    day.update(overrides)
    return day


class FakeRunna:
    def __init__(self, days):
        self.days = {d["id"]: d for d in days}

    def strength_days_cached(self, refresh=False):
        return list(self.days.values())

    def app_link_base(self):
        return "https://club.runna.com/USER/workout"


class FakeGarmin:
    def __init__(self):
        self.calls = []
        self._next_id = 100

    def upload_workout(self, workout):
        self._next_id += 1
        self.calls.append(("upload", self._next_id))
        return {"workoutId": self._next_id}

    def schedule_workout(self, workout_id, date):
        self.calls.append(("schedule", workout_id, date))
        return {"workoutScheduleId": workout_id * 10}

    def update_workout(self, workout_id, workout):
        self.calls.append(("update", workout_id))

    def unschedule_workout(self, schedule_id):
        self.calls.append(("unschedule", schedule_id))

    def delete_workout(self, workout_id):
        self.calls.append(("delete", workout_id))

    def call_names(self):
        return [c[0] for c in self.calls]


@pytest.fixture
def runna():
    return FakeRunna([make_day("r1", TOMORROW), make_day("r2", NEXT_WEEK), make_day("past", YESTERDAY)])


def test_first_sync_creates_and_schedules(runna, mapping, state):
    garmin = FakeGarmin()
    stats = full_sync(runna, garmin, mapping, state)
    assert stats == {"created": 2, "updated": 0, "rescheduled": 0, "deleted": 0, "unchanged": 0}
    assert garmin.call_names() == ["upload", "schedule", "upload", "schedule"]
    tracked = state.load(SYNC_FILE)["workouts"]
    assert set(tracked) == {"r1", "r2"}
    assert tracked["r1"]["date"] == TOMORROW and tracked["r1"]["scheduleId"]


def test_second_sync_is_noop(runna, mapping, state):
    full_sync(runna, FakeGarmin(), mapping, state)
    garmin = FakeGarmin()
    stats = full_sync(runna, garmin, mapping, state)
    assert stats["unchanged"] == 2 and garmin.calls == []


def test_content_change_updates_in_place(runna, mapping, state):
    full_sync(runna, FakeGarmin(), mapping, state)
    runna.days["r1"]["parts"][1]["partSets"] = 5
    garmin = FakeGarmin()
    stats = full_sync(runna, garmin, mapping, state)
    assert stats["updated"] == 1 and stats["rescheduled"] == 0
    assert garmin.call_names() == ["update"]


def test_date_change_reschedules(runna, mapping, state):
    full_sync(runna, FakeGarmin(), mapping, state)
    moved = (datetime.date.today() + datetime.timedelta(days=2)).isoformat()
    runna.days["r1"]["date"] = moved
    garmin = FakeGarmin()
    stats = full_sync(runna, garmin, mapping, state)
    assert stats["rescheduled"] == 1
    assert garmin.call_names() == ["update", "unschedule", "schedule"]
    assert state.load(SYNC_FILE)["workouts"]["r1"]["date"] == moved


def test_vanished_and_skipped_days_are_deleted(runna, mapping, state):
    full_sync(runna, FakeGarmin(), mapping, state)
    del runna.days["r1"]
    runna.days["r2"]["skipped"] = True
    garmin = FakeGarmin()
    stats = full_sync(runna, garmin, mapping, state)
    assert stats["deleted"] == 2
    assert garmin.call_names() == ["unschedule", "delete", "unschedule", "delete"]
    assert state.load(SYNC_FILE)["workouts"] == {}


def test_plan_sync_reports_without_touching_garmin(runna, mapping, state):
    plan = plan_sync(runna, mapping, state)
    assert {p["action"] for p in plan} == {"create"}
    assert {p["runnaId"] for p in plan} == {"r1", "r2"}  # past day excluded
    assert all("workout" in p for p in plan)
    assert state.load(SYNC_FILE) is None  # dry: no state written

    full_sync(runna, FakeGarmin(), mapping, state)
    runna.days["r1"]["parts"][1]["partSets"] = 4
    moved = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
    runna.days["r1"]["date"] = moved
    del runna.days["r2"]
    actions = {p["runnaId"]: p["action"] for p in plan_sync(runna, mapping, state)}
    assert actions == {"r1": "update+reschedule", "r2": "delete"}
