"""浏览器功能测试：验证学习流与应用自测状态。"""
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Thread

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright
from uvicorn import Config, Server

from app.main import app
from app.services import practice_sessions


@contextmanager
def run_server():
    config = Config(app, host="127.0.0.1", port=8765, log_level="error")
    server = Server(config)
    thread = Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=2)
        raise RuntimeError("Test server did not start")
    try:
        yield "http://127.0.0.1:8765"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def launch_browser(playwright):
    browser_paths = sorted((Path.home() / "AppData" / "Local" / "ms-playwright").glob("chromium-*/chrome-win64/chrome.exe"))
    if browser_paths:
        try:
            return playwright.chromium.launch(executable_path=str(browser_paths[-1]))
        except PlaywrightError:
            pass
    try:
        return playwright.chromium.launch(channel="msedge")
    except PlaywrightError as exc:
        pytest.skip(f"Playwright Chromium and Microsoft Edge are unavailable: {exc}")


@pytest.mark.ui
def test_reader_preferences_note_and_recall_flow(wiki):

    with run_server() as base_url, sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page()
        page.goto(base_url, wait_until="networkidle")

        # 初始目录保持收起，点击后再展开。
        assert page.locator(".tree-children").count() == 0
        page.locator(".tree-dir", has_text="AI").click()
        assert page.locator(".tree-children").count() == 1
        page.locator(".tree-dir", has_text="AI").click()
        assert page.locator(".tree-children").count() == 0
        assert page.locator("#home-action-title").is_visible()

        # 选择深层页面前按用户意图展开相应目录。
        page.locator(".tree-dir", has_text="AI").click()
        page.locator(".tree-dir", has_text="rag").click()

        page.locator("#concept-tree").get_by_text("Query Rewriting 查询改写", exact=True).click()
        page.get_by_role("button", name="阅读外观").click()
        page.locator("#reading-font-size").fill("20")
        page.get_by_role("button", name="夜间阅读").click()
        assert page.locator("html").get_attribute("data-theme") == "dark"

        page.get_by_role("button", name="学习笔记").click()
        page.locator("#note-input").fill("浏览器流程保存的学习笔记。")
        with page.expect_response(lambda response: response.request.method == "PUT" and "/api/study/notes" in response.url) as note_response:
            page.get_by_role("button", name="保存笔记").click()
        assert note_response.value.status == 200, note_response.value.text()
        page.locator("#notes-status").wait_for(state="visible")
        page.wait_for_function("document.querySelector('#notes-status').textContent.includes('已保存')", timeout=5000)
        assert "已保存" in page.locator("#notes-status").inner_text()
        page.get_by_role("button", name="关闭", exact=True).click()

        page.get_by_role("button", name="开始回忆表达").click()
        page.get_by_role("button", name="开始表达这个问题").click()
        page.locator("#recall-input").fill("查询改写是把问题缺少的上下文和关键词补完整。先补充对象、场景和约束条件，再把完整查询交给检索，所以更容易命中需要的资料。例如，把‘怎么安排’补成带有时间和目标的具体问题。")
        page.locator('input[name="recall-evidence-key"][value="mechanism"]').check()
        page.locator('input[name="recall-evidence-key"][value="example"]').check()
        with page.expect_response(lambda response: response.url.endswith("/api/study/sessions")) as session_response:
            page.get_by_role("button", name="保存并生成诊断").click()
        assert session_response.value.status == 200, session_response.value.text()
        page.wait_for_function("!document.querySelector('#recall-complete').classList.contains('hidden')")
        page.get_by_role("button", name="补充后，用更简单的话再讲一次").click()
        page.locator("#simplify-input").fill("查询改写是把问题补充完整，让检索系统找得更准。例如加上对象和场景。")
        page.get_by_role("button", name="保存第二次表达").click()
        page.locator(".learning-outcome").wait_for(state="visible")
        assert "本次学习结果" in page.locator(".learning-outcome").inner_text()
        assert not page.locator(".outcome-detail").evaluate("node => node.open")
        page.get_by_text("查看两次表达与分析", exact=True).click()
        assert page.locator(".outcome-detail").evaluate("node => node.open")
        page.get_by_role("button", name="回到学习资料").click()

        page.get_by_role("button", name="学习记录").click()
        page.get_by_role("tab", name="学习心得").click()
        page.locator("#reflection-input").fill("我现在会先分清检索目标、对象和约束，再判断问题是否足够明确。")
        page.get_by_role("button", name="保存心得").click()
        page.locator(".reflection-item").first.wait_for(state="visible")
        page.locator(".reflection-select-input").first.check()
        page.get_by_role("button", name="生成阶段总结").click()
        page.locator(".reflection-item").filter(has_text="阶段总结").wait_for(state="visible")
        page.get_by_role("button", name="关闭", exact=True).click()

        page.get_by_role("button", name="复习模块", exact=True).click()
        page.get_by_role("tab", name="突击检查").click()
        review = page.locator(".review-item").first
        review.locator(".review-answer-input").fill("查询改写会补足对象、场景和约束条件，使检索返回的资料更贴近任务。例如把模糊问题改成包含上下文的查询。")
        review.get_by_role("button", name="突击教练 · 直接检查").click()
        review.locator(".review-feedback").wait_for(state="visible")
        assert "突击教练" in review.locator(".review-feedback").inner_text()
        page.get_by_role("button", name="关闭", exact=True).click()

        page.get_by_role("button", name="学习记录").click()
        assert page.get_by_role("tab", name="待处理盲区").get_attribute("aria-selected") == "true"
        page.locator("#history-content").wait_for(state="visible")

        page.reload(wait_until="networkidle")
        assert page.locator("html").get_attribute("data-theme") == "dark"
        page.locator(".tree-dir", has_text="AI").click()
        page.locator(".tree-dir", has_text="rag").click()
        page.locator("#concept-tree").get_by_text("Query Rewriting 查询改写", exact=True).click()
        page.get_by_role("button", name="阅读外观").click()
        assert page.locator("#reading-font-size").input_value() == "20"
        browser.close()


