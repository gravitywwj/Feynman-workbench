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

        page.get_by_text("Query Rewriting 查询改写", exact=True).click()
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

        page.get_by_role("button", name="开始回顾").click()
        page.get_by_role("button", name="我准备好了，开始表达").click()
        page.locator("#recall-input").fill("查询改写会补充问题缺少的上下文和关键词，所以检索更容易命中真正需要的资料。例如把模糊问题补上对象、场景和约束条件。")
        page.get_by_role("button", name="保存并生成诊断").click()
        page.get_by_role("button", name="回到资料页").click()

        page.get_by_role("button", name="学习记录").click()
        assert page.get_by_role("tab", name="待处理盲区").get_attribute("aria-selected") == "true"
        assert page.locator(".gap-item").count() >= 1
        page.locator(".gap-revision-input").first.fill("我会明确列出问题缺少的对象、上下文和约束，再将它们改写为完整检索条件，并用具体任务确认结果是否更贴近需求。")
        page.get_by_role("button", name="保存补充并核对").first.click()
        page.wait_for_function("document.querySelector('.gap-feedback').textContent.includes('补充已保存')")

        page.reload(wait_until="networkidle")
        assert page.locator("html").get_attribute("data-theme") == "dark"
        page.get_by_text("Query Rewriting 查询改写", exact=True).click()
        page.get_by_role("button", name="阅读外观").click()
        assert page.locator("#reading-font-size").input_value() == "20"
        browser.close()
