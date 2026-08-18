"""浏览器功能测试：验证学习流，不采集截图或做视觉断言。"""
import time
from contextlib import contextmanager
from pathlib import Path
from threading import Thread

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright
from uvicorn import Config, Server

from app.main import app


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


@pytest.mark.ui
def test_reader_preferences_note_and_recall_flow(wiki):
    browser_paths = sorted((Path.home() / "AppData" / "Local" / "ms-playwright").glob("chromium-*/chrome-win64/chrome.exe"))
    if not browser_paths:
        pytest.skip("Playwright Chromium is not installed")

    with run_server() as base_url, sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(executable_path=str(browser_paths[-1]))
        except PlaywrightError as exc:
            pytest.skip(f"Playwright Chromium could not launch: {exc}")
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
        page.get_by_role("button", name="保存笔记").click()
        page.locator("#notes-status").wait_for(state="visible")
        page.wait_for_function("document.querySelector('#notes-status').textContent.includes('已保存')")
        page.get_by_role("button", name="关闭", exact=True).click()

        page.get_by_role("button", name="开始回忆表达").click()
        page.get_by_role("button", name="开始表达这个问题").click()
        page.locator("#recall-input").fill("查询改写会补充问题缺少的上下文和关键词，所以检索更容易命中真正需要的资料。例如把模糊问题补上对象、场景和约束条件。")
        page.get_by_role("button", name="保存并生成诊断").click()
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
        assert page.locator("#history-content").is_visible()

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
    browser_paths = sorted((Path.home() / "AppData" / "Local" / "ms-playwright").glob("chromium-*/chrome-win64/chrome.exe"))
    if not browser_paths:
        pytest.skip("Playwright Chromium is not installed")

    source = wiki / "pages" / "AI" / "rag" / "query-rewriting.md"
    before = source.read_text(encoding="utf-8")
    with run_server() as base_url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(browser_paths[-1]))
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

        detail.get_by_role("button", name="确认并处理草案").click()
        page.locator(".knowledge-applied").wait_for(state="visible")
        assert "## 学习增量" in source.read_text(encoding="utf-8")
        page.get_by_role("button", name="撤销这次更新").click()
        page.get_by_text("已撤销", exact=True).wait_for(state="visible")
        assert source.read_text(encoding="utf-8") == before
        browser.close()


@pytest.mark.ui
def test_workspace_exposes_switchable_local_api_profiles(wiki):
    browser_paths = sorted((Path.home() / "AppData" / "Local" / "ms-playwright").glob("chromium-*/chrome-win64/chrome.exe"))
    if not browser_paths:
        pytest.skip("Playwright Chromium is not installed")
    with run_server() as base_url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(browser_paths[-1]))
        page = browser.new_page()
        page.goto(base_url, wait_until="networkidle")
        page.get_by_role("button", name="资料与设置").click()
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
    browser_paths = sorted((Path.home() / "AppData" / "Local" / "ms-playwright").glob("chromium-*/chrome-win64/chrome.exe"))
    if not browser_paths:
        pytest.skip("Playwright Chromium is not installed")
    with run_server() as base_url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(browser_paths[-1]))
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
        page.locator("#concept-drawer-backdrop").click(position={"x": 4, "y": 4})
        assert "mobile-open" not in (page.locator("#concept-panel").get_attribute("class") or "")
        action = page.get_by_role("button", name="开始回忆表达")
        action_box = action.bounding_box()
        assert action_box is not None and action_box["x"] >= 0 and action_box["x"] + action_box["width"] <= 390
        browser.close()


@pytest.mark.ui
def test_home_workspace_panels_share_height_and_concept_switch_animates(wiki):
    browser_paths = sorted((Path.home() / "AppData" / "Local" / "ms-playwright").glob("chromium-*/chrome-win64/chrome.exe"))
    if not browser_paths:
        pytest.skip("Playwright Chromium is not installed")
    with run_server() as base_url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(browser_paths[-1]))
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
def test_graph_node_drag_persists_and_can_be_cleared(wiki):
    browser_paths = sorted((Path.home() / "AppData" / "Local" / "ms-playwright").glob("chromium-*/chrome-win64/chrome.exe"))
    if not browser_paths:
        pytest.skip("Playwright Chromium is not installed")

    with run_server() as base_url, sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(executable_path=str(browser_paths[-1]))
        except PlaywrightError as exc:
            pytest.skip(f"Playwright Chromium could not launch: {exc}")
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
    browser_paths = sorted((Path.home() / "AppData" / "Local" / "ms-playwright").glob("chromium-*/chrome-win64/chrome.exe"))
    if not browser_paths:
        pytest.skip("Playwright Chromium is not installed")
    with run_server() as base_url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(browser_paths[-1]))
        page = browser.new_page()
        page.goto(base_url, wait_until="networkidle")
        page.get_by_role("button", name="资料与设置").click()
        page.get_by_role("radio", name="先体验两分钟示例").check()
        page.get_by_role("button", name="保存学习空间").click()
        page.get_by_role("button", name="知识图谱").click()
        page.locator("#graph-canvas svg").wait_for(state="visible")
        choice = page.locator(".graph-node-option").first
        assert "尚未留下" not in choice.inner_text()
        choice.click()
        assert page.get_by_role("button", name="开始回忆表达").is_enabled()
        browser.close()