@pytest.mark.ui
def test_note_can_be_reviewed_written_and_undone_through_knowledge_evolution(wiki):

    source = wiki / "pages" / "AI" / "rag" / "query-rewriting.md"
    before = source.read_text(encoding="utf-8")
    with run_server() as base_url, sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page()
        page.goto(base_url, wait_until="networkidle")
        page.locator(".tree-dir", has_text="AI").click()
        page.locator(".tree-dir", has_text="rag").click()
        page.locator("#concept-tree").get_by_text("Query Rewriting 查询改写", exact=True).click()

        page.get_by_role("button", name="学习笔记").click()
        page.locator("#note-input").fill("我需要比较改写前后的召回结果，并说明对象、场景和约束为什么会改变检索。")
        page.get_by_role("button", name="交给 Agent 整理").click()
        detail = page.locator(".knowledge-detail")
        detail.wait_for(state="visible")
        assert "待审核" in detail.inner_text()
        assert source.read_text(encoding="utf-8") == before

        detail.get_by_role("button", name="审核草稿").click()
        detail.get_by_role("button", name="确认为待验证问题").click()
        page.locator(".knowledge-applied").wait_for(state="visible")
        assert "## 待验证问题" in source.read_text(encoding="utf-8")
        page.get_by_role("button", name="撤销这次更新").click()
        page.get_by_text("已撤销", exact=True).wait_for(state="visible")
        assert source.read_text(encoding="utf-8") == before
        browser.close()


@pytest.mark.ui
def test_workspace_exposes_switchable_local_api_profiles(wiki):
    with run_server() as base_url, sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page()
        page.goto(base_url, wait_until="networkidle")
        page.get_by_role("button", name="设置").click()
        page.get_by_role("heading", name="学习助手 API").wait_for(state="visible")
        page.locator("#llm-profile-name").fill("浏览器测试连接")
        page.locator("#llm-base-url").fill("http://127.0.0.1:11434/v1")
        page.locator("#llm-model").fill("example-local-model")
        page.locator("#llm-api-key").fill("browser-test-secret")
        page.get_by_role("button", name="保存并启用连接").click()
        page.get_by_text("正在使用：浏览器测试连接", exact=True).wait_for(state="visible")
        assert page.locator("#llm-api-key").input_value() == ""
        browser.close()


