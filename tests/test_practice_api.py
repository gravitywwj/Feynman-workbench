"""应用自测 API、资格、备份与页面关联边界。"""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.services import practice_sessions
from app.services.study_sessions import export_learning_data, import_learning_data

client = TestClient(app)


def add_task_pages(wiki):
    paths = sorted({path for task in practice_sessions.TASKS for path in task["page_paths"]})
    for path in paths:
        target = wiki / "pages" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"---\ntitle: {Path(path).stem}\ntype: concept\n---\n\n# {Path(path).stem}\n", encoding="utf-8")
    return paths


def freeze_clock(monkeypatch, day=12):
    tz = datetime.now().astimezone().tzinfo
    value = {"now": datetime(2026, 10, day, 10, 0, tzinfo=tz)}
    monkeypatch.setattr(practice_sessions, "_now", lambda: value["now"])
    return value


def start_attempt(page_path, task_key, **overrides):
    payload = {
        "page_path": page_path, "task_key": task_key, "mode": "assessment",
        "familiarity": "unseen", "assistance": "none",
    }
    payload.update(overrides)
    response = client.post("/api/practice/attempts", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def expected_for(task_key):
    return next(task["expected"] for task in practice_sessions.TASKS if task["task_key"] == task_key)


def submit(uid, answer, **extra):
    response = client.post(f"/api/practice/attempts/{uid}/submit", json={"answer": answer, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def test_unfinished_practice_draft_resumes_from_home_and_backup(wiki, monkeypatch):
    add_task_pages(wiki)
    task = next(task for task in practice_sessions.TASKS if task["phase"] == "application")
    page_path = task["page_paths"][0]
    attempt = start_attempt(page_path, task["task_key"])
    field = task["fields"][0]
    draft = {field["key"]: "0.5" if field["type"] == "number" else "2"}
    invalid = client.put(f"/api/practice/attempts/{attempt['attempt_uid']}/draft", json={"answer": {"forged": "value"}})
    assert invalid.status_code == 400
    saved = client.put(f"/api/practice/attempts/{attempt['attempt_uid']}/draft", json={"answer": draft, "assistance": "none"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["answer"] == draft
    assert saved.json()["submitted_at"] is None and saved.json()["result"] is None
    action = client.get("/api/study/home").json()
    assert action["type"] == "practice_resume"
    assert action["attempt_uid"] == attempt["attempt_uid"]
    with db.cursor() as cur:
        assert cur.execute("SELECT COUNT(*) FROM learning_events WHERE event_type = 'practice_submitted'").fetchone()[0] == 0

    exported = export_learning_data()
    target_db = wiki / "practice-draft-backup.db"
    monkeypatch.setattr(db, "DB_PATH", target_db)
    db.init_db()
    imported = import_learning_data(exported)
    assert imported["imported"]["practice_attempts"] == 1
    restored = client.get(f"/api/practice/attempts/{attempt['attempt_uid']}").json()
    assert restored["answer"] == draft
    assert client.get("/api/study/home").json()["attempt_uid"] == attempt["attempt_uid"]


def application_pass(page_path, family):
    task = next(item for item in practice_sessions.TASKS if item["family"] == family and item["phase"] == "application")
    attempt = start_attempt(page_path, task["task_key"])
    return submit(attempt["attempt_uid"], task["expected"])


@pytest.mark.parametrize("task", practice_sessions.TASKS, ids=lambda task: task["task_key"])
def test_eighteen_versioned_tasks_grade_correct_and_incorrect_answers_offline(wiki, monkeypatch, task):
    add_task_pages(wiki)
    clock = freeze_clock(monkeypatch)
    page_path = task["page_paths"][0]
    tasks = client.get("/api/practice/tasks", params={"page_path": page_path}).json()
    assert "expected" not in str(tasks) and "solution" not in str(tasks)
    if task["phase"] != "application":
        application_pass(page_path, task["family"])
        if task["phase"] == "delayed":
            clock["now"] += timedelta(days=7)

    attempt = start_attempt(page_path, task["task_key"])
    assert "solution" not in attempt and "expected" not in str(attempt["task"])
    restored = client.get(f"/api/practice/attempts/{attempt['attempt_uid']}").json()
    assert "solution" not in restored
    passed = submit(attempt["attempt_uid"], task["expected"])
    assert passed["result"]["matches"] is True
    assert passed["result"]["eligible"] is True
    assert passed["check_kind"] == "deterministic"

    wrong = dict(task["expected"])
    field = task["fields"][0]
    if field["type"] == "integer":
        wrong[field["key"]] += 1
    elif field["type"] == "number":
        wrong[field["key"]] = min(1, wrong[field["key"]] + 0.2)
    elif field["type"] == "boolean":
        wrong[field["key"]] = not wrong[field["key"]]
    else:
        wrong[field["key"]] = next(value for value in field["options"] if value != wrong[field["key"]])
    retry = start_attempt(page_path, task["task_key"])
    failed = submit(retry["attempt_uid"], wrong)
    assert failed["result"]["matches"] is False


def test_task_versions_and_phase_matrix_preserve_the_original_six(wiki):
    assert len(practice_sessions.TASKS) == 18
    for family in {task["family"] for task in practice_sessions.TASKS}:
        for phase in practice_sessions.PHASES:
            matching = [task for task in practice_sessions.TASKS if task["family"] == family and task["phase"] == phase]
            assert len(matching) == 3
    original = {"metrics-apply", "metrics-transfer", "metrics-delayed", "validation-apply", "validation-transfer", "validation-delayed"}
    assert {task["task_key"] for task in practice_sessions.TASKS if task.get("version", 1) == 1} == original
    assert all(task.get("version", 1) == 2 for task in practice_sessions.TASKS if task["task_key"] not in original)


@pytest.mark.parametrize("task", practice_sessions.TASKS, ids=lambda task: task["task_key"])
def test_each_task_keeps_attempt_open_after_illegal_answer(wiki, monkeypatch, task):
    add_task_pages(wiki)
    clock = freeze_clock(monkeypatch)
    page_path = task["page_paths"][0]
    if task["phase"] != "application":
        application_pass(page_path, task["family"])
        if task["phase"] == "delayed":
            clock["now"] += timedelta(days=7)
    attempt = start_attempt(page_path, task["task_key"])
    illegal = dict(task["expected"])
    illegal.pop(next(iter(illegal)))
    rejected = client.post(f"/api/practice/attempts/{attempt['attempt_uid']}/submit", json={"answer": illegal})
    assert rejected.status_code == 400
    assert client.get(f"/api/practice/attempts/{attempt['attempt_uid']}").json()["submitted_at"] is None
    passed = submit(attempt["attempt_uid"], task["expected"])
    assert passed["result"]["matches"] is True


def test_invalid_answers_and_untrusted_fields_do_not_complete_attempt(wiki):
    add_task_pages(wiki)
    page_path = "AI/foundation/evaluation-basics.md"
    assert client.post("/api/practice/attempts", json={
        "page_path": page_path, "task_key": "metrics-apply", "mode": "assessment",
        "familiarity": "unseen", "assistance": "none", "eligible": True,
    }).status_code == 422
    assert client.post("/api/practice/attempts", json={
        "page_path": page_path, "task_key": "no-such-task", "mode": "assessment",
        "familiarity": "unseen", "assistance": "none",
    }).status_code == 404
    assert client.get("/api/practice/attempts/not-a-uuid").status_code == 400
    assert client.get(f"/api/practice/attempts/{uuid4()}").status_code == 404
    assert client.get("/api/practice/tasks", params={"page_path": "../outside.md"}).status_code == 400
    assert client.get("/api/practice/tasks", params={"page_path": "AI/foundation/missing.md"}).status_code == 404

    attempt = start_attempt(page_path, "metrics-apply")
    expected = expected_for("metrics-apply")
    invalid_answers = []
    as_bool = dict(expected, tp=True)
    invalid_answers.append(as_bool)
    missing = dict(expected)
    missing.pop("recall")
    invalid_answers.append(missing)
    extra = dict(expected, forged=True)
    invalid_answers.append(extra)
    negative = dict(expected, fp=-1)
    invalid_answers.append(negative)
    string_number = dict(expected, tp="1")
    invalid_answers.append(string_number)
    for answer in invalid_answers:
        response = client.post(f"/api/practice/attempts/{attempt['attempt_uid']}/submit", json={"answer": answer})
        assert response.status_code == 400

    nan_answer = dict(expected, precision=float("nan"))
    response = client.post(
        f"/api/practice/attempts/{attempt['attempt_uid']}/submit",
        content=json.dumps({"answer": nan_answer}, allow_nan=True),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    infinite_answer = dict(expected, precision=float("inf"))
    response = client.post(
        f"/api/practice/attempts/{attempt['attempt_uid']}/submit",
        content=json.dumps({"answer": infinite_answer}, allow_nan=True),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert client.get(f"/api/practice/attempts/{attempt['attempt_uid']}").json()["submitted_at"] is None
    with db.cursor() as cur:
        assert cur.execute("SELECT COUNT(*) FROM learning_events WHERE event_type = 'practice_submitted'").fetchone()[0] == 0


def test_same_uid_submission_is_idempotent_under_concurrent_retries(wiki):
    add_task_pages(wiki)
    attempt = start_attempt("AI/foundation/evaluation-basics.md", "metrics-apply")
    answer = expected_for("metrics-apply")

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(
            lambda _: client.post(f"/api/practice/attempts/{attempt['attempt_uid']}/submit", json={"answer": answer}),
            range(2),
        ))
    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json()["result"] == responses[1].json()["result"]
    conflict = client.post(
        f"/api/practice/attempts/{attempt['attempt_uid']}/submit",
        json={"answer": dict(answer, tp=2)},
    )
    assert conflict.status_code == 409
    with db.cursor() as cur:
        assert cur.execute("SELECT COUNT(*) FROM practice_attempts WHERE attempt_uid = ? AND submitted_at IS NOT NULL", (attempt["attempt_uid"],)).fetchone()[0] == 1
        assert cur.execute("SELECT COUNT(*) FROM learning_events WHERE event_type = 'practice_submitted'").fetchone()[0] == 1


def test_solution_and_assistance_cannot_create_independent_evidence(wiki):
    add_task_pages(wiki)
    attempt = start_attempt("AI/foundation/evaluation-basics.md", "metrics-apply", mode="practice", familiarity="unknown")
    solution = client.post(f"/api/practice/attempts/{attempt['attempt_uid']}/solution")
    assert solution.status_code == 200
    assert solution.json()["before_submission_notice"]
    result = submit(attempt["attempt_uid"], expected_for("metrics-apply"), assistance="none")
    assert result["result"]["matches"] is True
    assert result["result"]["eligible"] is False
    assert result["assistance"] == "solution"
    assert result["solution_viewed_at"]


def test_repeating_the_same_task_never_creates_a_new_first_pass(wiki):
    add_task_pages(wiki)
    page_path = "AI/foundation/evaluation-basics.md"
    application_pass(page_path, "metrics")
    repeated = start_attempt(page_path, "metrics-apply")
    result = submit(repeated["attempt_uid"], expected_for("metrics-apply"))
    assert result["result"]["matches"] is True
    assert result["result"]["eligible"] is False
    assert result["result"]["repeated"] is True


@pytest.mark.parametrize(("familiarity", "assistance"), [
    ("seen", "none"), ("unknown", "none"), ("unseen", "hint"),
])
def test_transfer_with_prior_exposure_or_help_stays_practice(wiki, familiarity, assistance):
    add_task_pages(wiki)
    page_path = "AI/foundation/evaluation-basics.md"
    application_pass(page_path, "metrics")
    attempt = start_attempt(page_path, "metrics-transfer", familiarity=familiarity, assistance=assistance)
    result = submit(attempt["attempt_uid"], expected_for("metrics-transfer"))
    assert result["result"]["matches"] is True
    assert result["result"]["eligible"] is False
    concept = next(item for item in client.get("/api/concepts").json()["concepts"] if item["path"] == page_path)
    assert concept["mastery"]["practice_evidence"]["transfer"]["eligible_count"] == 0


def test_transfer_requires_same_page_family_and_delay_uses_server_date(wiki, monkeypatch):
    add_task_pages(wiki)
    clock = freeze_clock(monkeypatch)
    page_path = "AI/foundation/evaluation-basics.md"
    locked = client.post("/api/practice/attempts", json={
        "page_path": page_path, "task_key": "metrics-transfer", "mode": "assessment",
        "familiarity": "unseen", "assistance": "none",
    })
    assert locked.status_code == 400
    application_pass(page_path, "metrics")
    cross_page = client.post("/api/practice/attempts", json={
        "page_path": "AI/foundation/ai-practice-lab.md", "task_key": "metrics-transfer", "mode": "assessment",
        "familiarity": "unseen", "assistance": "none",
    })
    assert cross_page.status_code == 400
    assert client.get("/api/practice/tasks", params={"page_path": page_path}).json()["tasks"]
    cross_family = client.post("/api/practice/attempts", json={
        "page_path": page_path, "task_key": "validation-transfer", "mode": "assessment",
        "familiarity": "unseen", "assistance": "none",
    })
    assert cross_family.status_code == 404

    state = client.get("/api/practice/tasks", params={"page_path": page_path}).json()
    delayed = next(item for item in state["tasks"] if item["task_key"] == "metrics-delayed")
    assert delayed["open"] is False and delayed["due_on"] == "2026-10-19"
    clock["now"] += timedelta(days=6)
    assert next(item for item in client.get("/api/practice/tasks", params={"page_path": page_path}).json()["tasks"] if item["task_key"] == "metrics-delayed")["open"] is False
    clock["now"] += timedelta(days=1)
    delayed = next(item for item in client.get("/api/practice/tasks", params={"page_path": page_path}).json()["tasks"] if item["task_key"] == "metrics-delayed")
    assert delayed["open"] is True
    attempt = start_attempt(page_path, "metrics-delayed")
    assert attempt["precursor_uid"]


def test_latest_eligible_failure_stays_visible_after_helped_retry(wiki):
    add_task_pages(wiki)
    page_path = "AI/foundation/evaluation-basics.md"
    wrong = dict(expected_for("metrics-apply"), tp=0)
    first = start_attempt(page_path, "metrics-apply")
    failed = submit(first["attempt_uid"], wrong)
    assert failed["result"]["eligible"] is True and failed["result"]["matches"] is False

    retry = start_attempt(page_path, "metrics-apply", assistance="hint")
    helped = submit(retry["attempt_uid"], expected_for("metrics-apply"), assistance="hint")
    assert helped["result"]["eligible"] is False and helped["result"]["matches"] is True
    concept = next(item for item in client.get("/api/concepts").json()["concepts"] if item["path"] == page_path)
    latest = concept["mastery"]["practice_evidence"]["application"]["latest"]
    assert latest["result"] == "需复查"


def test_page_concepts_graph_and_report_share_separate_practice_evidence(wiki):
    add_task_pages(wiki)
    page_path = "AI/foundation/evaluation-basics.md"
    before_schedule = client.get("/api/study/reviews/summary").json()
    attempt = start_attempt(page_path, "metrics-apply")
    submit(attempt["attempt_uid"], expected_for("metrics-apply"))

    page = client.get("/api/concepts/page", params={"path": page_path}).json()
    concept = next(item for item in client.get("/api/concepts").json()["concepts"] if item["path"] == page_path)
    graph = next(item for item in client.get("/api/concepts/graph").json()["nodes"] if item["id"] == page_path)
    evidence = concept["mastery"]["practice_evidence"]
    assert page["meta"]["mastery"]["practice_evidence"] == evidence
    assert graph["mastery"]["practice_evidence"] == evidence
    application = evidence["application"]
    assert application["count"] == 1 and application["eligible_count"] == 1 and application["matches"] == 1
    assert "total" not in concept["mastery"]
    report = client.get("/api/study/weekly-report").json()
    assert report["practice"]["application"]["assessments"] == 1
    assert report["practice"]["application"]["matches"] == 1
    assert report["summary"]["source_checks"] == 0
    assert client.get("/api/study/reviews/summary").json() == before_schedule


def test_practice_evidence_and_weekly_report_keep_family_phase_separate(wiki):
    add_task_pages(wiki)
    page_path = "AI/foundation/ai-practice-lab.md"
    for family in ("metrics", "validation"):
        application_pass(page_path, family)
    metrics = start_attempt(page_path, "metrics-transfer")
    submit(metrics["attempt_uid"], expected_for("metrics-transfer"))
    validation = start_attempt(page_path, "validation-transfer")
    wrong = dict(expected_for("validation-transfer"), violation="unknown_field")
    submit(validation["attempt_uid"], wrong)

    concept = next(item for item in client.get("/api/concepts").json()["concepts"] if item["path"] == page_path)
    transfer = concept["mastery"]["practice_evidence"]["by_family"]
    assert transfer["metrics"]["transfer"]["latest"]["result"] == "结果匹配"
    assert transfer["validation"]["transfer"]["latest"]["result"] == "需复查"
    report = client.get("/api/study/weekly-report").json()["practice"]["by_family"]
    assert report["metrics"]["transfer"]["matches"] == 1
    assert report["validation"]["transfer"]["needs_review"] == 1


def test_external_results_are_self_reported_and_urls_are_restricted(wiki):
    add_task_pages(wiki)
    page_path = "AI/foundation/evaluation-basics.md"
    bad = client.post("/api/practice/external-attempts", json={
        "page_path": page_path, "title": "作业", "source_url": "javascript:alert(1)",
    })
    assert bad.status_code == 400
    saved = client.post("/api/practice/external-attempts", json={
        "page_path": page_path, "title": "<script>alert(1)</script>",
        "source_url": "https://example.com/task", "reported_result": "pass",
        "artifact_text": "<img src=x onerror=alert(1)>", "familiarity": "unseen", "assistance": "none",
    })
    assert saved.status_code == 200
    data = saved.json()
    assert data["check_kind"] == "self_reported"
    assert data["result"]["label"] == "用户自报，未自动核验"
    assert data["result"].get("eligible") is None
    assert client.get("/api/practice/history", params={"page_path": page_path}).json()["attempts"][0]["source_url"] == "https://example.com/task"
    report = client.get("/api/study/weekly-report").json()
    assert report["practice"]["external"]["self_reported"] == 1


def test_database_upgrade_is_idempotent_and_keeps_review_schedule(wiki):
    add_task_pages(wiki)
    with db.cursor() as cur:
        cur.execute("INSERT INTO sessions (page_path, page_title, concept) VALUES ('AI/rag/query-rewriting.md', 'Query Rewriting', 'Query Rewriting')")
        session_id = cur.lastrowid
        cur.execute("INSERT INTO cards (session_id, question, answer, due, interval, reps) VALUES (?, 'q', 'a', '2099-01-01', 8, 3)", (session_id,))
        card_id = cur.lastrowid
        cur.execute("DROP TABLE practice_attempts")
    db.init_db()
    db.init_db()
    with db.cursor() as cur:
        card = cur.execute("SELECT due, interval, reps FROM cards WHERE id = ?", (card_id,)).fetchone()
        assert tuple(card) == ("2099-01-01", 8, 3)
        assert cur.execute("SELECT COUNT(*) FROM sessions WHERE id = ?", (session_id,)).fetchone()[0] == 1
        assert cur.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'practice_attempts'").fetchone()


def test_backup_versions_regrade_dedupe_and_reject_uid_conflicts(wiki, monkeypatch):
    add_task_pages(wiki)
    freeze_clock(monkeypatch)
    page_path = "AI/foundation/evaluation-basics.md"
    attempt = start_attempt(page_path, "metrics-apply")
    wrong = dict(expected_for("metrics-apply"), tp=0)
    submit(attempt["attempt_uid"], wrong)
    linked_page = "AI/foundation/ai-practice-lab.md"
    application = application_pass(linked_page, "metrics")
    transfer = start_attempt(linked_page, "metrics-transfer")
    submit(transfer["attempt_uid"], expected_for("metrics-transfer"))
    exported = export_learning_data()
    assert exported["version"] == 9 and exported["practice_attempts"]
    assert next(item for item in exported["practice_attempts"] if item["attempt_uid"] == transfer["attempt_uid"])["precursor_uid"] == application["attempt_uid"]

    roundtrip_db = wiki / "roundtrip.db"
    monkeypatch.setattr(db, "DB_PATH", roundtrip_db)
    db.init_db()
    full_import = import_learning_data(exported)
    assert full_import["imported"]["practice_attempts"] == len(exported["practice_attempts"])
    roundtrip = export_learning_data()
    by_uid = {item["attempt_uid"]: item for item in roundtrip["practice_attempts"]}
    assert by_uid[transfer["attempt_uid"]]["precursor_uid"] == application["attempt_uid"]
    assert practice_sessions._decode(by_uid[transfer["attempt_uid"]]["result_json"], {})["eligible"] is True
    assert import_learning_data(roundtrip)["imported"]["practice_attempts"] == 0

    target_db = wiki / "import-target.db"
    monkeypatch.setattr(db, "DB_PATH", target_db)
    db.init_db()
    for version in range(1, 9):
        old = import_learning_data({
            "format": exported["format"], "version": version,
            "notes": [{"page_path": f"legacy-{version}.md", "content": "legacy", "created_at": "2026-01-01", "updated_at": "2026-01-01"}],
        })
        assert old["imported"]["notes"] == 1

    incoming = {**exported, "practice_attempts": [dict(exported["practice_attempts"][0], result_json='{"matches":true,"eligible":true}')], "learning_events": []}
    preview = import_learning_data(incoming, dry_run=True)
    assert preview["practice"]["new"] == 1
    with db.cursor() as cur:
        assert cur.execute("SELECT COUNT(*) FROM practice_attempts").fetchone()[0] == 0
    imported = import_learning_data(incoming)
    assert imported["imported"]["practice_attempts"] == 1
    repeated = import_learning_data(incoming)
    assert repeated["imported"]["practice_attempts"] == 0
    with db.cursor() as cur:
        row = cur.execute("SELECT attempt_uid, result_json, check_kind FROM practice_attempts").fetchone()
        assert row["attempt_uid"] == attempt["attempt_uid"]
        assert practice_sessions._decode(row["result_json"], {})["matches"] is False
        assert row["check_kind"] == "deterministic"
        assert cur.execute("SELECT COUNT(*) FROM learning_events WHERE event_type = 'practice_submitted'").fetchone()[0] == 1

    conflict = {**incoming, "practice_attempts": [dict(incoming["practice_attempts"][0], page_title="different title")]}
    with pytest.raises(practice_sessions.ConflictError):
        import_learning_data(conflict, dry_run=True)
    conflicting_rows = [
        dict(incoming["practice_attempts"][0], attempt_uid=str(uuid4()), task_version=99),
        dict(incoming["practice_attempts"][0], page_title="different title"),
    ]
    with db.cursor() as cur:
        practice_count = cur.execute("SELECT COUNT(*) FROM practice_attempts").fetchone()[0]
    with pytest.raises(practice_sessions.ConflictError):
        import_learning_data({**incoming, "practice_attempts": conflicting_rows, "learning_events": []})
    with db.cursor() as cur:
        assert cur.execute("SELECT COUNT(*) FROM practice_attempts").fetchone()[0] == practice_count

    unknown = dict(incoming["practice_attempts"][0], attempt_uid=str(uuid4()), task_version=99,
                   result_json='{"matches":true,"eligible":true}')
    transfer_task = next(task for task in practice_sessions.TASKS if task["task_key"] == "metrics-transfer")
    transfer = dict(
        incoming["practice_attempts"][0], attempt_uid=str(uuid4()), task_key="metrics-transfer",
        task_snapshot_json=practice_sessions._json(practice_sessions._snapshot(transfer_task)),
        phase="transfer", answer_json=practice_sessions._json(transfer_task["expected"]),
        created_at="2026-10-12T10:00:10", submitted_at="2026-10-12T10:00:11",
        precursor_uid=attempt["attempt_uid"], result_json='{"matches":true,"eligible":true}',
    )
    import_learning_data({**incoming, "practice_attempts": [unknown, transfer], "learning_events": []})
    with db.cursor() as cur:
        rows = cur.execute(
            "SELECT task_key, task_version, result_json, check_kind, precursor_uid FROM practice_attempts "
            "WHERE attempt_uid IN (?, ?) ORDER BY task_version DESC",
            (unknown["attempt_uid"], transfer["attempt_uid"]),
        ).fetchall()
        by_key = {row["task_key"]: row for row in rows}
        assert by_key["metrics-apply"]["check_kind"] == "unverified"
        assert practice_sessions._decode(by_key["metrics-apply"]["result_json"], {})["eligible"] is False
        assert practice_sessions._decode(by_key["metrics-transfer"]["result_json"], {})["eligible"] is False
        assert by_key["metrics-transfer"]["precursor_uid"] == attempt["attempt_uid"]


def test_import_rejects_forged_phase_on_current_builtin_task(wiki, monkeypatch):
    add_task_pages(wiki)
    freeze_clock(monkeypatch)
    page_path = "AI/foundation/evaluation-basics.md"
    application_pass(page_path, "metrics")
    transfer = start_attempt(page_path, "metrics-transfer")
    submit(transfer["attempt_uid"], expected_for("metrics-transfer"))
    exported = export_learning_data()
    forged = next(item for item in exported["practice_attempts"] if item["attempt_uid"] == transfer["attempt_uid"])
    forged["phase"] = "delayed"

    target_db = wiki / "forged-import.db"
    monkeypatch.setattr(db, "DB_PATH", target_db)
    db.init_db()
    payload = {
        **exported,
        "notes": [{"page_path": "must-not-import.md", "content": "safe", "created_at": "2026-01-01", "updated_at": "2026-01-01"}],
    }
    with pytest.raises(ValueError, match="阶段与题目定义不一致"):
        import_learning_data(payload)
    with db.cursor() as cur:
        assert cur.execute("SELECT COUNT(*) FROM practice_attempts").fetchone()[0] == 0
        assert cur.execute("SELECT COUNT(*) FROM notes WHERE page_path = 'must-not-import.md'").fetchone()[0] == 0


def test_orphaned_practice_page_can_be_relinked_and_history_follows(wiki):
    add_task_pages(wiki)
    old_path = "AI/foundation/evaluation-basics.md"
    old_file = wiki / "pages" / old_path
    client.post("/api/practice/external-attempts", json={"page_path": old_path, "title": "课程作业"})
    attempt = start_attempt(old_path, "metrics-apply")
    submit(attempt["attempt_uid"], expected_for("metrics-apply"))
    old_file.unlink()
    orphan = next(item for item in client.get("/api/study/orphans").json()["orphans"] if item["page_path"] == old_path)
    assert orphan["page_title"] == "evaluation-basics"

    new_path = "AI/foundation/renamed-evaluation.md"
    new_file = wiki / "pages" / new_path
    new_file.write_text("---\ntitle: 新评价页\ntype: concept\n---\n\n# 新评价页\n", encoding="utf-8")
    response = client.post("/api/study/relink", json={"old_path": old_path, "new_path": new_path})
    assert response.status_code == 200
    assert client.get("/api/practice/history", params={"page_path": new_path}).json()["attempts"]
    assert client.get("/api/practice/tasks", params={"page_path": new_path}).json()["has_tasks"] is True
    active = client.get("/api/study/weekly-report").json()["active_concepts"]
    assert any(item["path"] == new_path for item in active)
    assert not any(item["path"] == old_path for item in active)
