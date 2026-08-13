"""A small, explainable mastery model shared by the library, graph and reports."""

from __future__ import annotations

from app import db

LEVELS = ("unseen", "read", "recalled", "revised", "stable")
LABELS = {
    "unseen": "未接触",
    "read": "已阅读",
    "recalled": "已回忆",
    "revised": "已修订",
    "stable": "稳定掌握",
}


def _row_stats() -> dict[str, dict]:
    """Fetch all per-page learning evidence in a bounded number of queries."""
    with db.cursor() as cur:
        rows = cur.execute(
            "SELECT sessions.page_path, "
            "COUNT(DISTINCT sessions.id) AS session_total, "
            "SUM(CASE WHEN sessions.status = 'done' THEN 1 ELSE 0 END) AS done_total, "
            "COALESCE(MAX(cards.reps), 0) AS max_reps, "
            "COALESCE(MAX(cards.interval), 0) AS max_interval, "
            "COALESCE(SUM(CASE WHEN cards.due <= date('now', 'localtime') THEN 1 ELSE 0 END), 0) AS due_cards "
            "FROM sessions LEFT JOIN cards ON cards.session_id = sessions.id "
            "GROUP BY sessions.page_path"
        ).fetchall()
        gap_rows = cur.execute(
            "SELECT sessions.page_path, "
            "SUM(CASE WHEN gaps.status = 'open' THEN 1 ELSE 0 END) AS open_gaps, "
            "SUM(CASE WHEN gaps.status = 'verified' THEN 1 ELSE 0 END) AS verified_gaps "
            "FROM sessions JOIN gaps ON gaps.session_id = sessions.id GROUP BY sessions.page_path"
        ).fetchall()
    stats = {row["page_path"]: dict(row) for row in rows}
    for row in gap_rows:
        stats.setdefault(row["page_path"], {}).update(dict(row))
    return stats


def _level(reading_status: str, stat: dict) -> str:
    if stat.get("max_reps", 0) >= 2 and stat.get("max_interval", 0) >= 14:
        return "stable"
    if stat.get("done_total", 0):
        return "revised"
    if stat.get("session_total", 0):
        return "recalled"
    if reading_status in {"reading", "read"}:
        return "read"
    return "unseen"


def overview(concepts: list[dict]) -> dict[str, dict]:
    """Return a stable, user-facing learning state for every concept path."""
    stats = _row_stats()
    result: dict[str, dict] = {}
    for concept in concepts:
        stat = stats.get(concept["path"], {})
        level = _level(concept.get("status", "unread"), stat)
        detail = {
            "unseen": "还没有留下阅读或回忆证据",
            "read": "已标记阅读，下一步是合上资料回忆表达",
            "recalled": "已经完成第一次回忆，等待补充和简化复述",
            "revised": "已完成二次表达，等待间隔复习巩固",
            "stable": "已在较长间隔后稳定回忆",
        }[level]
        result[concept["path"]] = {
            "level": level,
            "label": LABELS[level],
            "detail": detail,
            "open_gaps": int(stat.get("open_gaps") or 0),
            "due_cards": int(stat.get("due_cards") or 0),
            "session_total": int(stat.get("session_total") or 0),
        }
    return result


def weakest_first(concepts: list[dict]) -> list[dict]:
    """Sort concepts by the next useful learning action, then title."""
    states = overview(concepts)
    order = {level: index for index, level in enumerate(LEVELS)}
    return sorted(concepts, key=lambda concept: (order[states[concept["path"]]["level"]], concept["title"]))