@pytest.mark.ui
def test_mobile_primary_action_is_not_clipped(wiki):
    with run_server() as base_url, sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.goto(base_url, wait_until="networkidle")
        home_button = page.locator("#btn-home-action")
        assert home_button.is_visible()
        box = home_button.bounding_box()
        assert box is not None and box["x"] >= 0 and box["x"] + box["width"] <= 390
        home_button.click()
        page.get_by_role("button", name="开始回忆表达").wait_for(state="visible")
        page.get_by_role("button", name="切换知识点").click()
        assert "mobile-open" in (page.locator("#concept-panel").get_attribute("class") or "")
        page.locator("#concept-drawer-backdrop").click(position={"x": 380, "y": 4})
        assert "mobile-open" not in (page.locator("#concept-panel").get_attribute("class") or "")
        action = page.get_by_role("button", name="开始回忆表达")
        action_box = action.bounding_box()
        assert action_box is not None and action_box["x"] >= 0 and action_box["x"] + action_box["width"] <= 390
        browser.close()


@pytest.mark.ui
def test_review_plan_starts_with_one_card_and_pauses_after_rating(wiki):

    from app import db
    from app.services import study_sessions

    study_sessions.create_session(
        "AI/rag/query-rewriting.md",
        "查询改写会补足对象、场景和约束条件，让检索返回更贴近任务的资料。例如把模糊问题改成包含上下文的查询。",
    )
    with db.cursor() as cur:
        cur.execute("UPDATE cards SET due = date('now', 'localtime', '-1 day')")

    with run_server() as base_url, sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page()
        page.goto(base_url, wait_until="networkidle")
        page.get_by_role("button", name="复习模块", exact=True).click()
        page.locator("#review-modal").wait_for(state="visible")
        assert page.locator(".review-item").count() == 1
        assert "现在只做下一张" in page.locator("#review-progress").inner_text()

        review = page.locator(".review-item")
        review.get_by_role("button", name="查看 Wiki 参考与必备要点").click()
        review.get_by_role("button", name="记得住").click()
        page.get_by_text("已完成这一张", exact=True).wait_for(state="visible")
        assert page.get_by_role("button", name="继续下一张").is_visible()
        assert page.locator(".review-item").count() == 0
        browser.close()


@pytest.mark.ui
def test_home_workspace_panels_share_height_and_concept_switch_animates(wiki):
    with run_server() as base_url, sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page()
        page.goto(base_url, wait_until="networkidle")
        heights = page.evaluate("""() => {
          const concept = document.querySelector('#concept-panel').getBoundingClientRect();
          const pagePanel = document.querySelector('#page-panel').getBoundingClientRect();
          return { concept: concept.height, page: pagePanel.height };
        }""")
        assert abs(heights["concept"] - heights["page"]) <= 1
        page.locator(".tree-dir", has_text="AI").click()
        page.locator(".tree-dir", has_text="rag").click()
        page.locator("#concept-tree").get_by_text("Query Rewriting 查询改写", exact=True).click()
        page.locator("#page-content").wait_for(state="visible")
        assert "content-enter" in (page.locator("#page-content").get_attribute("class") or "")
        browser.close()


