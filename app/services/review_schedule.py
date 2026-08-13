"""间隔复习的排程规则。

规则故意保持小而可解释：第一次复习留出一天的间隔；能回忆时依次拉长到
3、7 天，再依据卡片自己的难度系数延伸。评分不是考试分数，而是下一次复习
的时间信号。
"""
from datetime import date, timedelta

INITIAL_INTERVAL_DAYS = 1


def initial_due(today: date | None = None) -> str:
    """学习完成后至少隔一天再进入第一次复习队列。"""
    return ((today or date.today()) + timedelta(days=INITIAL_INTERVAL_DAYS)).isoformat()


def next_schedule(*, interval: int, reps: int, ease: float, rating: str, today: date | None = None) -> dict:
    """根据一次自评给出下一次时间，返回字段可直接写入 cards。"""
    if rating not in {"again", "hard", "good", "easy"}:
        raise ValueError("评分必须为 again、hard、good 或 easy")

    old_interval = interval or 0
    old_reps = reps or 0
    old_ease = ease or 2.5
    if rating == "again":
        next_interval, next_reps, next_ease = 1, 0, max(1.3, old_ease - 0.2)
    elif rating == "hard":
        next_interval = 1 if old_reps <= 1 else max(2, round(max(1, old_interval) * 1.2))
        next_reps, next_ease = old_reps + 1, max(1.3, old_ease - 0.15)
    elif rating == "good":
        next_interval = 3 if old_reps == 0 else 7 if old_reps == 1 else max(10, round(old_interval * old_ease))
        next_reps, next_ease = old_reps + 1, old_ease
    else:
        next_interval = 7 if old_reps == 0 else 14 if old_reps == 1 else max(14, round(old_interval * old_ease * 1.3))
        next_reps, next_ease = old_reps + 1, min(3.0, old_ease + 0.15)
    due = (today or date.today()) + timedelta(days=next_interval)
    return {
        "interval": next_interval,
        "reps": next_reps,
        "ease": next_ease,
        "due": due.isoformat(),
    }


def stage_label(reps: int, interval: int) -> str:
    """供 UI 展示当前排程所处的、易理解的阶段。"""
    if reps <= 0:
        return "首次回忆"
    if interval <= 3:
        return "巩固回忆"
    if interval <= 14:
        return "稳定回忆"
    return "长期保持"
