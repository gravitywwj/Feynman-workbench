"""A small, explainable mastery model shared by the library, graph and reports."""

from __future__ import annotations

from app import db

LEVELS = ("unseen", "read", "recalled", "checked", "maintaining")
LABELS = {
    "unseen": "未接触",
    "read": "已接触",
    "recalled": "能回忆",
    "checked": "已核对",
    "maintaining": "保持中",
}


def _row_stats() -> dict[str, dict]:
    """Fetch all per-page learning evidence in a bounded number of queries."""
    with db.cursor() as cur:
        session_rows = cur.execute(
            "SELECT page_path, COUNT(*) AS session_total, "
            "SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) AS done_total "
            "FROM sessions GROUP BY page_path"
        ).fetchall()
        card_rows = cur.execute(
            "SELECT sessions.page_path, COALESCE(MAX(cards.reps), 0) AS max_reps, "
            "COALESCE(MAX(cards.interval), 0) AS max_interval, "
            "COALESCE(SUM(CASE WHEN cards.due <= date('now', 'localtime') THEN 1 ELSE 0 END), 0) AS due_cards "
            "FROM sessions LEFT JOIN cards ON cards.session_id = sessions.id GROUP BY sessions.page_path"
        ).fetchall()
        gap_rows = cur.execute(
            "SELECT sessions.page_path, "
            "SUM(CASE WHEN gaps.status = 'open' THEN 1 ELSE 0 END) AS open_gaps, "
            "SUM(CASE WHEN gaps.status = 'verified' THEN 1 ELSE 0 END) AS verified_gaps "
            "FROM sessions JOIN gaps ON gaps.session_id = sessions.id GROUP BY sessions.page_path"
        ).fetchall()
        review_rows = cur.execute(
            "SELECT sessions.page_path, "
            "SUM(CASE WHEN review_attempts.verdict = 'pass' "
            "AND review_attempts.evidence_level IN ('source_standard', 'source_reviewed') THEN 1 ELSE 0 END) AS source_checks "
            ", COUNT(DISTINCT CASE WHEN review_attempts.verdict = 'pass' "
            "AND review_attempts.evidence_level IN ('source_standard', 'source_reviewed') "
            "THEN substr(review_attempts.created_at, 1, 10) END) AS source_check_days "
            ", COALESCE(CAST(julianday(MAX(CASE WHEN review_attempts.verdict = 'pass' "
            "AND review_attempts.evidence_level IN ('source_standard', 'source_reviewed') THEN review_attempts.created_at END)) "
            "- julianday(MIN(CASE WHEN review_attempts.verdict = 'pass' "
            "AND review_attempts.evidence_level IN ('source_standard', 'source_reviewed') THEN review_attempts.created_at END)) AS INTEGER), 0) AS source_check_span_days "
            "FROM sessions JOIN cards ON cards.session_id = sessions.id "
            "LEFT JOIN review_attempts ON review_attempts.card_id = cards.id "
            "GROUP BY sessions.page_path"
        ).fetchall()
        confidence_rows = cur.execute(
            "SELECT page_path, confidence FROM self_assessments"
        ).fetchall()
    stats = {row["page_path"]: dict(row) for row in session_rows}
    for row in card_rows:
        stats.setdefault(row["page_path"], {}).update(dict(row))
    for row in gap_rows:
        stats.setdefault(row["page_path"], {}).update(dict(row))
    for row in review_rows:
        stats.setdefault(row["page_path"], {}).update(dict(row))
    for row in confidence_rows:
        stats.setdefault(row["page_path"], {}).update({"self_confidence": row["confidence"]})
    return stats


def _level(reading_status: str, stat: dict) -> str:
    source_checks = int(stat.get("source_checks") or 0)
    verified_gaps = int(stat.get("verified_gaps") or 0)
    if source_checks >= 2 and int(stat.get("source_check_days") or 0) >= 2 and int(stat.get("source_check_span_days") or 0) >= 14:
        return "maintaining"
    if source_checks >= 1 or verified_gaps >= 1:
        return "checked"
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
            "read": "已留下阅读记录，下一步是合上资料回忆表达",
            "recalled": "已经留下回忆表达，尚未依据原文或明确标准核对",
            "checked": "至少一次回答已依据 Wiki 原文或明确要点完成核对",
            "maintaining": "已在至少 14 天内多次通过来源核对，仍应继续复习",
        }[level]
        result[concept["path"]] = {
            "level": level,
            "label": LABELS[level],
            "detail": detail,
            "open_gaps": int(stat.get("open_gaps") or 0),
            "due_cards": int(stat.get("due_cards") or 0),
            "session_total": int(stat.get("session_total") or 0),
            "source_checks": int(stat.get("source_checks") or 0),
            "source_check_span_days": int(stat.get("source_check_span_days") or 0),
            "self_confidence": int(stat["self_confidence"]) if stat.get("self_confidence") else None,
        }
    return result


def weakest_first(concepts: list[dict]) -> list[dict]:
    """Sort concepts by the next useful learning action, then title."""
    states = overview(concepts)
    order = {level: index for index, level in enumerate(LEVELS)}
    return sorted(concepts, key=lambda concept: (order[states[concept["path"]]["level"]], concept["title"]))