@pytest.mark.ui
def test_home_dashboard_surfaces_learning_records_and_keeps_actions_connected(wiki):

    with run_server() as base_url, sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page()
        page.goto(base_url, wait_until="networkidle")

        page.locator("#dashboard-rail").wait_for(state="visible")
        assert page.get_by_role("heading", name="待处理盲区").is_visible()
        assert page.get_by_role("heading", name="掌握变化").is_visible()
        assert page.get_by_role("heading", name="需要补洞").is_visible()

        page.get_by_role("button", name="处理盲区").click()
        page.locator("#history-modal").wait_for(state="visible")
        page.get_by_role("button", name="关闭", exact=True).click()

        page.locator("#dashboard-needs-list .dashboard-row").first.click()
        page.locator("#page-content").wait_for(state="visible")
        assert page.locator("#dashboard-rail").is_hidden()
        browser.close()


@pytest.mark.ui
def test_home_update_reminder_and_library_time_status_filters_are_actionable(wiki):

    today = date.today().isoformat()
    pages = wiki / "pages" / "AI"
    (pages / "newer-note.md").write_text(
        f"---\ntitle: 今日新资料\ncreated: {today}\nupdated: 2000-01-01\nstatus: unread\n---\n\n# 今日新资料\n",
        encoding="utf-8",
    )
    (pages / "older-note.md").write_text(
        "---\ntitle: 较早资料\ncreated: 2000-01-01\nupdated: 2099-01-01\nstatus: unread\n---\n\n# 较早资料\n",
        encoding="utf-8",
    )
    with run_server() as base_url, sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page()
        page.goto(base_url, wait_until="networkidle")

        page.locator("#home-update-list").get_by_text("今日新资料", exact=True).wait_for(state="visible")
        assert "普通编辑和阅读状态变化不会重复提醒" in page.locator("#home-update-hint").inner_text()

        page.locator("#library-sort").select_option("created_desc")
        assert page.locator("#library-sort").input_value() == "created_desc"
        page.locator('.tree-dir[data-dir="AI"]').click()
        paths = page.locator("#concept-tree [data-path]").evaluate_all("els => els.map(el => el.dataset.path)")
        assert paths.index("AI/newer-note.md") < paths.index("AI/older-note.md")

        page.locator('[data-library-status="read"]').click()
        page.locator("#search-input").fill("Agent Memory")
        assert page.get_by_text("Agent Memory System", exact=True).is_visible()
        page.locator('[data-library-status="unread"]').click()
        assert not page.get_by_text("Agent Memory System", exact=True).is_visible()

        page.locator("#search-input").fill("")
        page.locator("#home-update-list").get_by_text("今日新资料", exact=True).click()
        page.locator("#page-content").wait_for(state="visible")
        assert page.locator("#page-title").inner_text() == "今日新资料"
        browser.close()


@pytest.mark.ui
def test_graph_node_drag_persists_and_can_be_cleared(wiki):

    with run_server() as base_url, sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page()
        page.goto(base_url, wait_until="networkidle")
        page.get_by_role("button", name="知识图谱").click()
        page.locator("#graph-canvas svg").wait_for(state="visible")
        local_count = page.locator(".graph-node").count()
        assert 1 <= local_count < 7
        page.get_by_role("button", name="查看全部图谱").click()
        assert page.locator(".graph-node").count() == 7
        # 初始力导向布局短暂移动，等待稳定后再按住圆心拖动。
        page.wait_for_timeout(1200)
        initial_transform = page.locator(".graph-node").first.get_attribute("transform")
        page.get_by_role("button", name="图谱设置").click()
        page.locator("#graph-center-force").fill("0.04")
        page.wait_for_function(
            f"document.querySelector('.graph-node')?.getAttribute('transform') !== {initial_transform!r}"
        )
        page.get_by_role("button", name="图谱设置").click()
        suggestion_box = page.locator(".graph-next-step").bounding_box()
        box = None
        for index in range(page.locator(".graph-node circle:not(.graph-mastery-ring)").count()):
            candidate = page.locator(".graph-node circle:not(.graph-mastery-ring)").nth(index).bounding_box()
            if not candidate:
                continue
            center_x = candidate["x"] + candidate["width"] / 2
            center_y = candidate["y"] + candidate["height"] / 2
            covered = suggestion_box and (
                suggestion_box["x"] <= center_x <= suggestion_box["x"] + suggestion_box["width"]
                and suggestion_box["y"] <= center_y <= suggestion_box["y"] + suggestion_box["height"]
            )
            if not covered:
                box = candidate
                break
        assert box is not None
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] / 2 + 75, box["y"] + box["height"] / 2 + 40, steps=5)
        page.mouse.up()
        page.locator(".graph-pin").wait_for(state="visible")

        page.reload(wait_until="networkidle")
        page.get_by_role("button", name="知识图谱").click()
        page.locator(".graph-pin").wait_for(state="visible")
        page.get_by_role("button", name="图谱设置").click()
        page.get_by_role("button", name="清除手动布局").click()
        assert page.locator(".graph-pin").count() == 0
        page.get_by_role("button", name="返回学习界面").click()
        assert page.locator("#layout-main").is_visible()
        browser.close()


