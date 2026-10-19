"""Pinned course reading, learner drafts, execution gate, and backup boundaries."""
import hashlib
from datetime import datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.services import learning_lab
from app.services.study_sessions import export_learning_data, import_learning_data

client = TestClient(app)


def test_catalog_reads_only_the_pinned_course_and_adapted_stage_docs(wiki):
    overview = client.get("/api/learning-lab")
    assert overview.status_code == 200
    data = overview.json()
    assert data["course"]["source_status"]["available"] is True
    assert data["course"]["course_version"] == learning_lab.COURSE_VERSION
    assert len(data["missions"]) == 4
    assert data["progress_total"] == 4
    assert data["xp"] == 0

    detail = client.get("/api/learning-lab/missions/mission-01-find-missing-evidence")
    assert detail.status_code == 200
    mission = detail.json()
    assert mission["mission"]["stage_id"] == "02-precision-and-recall"
    assert "工作台任务适配" in mission["adaptation"]
    assert any("stages/01-validate-rankings/docs/en.md" in source["path"] for source in mission["sources"])
    assert all("/solution" not in source["path"] for source in mission["sources"])
    assert "precision" in mission["initial_code"]
    assert "root" not in data["course"]["source_status"]


def test_draft_saves_and_reloads_without_creating_wiki_records(wiki):
    key = "mission-01-find-missing-evidence"
    payload = {
        "prediction": "Top-K 增大时 recall 不会下降。",
        "observation": "尚未运行；这是手动推演。",
        "explanation": "precision 与 recall 的分母不同。",
        "code_text": "def precision_recall(ranking, judgments, k):\n    return None\n",
        "help_level": 1,
    }
    saved = client.put(f"/api/learning-lab/missions/{key}/draft", json=payload)
    assert saved.status_code == 200, saved.text
    record = saved.json()
    assert record["status"] == "in_progress"
    assert record["code_sha256"] == hashlib.sha256(payload["code_text"].encode()).hexdigest()
    assert record["source"] == "local"

    refreshed = client.get(f"/api/learning-lab/missions/{key}").json()["progress"]
    assert refreshed["prediction"] == payload["prediction"]
    assert refreshed["code_text"] == payload["code_text"]
    assert refreshed["updated_at"] == record["updated_at"]

    stale = client.put(f"/api/learning-lab/missions/{key}/draft", json={**payload, "expected_updated_at": None})
    assert stale.status_code == 409
    assert client.get(f"/api/learning-lab/missions/{key}").json()["progress"]["explanation"] == payload["explanation"]
    with db.cursor() as cur:
        assert cur.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert "page_path" not in {row["name"] for row in cur.execute("PRAGMA table_info(learning_lab_progress)")}


def test_unknown_mission_is_rejected_and_run_stays_closed(wiki):
    assert client.get("/api/learning-lab/missions/unknown").status_code == 404
    response = client.post("/api/learning-lab/missions/mission-01-find-missing-evidence/run")
    assert response.status_code == 503
    assert "P0" in response.json()["detail"]
    with db.cursor() as cur:
        assert cur.execute("SELECT COUNT(*) FROM learning_lab_attempts").fetchone()[0] == 0
        assert cur.execute("SELECT COUNT(*) FROM learning_lab_rewards").fetchone()[0] == 0


def test_backup_v9_roundtrip_marks_imported_evidence_for_review(wiki, tmp_path, monkeypatch):
    key = "mission-01-find-missing-evidence"
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    attempt_uid = str(uuid4())
    reward_uid = str(uuid4())
    code = "print('learner draft')\n"
    digest = hashlib.sha256(code.encode()).hexdigest()
    export = {
        "format": "feynman-workbench-export",
        "version": 9,
        "learning_lab_progress": [{
            "course_id": learning_lab.COURSE_ID,
            "course_version": learning_lab.COURSE_VERSION,
            "mission_key": key,
            "mission_version": 1,
            "status": "completed",
            "prediction": "先预测",
            "observation": "导入的历史观察",
            "explanation": "需要重新确认",
            "code_text": code,
            "code_sha256": digest,
            "help_level": 0,
            "source": "local",
            "created_at": now,
            "updated_at": now,
        }],
        "learning_lab_attempts": [{
            "attempt_uid": attempt_uid,
            "course_id": learning_lab.COURSE_ID,
            "course_version": learning_lab.COURSE_VERSION,
            "mission_key": key,
            "mission_version": 1,
            "phase": "practice",
            "status": "succeeded",
            "code_sha256": digest,
            "code_snapshot": code,
            "input_json": "{}",
            "command_json": "[]",
            "result_json": "{}",
            "exit_code": 0,
            "grader_version": "stage-2-v1",
            "help_level": 0,
            "source": "local",
            "created_at": now,
        }],
        "learning_lab_rewards": [{
            "reward_key": f"{learning_lab.COURSE_ID}:{key}:mission",
            "reward_uid": reward_uid,
            "course_id": learning_lab.COURSE_ID,
            "mission_key": key,
            "reward_type": "mission",
            "xp": 100,
            "attempt_uid": attempt_uid,
            "source": "local",
            "created_at": now,
        }],
    }
    assert import_learning_data(export, dry_run=True)["learning_lab"]["conflicts"] == {
        "progress": 0, "attempts": 0, "rewards": 0,
    }

    target_db = tmp_path / "lab-backup.db"
    monkeypatch.setattr(db, "DB_PATH", target_db)
    db.init_db()
    imported = import_learning_data(export)
    assert imported["imported"]["learning_lab_progress"] == 1
    assert imported["imported"]["learning_lab_attempts"] == 1
    assert imported["imported"]["learning_lab_rewards"] == 1
    with db.cursor() as cur:
        progress = cur.execute("SELECT status, source FROM learning_lab_progress").fetchone()
        attempt = cur.execute("SELECT status, source FROM learning_lab_attempts").fetchone()
        reward = cur.execute("SELECT xp, source FROM learning_lab_rewards").fetchone()
    assert tuple(progress) == ("needs_review", "imported")
    assert tuple(attempt) == ("imported", "imported")
    assert tuple(reward) == (100, "imported")
    repeated = import_learning_data(export)
    assert repeated["imported"]["learning_lab_rewards"] == 0
    assert learning_lab.overview()["imported_xp"] == 100


def test_v8_backup_remains_importable_without_lab_arrays(wiki, tmp_path, monkeypatch):
    old = export_learning_data()
    old["version"] = 8
    old.pop("learning_lab_progress", None)
    old.pop("learning_lab_attempts", None)
    old.pop("learning_lab_rewards", None)
    target_db = tmp_path / "v8-backup.db"
    monkeypatch.setattr(db, "DB_PATH", target_db)
    db.init_db()
    result = import_learning_data(old)
    assert result["dry_run"] is False
    assert result["imported"]["learning_lab_progress"] == 0


def test_import_rejects_changed_code_hash(wiki):
    record = {
        "course_id": learning_lab.COURSE_ID,
        "course_version": learning_lab.COURSE_VERSION,
        "mission_key": "mission-01-find-missing-evidence",
        "mission_version": 1,
        "status": "in_progress",
        "prediction": "",
        "observation": "",
        "explanation": "",
        "code_text": "print('edited')",
        "code_sha256": "0" * 64,
        "help_level": 0,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        learning_lab.validate_import_rows([record], [], [])
    except ValueError as exc:
        assert "哈希不匹配" in str(exc)
    else:
        raise AssertionError("changed code hash was accepted")
