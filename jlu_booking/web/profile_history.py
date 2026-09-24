"""Read-only, owner-scoped history for Web booking attempts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class BookingHistoryItem:
    kind: str
    record_id: str
    occurred_at: str
    target_date: str
    venue: str
    sport: str
    status: str
    source: str


@dataclass(frozen=True)
class BookingHistoryPage:
    items: tuple[BookingHistoryItem, ...]
    page: int
    has_next: bool


def list_history(connection, user_id: int, *, page: int = 1, page_size: int = 20) -> BookingHistoryPage:
    """List one account's automatic tasks and actually submitted manual attempts."""
    if page < 1 or not 1 <= page_size <= 50:
        raise ValueError("预约记录分页参数无效。")
    rows = connection.execute(
        "SELECT kind, record_id, occurred_at, execution_date, target_day, venue, sport, status, source "
        "FROM ("
        "SELECT 'auto' AS kind, CAST(t.id AS TEXT) AS record_id, t.created_at AS occurred_at, "
        "t.execution_date AS execution_date, t.target_day AS target_day, "
        "t.venue AS venue, t.sport AS sport, t.status AS status, t.source AS source "
        "FROM booking_tasks AS t WHERE t.user_id=? "
        "UNION ALL "
        "SELECT 'manual' AS kind, a.id AS record_id, a.updated_at AS occurred_at, "
        "c.query_date AS execution_date, 'today' AS target_day, "
        "c.venue AS venue, c.sport AS sport, a.status AS status, 'manual' AS source "
        "FROM manual_booking_attempts AS a "
        "JOIN manual_candidates AS c ON c.id=a.candidate_id "
        "WHERE a.user_id=? AND c.user_id=a.user_id AND a.status!='prechecked'"
        ") ORDER BY occurred_at DESC, kind DESC, record_id DESC LIMIT ? OFFSET ?",
        (int(user_id), int(user_id), page_size + 1, (page - 1) * page_size),
    ).fetchall()
    items = []
    for row in rows[:page_size]:
        target = date.fromisoformat(row["execution_date"])
        if row["kind"] == "auto" and row["target_day"] == "tomorrow":
            target += timedelta(days=1)
        items.append(BookingHistoryItem(
            kind=row["kind"], record_id=row["record_id"],
            occurred_at=row["occurred_at"], target_date=target.isoformat(),
            venue=row["venue"], sport=row["sport"], status=row["status"],
            source=row["source"],
        ))
    return BookingHistoryPage(tuple(items), page, len(rows) > page_size)