@pytest.mark.ui
def test_first_run_demo_workspace_and_graph_list_are_actionable(wiki):
    with run_server() as base_url, sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page()
        page.goto(base_url, wait_until="networkidle")
        page.get_by_role("button", name="设置").click()
        page.get_by_role("radio", name="先体验两分钟示例").check()
        page.get_by_role("button", name="保存学习空间").click()
        page.get_by_role("button", name="知识图谱").click()
        page.locator("#graph-canvas svg").wait_for(state="visible")
        choice = page.locator(".graph-node-option").first
        assert "尚未留下" not in choice.inner_text()
        choice.click()
        assert page.get_by_role("button", name="开始回忆表达").is_enabled()
        browser.close()


@pytest.mark.ui
def test_application_practice_flow_and_visual_evidence(wiki, monkeypatch):
    pages = wiki / "pages" / "AI" / "foundation"
    pages.mkdir(parents=True, exist_ok=True)
    for name in ("evaluation-basics", "ai-practice-lab", "python-api-basics"):
        (pages / f"{name}.md").write_text(
            f"---\ntitle: {name}\ntype: concept\n---\n\n# {name}\n", encoding="utf-8",
        )
    tz = datetime.now().astimezone().tzinfo
    clock = {"now": datetime(2026, 10, 12, 10, 0, tzinfo=tz)}
    monkeypatch.setattr(practice_sessions, "_now", lambda: clock["now"])
    metrics = next(task for task in practice_sessions.TASKS if task["task_key"] == "metrics-apply")["expected"]
    validation = next(task for task in practice_sessions.TASKS if task["task_key"] == "validation-apply")["expected"]
    wrong_metrics = dict(metrics, tp=0)
    # Regression captures must not overwrite dated audit evidence.
    screenshots = wiki / "practice-screenshots"
    screenshots.mkdir(parents=True, exist_ok=True)

    with run_server() as base_url, sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.set_default_timeout(10000)
        page.goto(base_url, wait_until="networkidle")
        page.locator(".tree-dir", has_text="AI").click()
        page.locator(".tree-dir", has_text="foundation").click()
        page.locator("#concept-tree").get_by_text("ai-practice-lab", exact=True).click()
        page.wait_for_function("document.querySelector('#page-title')?.textContent === 'ai-practice-lab'")
        page.locator("#btn-open-practice").click()
        panel = page.locator("#practice-panel")
        panel.wait_for(state="visible")
        page.locator("#btn-open-practice").focus()
        page.keyboard.press("Space")
        panel.wait_for(state="hidden")
        page.keyboard.press("Space")
        panel.wait_for(state="visible")
        page.wait_for_function("document.querySelector('.practice-task-availability')?.textContent.includes('独立应用验收')")
        page.screenshot(path=str(screenshots / "01-preconditions.png"), full_page=True)

        page.locator("#practice-task").select_option("metrics-apply")
        page.locator("#practice-start-form button[type=submit]").click()
        form = page.locator("#practice-submit-form")
        form.wait_for(state="visible")
        for key, value in wrong_metrics.items():
            form.locator(f'[data-answer-field="{key}"]').fill(str(value))
        page.route(
            "**/api/practice/attempts/*/submit",
            lambda route: route.fulfill(status=503, content_type="application/json", body='{"detail":"mock save failure"}'),
        )
        with page.expect_response(lambda response: response.url.endswith("/submit")) as failed_response:
            form.locator('button[type="submit"]').click()
        assert failed_response.value.status == 503
        page.wait_for_function("document.querySelector('#practice-status')?.textContent.includes('mock save failure')")
        assert form.locator('[data-answer-field="tp"]').input_value() == "0"
        page.screenshot(path=str(screenshots / "02-save-error-preserves-answer.png"), full_page=True)
        page.unroute("**/api/practice/attempts/*/submit")
        form.locator('button[type="submit"]').click()
        page.locator(".practice-result").wait_for(state="visible")
        assert "需复查" in page.locator(".practice-result").inner_text()
        page.screenshot(path=str(screenshots / "03-failed-correction.png"), full_page=True)

        page.locator('[data-practice-action="list"]').click()
        page.locator("#practice-task").select_option("validation-apply")
        page.locator('#practice-start-form select[name="mode"]').select_option("practice")
        page.locator("#practice-start-form button[type=submit]").click()
        page.locator('[data-practice-action="solution"]').click()
        page.locator(".practice-solution").wait_for(state="visible")
        help_select = page.locator('#practice-submit-form select[name="assistance"]')
        assert help_select.input_value() == "solution" and help_select.is_disabled()
        assert "本次将作为练习记录" in page.locator(".practice-attempt").inner_text()
        page.screenshot(path=str(screenshots / "04-solution-viewed.png"), full_page=True)
        for key, value in validation.items():
            field = page.locator(f'[data-answer-field="{key}"]')
            if field.evaluate("node => node.tagName") == "SELECT":
                field.select_option(str(value).lower() if isinstance(value, bool) else value)
            else:
                field.fill(str(value))
        page.locator('#practice-submit-form button[type="submit"]').click()
        page.locator(".practice-result").wait_for(state="visible")
        assert "solution" in page.locator(".practice-result").inner_text()

        page.locator("#concept-tree").get_by_text("evaluation-basics", exact=True).click()
        page.wait_for_function("document.querySelector('#practice-task')?.querySelector('option[value=\\\"metrics-apply\\\"]')")
        page.locator("#practice-task").select_option("metrics-apply")
        page.locator("#practice-start-form button[type=submit]").click()
        for key, value in metrics.items():
            page.locator(f'[data-answer-field="{key}"]').fill(str(value))
        page.locator('#practice-submit-form button[type="submit"]').click()
        page.locator(".practice-result").wait_for(state="visible")
        assert "符合本题的首次独立验收条件" in page.locator(".practice-result").inner_text()
        page.screenshot(path=str(screenshots / "05-independent-pass.png"), full_page=True)

        page.reload(wait_until="networkidle")
        page.locator(".tree-dir", has_text="AI").click()
        page.locator(".tree-dir", has_text="foundation").click()
        page.locator("#concept-tree").get_by_text("evaluation-basics", exact=True).click()
        page.wait_for_function("document.querySelector('#page-title')?.textContent === 'evaluation-basics'")
        page.locator("#btn-open-practice").click()
        page.wait_for_function("document.querySelectorAll('.practice-evidence-card')[1]?.textContent.includes('1 次匹配')")
        assert page.locator(".practice-history-item").filter(has_text="结果匹配").count() == 1

        page.locator("#btn-close-practice").click()
        clock["now"] += timedelta(days=6)
        page.locator("#btn-open-practice").click()
        page.wait_for_function("document.querySelector('.practice-task-availability')?.textContent.includes('2026-10-19')")
        page.screenshot(path=str(screenshots / "06-delayed-locked.png"), full_page=True)
        page.locator("#btn-close-practice").click()
        clock["now"] += timedelta(days=1)
        page.locator("#btn-open-practice").click()
        page.wait_for_function("document.querySelector('.practice-task-availability')?.textContent.includes('已到七日间隔复测日期')")
        page.locator("#practice-task").select_option("metrics-delayed")
        page.screenshot(path=str(screenshots / "07-delayed-open.png"), full_page=True)

        page.locator('[data-practice-view="external"]').click()
        external = page.locator("#practice-external-form")
        external.locator('[name="title"]').fill("<script>alert(1)</script>")
        external.locator('[name="source_url"]').fill("https://example.com/task")
        external.locator('[name="reported_result"]').fill("平台显示通过；用户自报，未自动核验")
        external.locator('[name="artifact_text"]').fill("<img src=x onerror=alert(1)>")
        external.locator('[name="familiarity"]').select_option("unseen")
        external.locator('[name="assistance"]').select_option("none")
        external.locator('button[type="submit"]').click()
        external_history = page.locator(".practice-history-item").filter(has_text="用户自报，未自动核验")
        external_history.wait_for(state="visible")
        assert external_history.count() == 1
        assert page.locator("img[onerror]").count() == 0
        source_link = page.get_by_role("link", name="打开练习链接")
        assert source_link.get_attribute("rel") == "noopener noreferrer"
        page.screenshot(path=str(screenshots / "08-external-self-report.png"), full_page=True)
        page.context.route(
            "https://example.com/task",
            lambda route: route.fulfill(status=200, content_type="text/html", body="<title>mock target</title>"),
        )
        with page.expect_popup() as popup_info:
            source_link.click()
        popup = popup_info.value
        popup.wait_for_load_state("load")
        assert popup.url == "https://example.com/task"
        popup.close()

        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert not page.locator(".concept-panel").evaluate("el => el.classList.contains('mobile-open')")
        page.wait_for_function("document.querySelector('#concept-panel').getBoundingClientRect().right <= 0")
        page.locator("#practice-panel").scroll_into_view_if_needed()
        page.screenshot(path=str(screenshots / "09-mobile-390px.png"))

        page.set_viewport_size({"width": 1440, "height": 1000})
        page.locator("#btn-close-practice").click()
        page.locator(".tree-dir", has_text="Financing").click()
        page.locator(".tree-dir", has_text="cashflow").click()
        page.locator("#concept-tree").get_by_text("预算与储蓄", exact=True).click()
        page.wait_for_function("document.querySelector('#page-title')?.textContent === '预算与储蓄'")
        page.locator("#btn-open-practice").click()
        page.locator('[data-practice-view="local"]').click()
        page.wait_for_function("document.querySelector('.practice-empty')?.textContent.includes('没有适配的本地自测题')")
        assert not page.locator("#practice-task").count()
        page.screenshot(path=str(screenshots / "10-no-adapted-local-task.png"), full_page=True)
        page.locator('[data-practice-view="external"]').click()
        assert page.get_by_role("button", name="保存自报记录").is_visible()
        browser.close()


