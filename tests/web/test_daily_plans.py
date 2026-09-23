"""Recurring-plan safety and ordering tests; no school API calls."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from jlu_booking.web.db import connect_database, migrate_database
from jlu_booking.web.tasks import CredentialUnavailable, TaskDraft, UserUnavailable
from jlu_booking.web.daily_plans import DailyPlanService

BEIJING = ZoneInfo("Asia/Shanghai")
BASE = datetime(2026, 9, 23, 6, 20, tzinfo=BEIJING)


@pytest.fixture
def state(tmp_path):
    db = connect_database(tmp_path / "web.sqlite3")
    migrate_database(db)
    return db, DailyPlanService(db)


def add_user(db, name, *, role="user", credential=True):
    stamp = BASE.isoformat()
    user_id = db.execute(
        "INSERT INTO users (username,password_hash,role,status,created_at) "
        "VALUES (?,'hash',?,'active',?)", (name, role, stamp),
    ).lastrowid
    if credential:
        db.execute(
            "INSERT INTO user_credentials (user_id,token_ciphertext,token_blind_index,"
            "verified_at,last_status,updated_at) VALUES (?,?,?,?,?,?)",
            (user_id, name.encode(), f"blind-{name}".encode(), stamp, "valid", stamp),
        )
    companion_id = db.execute(
        "INSERT INTO companions (user_id,student_number_ciphertext,name_ciphertext,"
        "verified_at,updated_at) VALUES (?,?,?,?,?)",
        (user_id, b"number", b"name", stamp, stamp),
    ).lastrowid
    return user_id, companion_id


def draft(companion_id, **changes):
    fields = dict(target_day="today", venue="前卫体育馆", sport="羽毛球",
                  companion_id=companion_id, preferred_court_number=3,
                  time_priority=[["15:30", "17:30"], ["17:30", "19:30"]],
                  real_booking_enabled=False)
    fields.update(changes)
    return TaskDraft(**fields)


def insert_task(db, user_id, companion_id, plan_id, status, *, source="daily"):
    stamp = BASE.isoformat()
    return db.execute(
        "INSERT INTO booking_tasks (user_id,execution_date,target_day,venue,sport,companion_id,"
        "preferred_court_number,time_priority_json,real_booking_enabled,status,source,daily_plan_id,"
        "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (user_id, "2026-09-23", "today", "前卫体育馆", "羽毛球", companion_id,
         3, '[["15:30","17:30"]]', 0, status, source, plan_id, stamp, stamp),
    ).lastrowid


def test_save_validates_owner_and_preserves_order_and_explicit_real_mode(state):
    db, plans = state
    alice, companion = add_user(db, "alice")
    bob, other_companion = add_user(db, "bob")
    no_token, no_token_companion = add_user(db, "missing", credential=False)
    admin, admin_companion = add_user(db, "admin", role="admin")
    saved = plans.save(alice, draft(companion), now=BASE)
    assert saved.enabled is False and saved.real_booking_enabled is False
    assert saved.time_priority[:2] == (("15:30", "17:30"), ("17:30", "19:30"))
    real = plans.save(alice, draft(companion, real_booking_enabled=True,
                                   time_priority=[["17:30", "19:30"], ["15:30", "17:30"]]), now=BASE)
    assert real.real_booking_enabled is True
    assert real.time_priority[0] == ("17:30", "19:30")
    with pytest.raises(ValueError):
        plans.save(alice, draft(other_companion), now=BASE)
    with pytest.raises(ValueError):
        plans.save(alice, draft(companion, sport="网球"), now=BASE)
    with pytest.raises(CredentialUnavailable):
        plans.save(no_token, draft(no_token_companion), now=BASE)
    with pytest.raises(UserUnavailable):
        plans.save(admin, draft(admin_companion), now=BASE)
    assert plans.get_for_user(bob) is None


def test_enable_order_resets_only_after_off_to_on(state):
    db, plans = state
    alice, a_companion = add_user(db, "alice")
    bob, b_companion = add_user(db, "bob")
    plans.save(alice, draft(a_companion), now=BASE)
    plans.save(bob, draft(b_companion), now=BASE)
    plans.set_enabled(alice, True, now=BASE)
    later = BASE.replace(minute=21)
    plans.set_enabled(bob, True, now=later)
    first_enabled = plans.get_for_user(alice).enabled_at
    plans.set_enabled(alice, True, now=BASE.replace(minute=22))
    assert plans.get_for_user(alice).enabled_at == first_enabled
    plans.set_enabled(alice, False, now=BASE.replace(minute=23))
    plans.set_enabled(alice, True, now=BASE.replace(minute=24))
    assert plans.get_for_user(bob).enabled_at < plans.get_for_user(alice).enabled_at


def test_disable_after_cutoff_cancels_only_scheduled_daily_task(state):
    db, plans = state
    users = [add_user(db, name) for name in ("alice", "bob", "charlie")]
    for user_id, companion_id in users:
        plans.save(user_id, draft(companion_id), now=BASE)
        plans.set_enabled(user_id, True, now=BASE)
    scheduled = insert_task(db, *users[0], plans.get_for_user(users[0][0]).id, "scheduled")
    running = insert_task(db, *users[1], plans.get_for_user(users[1][0]).id, "running")
    one_shot = insert_task(db, *users[2], None, "scheduled", source="one_shot")
    for user_id, _ in users:
        plans.set_enabled(user_id, False, now=BASE.replace(hour=7, minute=27, second=1))
    statuses = [db.execute("SELECT status FROM booking_tasks WHERE id=?", (task_id,)).fetchone()[0]
                for task_id in (scheduled, running, one_shot)]
    assert statuses == ["cancelled", "running", "scheduled"]


def test_edit_before_cutoff_updates_daily_snapshot_but_after_cutoff_does_not(state):
    db, plans = state
    user_id, companion_id = add_user(db, "alice")
    plan = plans.save(user_id, draft(companion_id), now=BASE)
    task_id = insert_task(db, user_id, companion_id, plan.id, "scheduled")
    earlier = BASE.replace(hour=7, minute=26, second=59)
    plans.save(user_id, draft(companion_id, preferred_court_number=7), now=earlier)
    assert db.execute("SELECT preferred_court_number FROM booking_tasks WHERE id=?", (task_id,)).fetchone()[0] == 7
    cutoff = BASE.replace(hour=7, minute=27, second=0)
    plans.save(user_id, draft(companion_id, preferred_court_number=9), now=cutoff)
    assert plans.get_for_user(user_id).preferred_court_number == 9
    assert db.execute("SELECT preferred_court_number FROM booking_tasks WHERE id=?", (task_id,)).fetchone()[0] == 7


def test_materialize_respects_enable_order_cap_and_restart(state):
    db, plans = state
    owners = []
    for index in range(12):
        user_id, companion_id = add_user(db, f"user{index}")
        owners.append(user_id)
        plans.save(user_id, draft(companion_id), now=BASE)
        plans.set_enabled(user_id, True, now=BASE + timedelta(seconds=index))
    ready = BASE + timedelta(seconds=12)
    created = plans.materialize(ready)
    assert len(created) == 10
    assert plans.materialize(ready) == ()
    assert DailyPlanService(db).materialize(ready) == ()
    booked = [row[0] for row in db.execute(
        "SELECT user_id FROM booking_tasks WHERE source='daily' ORDER BY id"
    )]
    assert booked == owners[:10]
    plans.set_enabled(owners[0], False, now=BASE.replace(minute=30))
    replacement = plans.materialize(BASE.replace(minute=30))
    assert len(replacement) == 1
    assert db.execute("SELECT user_id FROM booking_tasks WHERE id=?", replacement).fetchone()[0] == owners[10]


def test_one_shot_wins_and_materialization_stops_at_beijing_cutoff(state):
    db, plans = state
    owners = []
    for index in range(11):
        user_id, companion_id = add_user(db, f"user{index}")
        owners.append((user_id, companion_id))
        plans.save(user_id, draft(companion_id), now=BASE)
        plans.set_enabled(user_id, True, now=BASE + timedelta(seconds=index))
    one_shot_id = insert_task(db, *owners[0], None, "scheduled", source="one_shot")
    before = BASE.replace(hour=7, minute=26, second=59)
    created = plans.materialize(before.astimezone(timezone.utc))
    assert len(created) == 9
    assert db.execute("SELECT status FROM booking_tasks WHERE id=?", (one_shot_id,)).fetchone()[0] == "scheduled"
    assert db.execute("SELECT COUNT(*) FROM booking_tasks WHERE source='daily' AND user_id=?", (owners[0][0],)).fetchone()[0] == 0
    assert plans.materialize(BASE.replace(hour=7, minute=27)) == ()


def test_materialize_at_exact_cutoff_creates_nothing(state):
    db, plans = state
    user_id, companion_id = add_user(db, "alice")
    plans.save(user_id, draft(companion_id), now=BASE)
    plans.set_enabled(user_id, True, now=BASE)
    assert plans.materialize(BASE.replace(hour=7, minute=27)) == ()
    assert plans.materialize(BASE.replace(hour=7, minute=26, second=59))