@pytest.mark.ui
def test_flexible_focus_resumes_and_tools_work_in_any_order(wiki):
    with run_server() as base_url, sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"{base_url}/?path=AI/rag/query-rewriting.md", wait_until="networkidle")
        page.locator("#page-content").wait_for(state="visible")
        page.locator("#btn-focus-edit").click()
        page.locator("#focus-question-input").fill("什么情况下应该保留原查询？")
        page.locator("#focus-checkpoint-input").fill("已分清改写的目标；下次比较两个检索结果。")
        assert page.locator("#focus-minutes").input_value() == "60"
        with page.expect_response(lambda response: response.request.method == "PUT" and response.url.endswith("/api/study/focus")):
            page.locator("#btn-focus-save").click()
        page.locator("#focus-form").wait_for(state="hidden")

        # Applying or reading does not require completing a recall stage first.
        page.locator('[data-study-tool="practice"]').click()
        page.locator("#practice-panel").wait_for(state="visible")
        assert page.locator("#recall-modal").is_hidden()
        page.locator('[data-study-tool="read"]').click()
        assert page.locator("#page-body").evaluate("node => node.classList.contains('expanded')")
        page.locator('[data-study-tool="notes"]').click()
        page.locator("#notes-modal").wait_for(state="visible")
        page.locator("#btn-close-notes").click()
        page.locator('[data-study-tool="recall"]').click()
        page.wait_for_function("document.querySelector('#recall-question').textContent.includes('什么情况下应该保留原查询？')")
        page.locator("#btn-close-recall").click()

        page.goto(base_url, wait_until="networkidle")
        assert "什么情况下应该保留原查询？" in page.locator("#home-action-title").inner_text()
        assert page.locator("#btn-home-action").bounding_box()["y"] < 1000
        page.locator("#btn-home-action").click()
        page.locator("#page-content").wait_for(state="visible")
        assert "下次比较两个检索结果" in page.locator("#focus-checkpoint").inner_text()
        page.locator("#btn-focus-edit").click()
        page.locator("#focus-minutes").fill("180")
        page.locator("#focus-checkpoint-input").fill("周末继续深入，先检验改写丢失约束的情况。")
        page.reload(wait_until="networkidle")
        page.locator("#btn-focus-edit").click()
        assert page.locator("#focus-minutes").input_value() == "180"
        assert "周末继续深入" in page.locator("#focus-checkpoint-input").input_value()
        page.locator("#btn-focus-save").click()
        page.wait_for_function("document.querySelector('#focus-budget').textContent.includes('180')")
        page.screenshot(path=str(wiki / "focus-desktop.png"))

        page.set_viewport_size({"width": 390, "height": 844})
        page.locator("#btn-focus-edit").click()
        page.locator("#learning-focus").scroll_into_view_if_needed()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert page.locator("#btn-focus-save").is_visible()
        page.screenshot(path=str(wiki / "focus-mobile.png"))
        page.locator("#btn-focus-cancel").click()
        page.locator("#btn-focus-archive").click()
        page.wait_for_function("document.querySelector('#focus-status').textContent.includes('学习心得')")
        assert page.request.get(f"{base_url}/api/study/focus").json()["focus"] is None
        assert not errors
        browser.close()


@pytest.mark.ui
def test_history_recommendations_and_wiki_brief_remain_optional(wiki):
    question = '查询改写的边界 <img src=x onerror="alert(1)">'
    with run_server() as base_url, sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        response = page.request.put(f"{base_url}/api/study/focus", data={
            "page_path": "AI/rag/query-rewriting.md", "question": question,
        })
        assert response.ok
        page.goto(base_url, wait_until="networkidle")
        home = page.locator('#page-empty [data-recommendations]')
        home.locator('.reading-suggestion').first.wait_for(state="visible")
        assert "Wiki 链接" in home.inner_text()
        home.locator('summary').click()
        assert question in home.inner_text()
        assert page.locator('img[onerror]').count() == 0
        with page.expect_download() as download_info:
            home.get_by_role('link', name='导出 Wiki 维护清单').click()
        download = download_info.value
        brief = wiki / download.suggested_filename
        download.save_as(brief)
        assert "待核查数据" in brief.read_text(encoding="utf-8")
        page.screenshot(path=str(wiki / 'recommendations-desktop.png'), full_page=True)

        suggestion = home.locator('.reading-suggestion').filter(has_text='rag-from-scratch')
        suggestion.click()
        page.wait_for_function("document.querySelector('#page-title').textContent === 'rag-from-scratch'")
        rail = page.locator('#default-right-rail [data-recommendations]')
        rail.locator('.reading-suggestion').first.wait_for(state="visible")
        assert page.request.get(f"{base_url}/api/study/focus").json()['focus']['question'] == question
        assert page.locator('#recall-modal').is_hidden()

        page.set_viewport_size({"width": 390, "height": 844})
        rail.scroll_into_view_if_needed()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        rail.get_by_role('button', name='刷新推荐').click()
        rail.locator('.reading-suggestion').first.wait_for(state="visible")
        page.screenshot(path=str(wiki / 'recommendations-mobile.png'))
        assert not errors
        browser.close()
