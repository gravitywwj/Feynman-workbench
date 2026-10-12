# 学习验证改造执行报告

日期：2026-10-12（Asia/Hong_Kong）
工作目录：`D:/feynman-workbench`
分支与基线：`main`，执行前 HEAD `f2fccea11ddfa5d72dc876c8e3c7b302461f47c1`。
状态：P0–P5 已完成；独立审核通过。

## 实施结果

- 自然语言本地反馈固定为 `retry + local + unverified`；只有有可靠资料且 AI 返回完整合法通过结果时才计入来源核对。历史 `source_standard` 记录保留原值与导出内容，不再抬高掌握状态或周报来源核对数。
- 复测问题传入 AI 判定；无参考资料时不请求 AI、不生成已核对证据。原十四日来源核对跨度及现有复习排程保留。
- 增加六道版本化离线应用题、尝试生命周期、确定性判分、资格判定、历史查询和外部自报记录。服务端验证全部输入；不执行用户代码、工具调用或外部平台提交。
- 新证据分开显示表达/来源核对、应用、变式、延迟结果；本地验收再按题组与阶段分别统计，合格失败不会被提示后的结果覆盖。同一资格规则用于页面、概念列表、图谱、周报和备份导入。
- 增加版本 6 备份、1–6 版本导入、uid 去重与冲突拒绝、旧数据幂等建表、孤立记录重关联；练习不修改每日复习目标或卡片排程。
- 应用自测面板包含单题作答、标准答案练习标记、恢复记录、外部自报、无适配题提示、错误保留输入和移动布局。关闭面板后清除已提交题目的临时视图；重新打开回到题目列表，未提交记录仍可恢复。
- Edge 交互检查发现并修复了两个界面问题：保存成功后顶部证据卡未刷新；高内容量弹窗在 720px 视口下无法滚动到复习按钮。

## 验收清单

| ID | 结果 | 证据 |
|---|---|---|
| E01 | 通过 | `tests/test_tutor.py::test_local_feedback_never_verifies_keyword_or_long_text` 覆盖两种教练及反向回答；`tests/test_study_api.py::test_mastery_requires_source_checks_not_only_ratings` 覆盖掌握/周报不升级。 |
| E02 | 通过 | 同上，覆盖复制要点、关键词堆砌、无意义长文本、本地正向回答；自评排程由 `test_mastery_requires_source_checks_not_only_ratings` 验证。 |
| E03 | 通过 | `tests/test_tutor.py::test_review_coach_only_source_grounded_ai_pass_is_verified` 覆盖合法通过、retry、错误 JSON、缺字段、非字符串字段、超时；`test_review_coach_without_reference_does_not_call_ai` 覆盖无资料回退。 |
| E04 | 通过 | `tests/test_study_api.py::test_mastery_requires_source_checks_not_only_ratings` 验证 `source_standard` 历史原样导出/导入且不计核对或升级状态。 |
| E05 | 通过 | `tests/test_study_api.py::test_source_reviewed_pass_keeps_fourteen_day_mastery_span`；`tests/test_tutor.py::test_gap_revision_passes_retest_prompt_to_ai`、`test_gap_revision_without_reference_never_calls_ai`。 |
| T01 | 通过 | `tests/test_practice_api.py::test_six_fixed_tasks_grade_correct_and_incorrect_answers_offline` 参数化覆盖六题正确与错误答案；判分服务不依赖模型。 |
| T02 | 通过 | `tests/test_practice_api.py::test_invalid_answers_and_untrusted_fields_do_not_complete_attempt` 覆盖 bool、字符串数字、NaN/Infinity、缺失/多余字段、负数、非法/不存在 UID、非法/不存在路径和未知题；断言无提交及完成事件。 |
| T03 | 通过 | `tests/test_practice_api.py::test_same_uid_submission_is_idempotent_under_concurrent_retries` 覆盖并发同答案、异答案冲突及唯一完成事件。 |
| T04 | 通过 | `tests/test_practice_api.py::test_solution_and_assistance_cannot_create_independent_evidence` 覆盖看答案、练习模式和不确定熟悉度；伪造资格字段被拒绝由 `test_invalid_answers_and_untrusted_fields_do_not_complete_attempt` 覆盖。 |
| T05 | 通过 | `tests/test_practice_api.py::test_repeating_the_same_task_never_creates_a_new_first_pass`、`test_transfer_with_prior_exposure_or_help_stays_practice`、`test_transfer_requires_same_page_family_and_delay_uses_server_date` 覆盖重做、seen/unknown、提示、无前置、跨页/跨题组。 |
| T06 | 通过 | `test_transfer_requires_same_page_family_and_delay_uses_server_date` 冻结服务端日期验证 10 月 18 日未开放、19 日开放；浏览器流程重复验证开放状态。 |
| T07 | 通过 | `tests/test_practice_api.py::test_latest_eligible_failure_stays_visible_after_helped_retry` 验证合格失败保留为最近结果，提示后的匹配不覆盖。 |
| T08 | 通过 | `tests/test_practice_api.py::test_external_results_are_self_reported_and_urls_are_restricted` 与 `tests/test_ui_functional.py::test_application_practice_flow_and_visual_evidence` 覆盖自报标记、危险协议拒绝、HTML 转义、外链安全属性与外链打开。未抓取或执行外部内容。 |
| D01 | 通过 | `tests/test_practice_api.py::test_database_upgrade_is_idempotent_and_keeps_review_schedule` 删除新表模拟旧结构，连续初始化两次并验证原 sessions/cards 与排程值不变。 |
| D02 | 通过 | `tests/test_practice_api.py::test_backup_versions_regrade_dedupe_and_reject_uid_conflicts` 覆盖版本 1–5 兼容、版本 6 应用/变式前置关系往返、重判、重复导入及事件去重。 |
| D03 | 通过 | 同上，覆盖 dry-run 零写入、uid 冲突及失败不落部分数据、伪造结果重判、未知版本留作未核验、无效前置资格撤销；当前版本题目的导入阶段与快照不一致时拒绝整份导入，不写入前面的笔记或尝试。 |
| D04 | 通过 | `tests/test_practice_api.py::test_orphaned_practice_page_can_be_relinked_and_history_follows` 覆盖仅练习数据页面的发现、重关联、历史与完成事件路径更新；周报活动只显示新路径。 |
| D05 | 通过 | `tests/test_practice_api.py::test_page_concepts_graph_and_report_share_separate_practice_evidence` 验证页面/概念/图谱证据一致、周报分项、来源核对不增加、复习排程不变；`test_practice_evidence_and_weekly_report_keep_family_phase_separate` 验证同页的指标题组通过与校验题组失败分别显示；外部计数由 `test_external_results_are_self_reported_and_urls_are_restricted` 验证。 |
| U01 | 通过 | `tests/test_ui_functional.py::test_application_practice_flow_and_visual_evidence` 实际浏览器操作答错、保存失败重试、看答案、再提交。 |
| U02 | 通过 | 同一浏览器用例完成独立验收后整页刷新，验证知识点证据与历史恢复；本地题不请求模型。 |
| U03 | 通过 | 同一用例验证键盘开合、390px 无横向溢出、未开放/开放状态、保存错误保留答案、无适配题状态。截图 `01`–`07`、`09`、`10`。 |
| U04 | 通过 | 同一浏览器用例验证外部自报文本、合法外链新窗口与安全属性；外部周报自报计数由 API 用例验证。 |
| R01 | 通过 | `tests/test_study_api.py` 的复习排程、隔日复测、周报用例；`tests/test_ui_functional.py::test_review_plan_starts_with_one_card_and_pauses_after_rating` 验证复习单卡流程。 |
| R02 | 通过 | 全部非 UI 与 UI 测试均通过；分别运行 111 个非 UI 测试和 11 个 Edge UI 测试，未跳过原有 UI 测试。 |
| R03 | 通过 | `tests/test_graph_performance.py::test_build_graph_handles_hundreds_of_linked_pages`；掌握汇总按页批量查询。Ruff、JS 语法及 `git diff --check` 均退出码 0。 |

## 执行命令

| 命令 | 退出码 | 输出 |
|---|---:|---|
| `python -m pytest tests/test_practice_api.py tests/test_tutor.py -q`（项目 `.venv`） | 0 | 42 passed，4 warnings，9.73s。 |
| `python -m pytest -m "not ui" -q --basetemp=.pytest_tmp_root_nonui`（项目 `.venv`） | 0 | 111 passed，11 deselected，4 warnings，30.08s。 |
| `python -m pytest -m ui -q --basetemp=.pytest_tmp_root_ui`（项目 `.venv`，Microsoft Edge） | 0 | 11 passed，111 deselected，4 warnings，41.13s。 |
| `python -m ruff check`（所有修改的 Python 实现与测试文件） | 0 | All checks passed。 |
| `node --check app/static/app.js` | 0 | 无语法错误。 |
| `git diff --check` | 0 | 无空白错误；Git 提示若干既有 LF 文件下次由 Git 转为 CRLF。 |

四条测试警告来自 Starlette/httpx、Pydantic class-based config 与 FastAPI `on_event` 弃用提示；不是本轮测试失败。

测试使用项目 `.venv`、临时 Wiki/SQLite fixture、mock AI 和冻结日期。浏览器使用本机 Microsoft Edge；未找到 Playwright Chromium 缓存。真实学习库、真实 Wiki、密钥和外部平台均未读取或修改。

## 截图

截图均在 Edge 中实际操作并检查。按用户要求，截图文件及 pytest 临时目录已清理，不纳入提交；P5 修复后复查过 `05`、`08`、`09`，包括分组证据和 390px 布局。已清理的截图名称：

`01-preconditions.png`、`02-save-error-preserves-answer.png`、`03-failed-correction.png`、`04-solution-viewed.png`、`05-independent-pass.png`、`06-delayed-locked.png`、`07-delayed-open.png`、`08-external-self-report.png`、`09-mobile-390px.png`、`10-no-adapted-local-task.png`。

## 基线与文件边界

执行前副本、哈希、`git status` 和已有目标文件 diff 位于 `audit/learning-validation-2026-10-12/baseline/`。原先已修改的 `study_sessions.py`、`app.js`、`style.css`、`test_study_api.py`、`test_ui_functional.py` 仍按基线保留；本报告哈希对应本次验收版本。原先已删除的 16 张 2026-08-12 审核截图未恢复或改动；既有未跟踪的 `audit/product-learning-flow-2026-08-24/` 未触碰。真实 DB 和 Wiki 均未参与测试。

当前实现文件 SHA-256：

| 文件 | SHA-256 |
|---|---|
| `README.md` | `B1B17D041B30ED992056D216B3AB12B33879171BA8D7A2F455BBC052127F85F2` |
| `PRODUCT.md` | `1B66502BBB6F4A3D43D7656CEEEE98B31FD35134971534045675E908484B4DB9` |
| `app/db.py` | `7C734BD7E435DDBCAD582648849DF78E1C8126D997178AF30A6B01A4D78B0C3E` |
| `app/main.py` | `F156989C55142176C3FDECE4AE41F6319AF43B8226A26A47F9A9EA82D2A25138` |
| `app/services/review_coach.py` | `F54E63D8C9CEC7A9C2D97857EB35C3071B2EABCACEFF7B04E676DADFA73FAF64` |
| `app/services/mastery.py` | `FB833FA16B6E44665BFEAA312F8745F0E6C2C484AD53BF1A26FD21CD2D5AC73F` |
| `app/services/tutor.py` | `211B6EC4B5BC5FD5F5BC749C770EE7DEB1F7E25EA770F7D96C9793F59D1EC310` |
| `app/services/study_sessions.py` | `1A7324311E324E1DC452EA8D3C24DFCA608E047A5ED5ECEEED20E1AD28C7F8AD` |
| `app/services/practice_sessions.py` | `C92649326C86CCB657983DBE9E0BBF5C25D995DEBDAB97D6559099F0FA2A88E7` |
| `app/routers/practice.py` | `62115C0B1A724CF2A15A4303B51F43565609039B0E51B528D0C853CE2BC00655` |
| `app/static/index.html` | `C286DDD34CC0E007CF43ECB37EB2F90D50268DCFCEBCBB58E07F3139522547A3` |
| `app/static/app.js` | `0EB10435139DCF1423CF3CB5F408EEAC1A900C1E680BE9D5B0321E4450E03462` |
| `app/static/style.css` | `41C90759F9EE1F8436F96974DCAD9CBA79CACD068D30FB271A3A3649FA97506E` |
| `tests/test_study_api.py` | `CAA2820303FD79CA8776CFE9593A0ED165AEE65AB85D3A2B4A10485CAF933348` |
| `tests/test_tutor.py` | `289856A3ACFFD2F290D902B10E1B9E2C5362D8ADC7769BFD32AE17024094F87D` |
| `tests/test_practice_api.py` | `F9D1B54D98EC06A88AD10021BFEFE633D99807BADBF905A9F6D5BD885A52C264` |
| `tests/test_ui_functional.py` | `C50192C62F05AC786BF4C7AD66562023843B6081A0DF2FED8E9FB6D816C0AC30` |

## 范围限制

六道本地题只核对计算与规则应用，不证明代码真实执行或完整编程能力。外部平台结果是用户自报，不能自动核验。真实数据升级、真实模型调用及第三方平台执行未做，也不属于第一版交付。

## P5 独立审核

初审发现并推动修复四项：导入时拒绝已知题目阶段与其标准快照不一致；AI 评审字段必须为非空字符串；页面重关联同步更新 `learning_events.page_path`；页面及周报按 family 与 phase 分开展示本地验收。独立复跑核心 API、资料与评审用例为 71 passed，关键 Edge 流程为 1 passed；05、08、09 截图重新检查；报告所列 17 个目标文件 SHA-256 与当前文件全部匹配，既有修改和历史截图删除均保留。独立审核结论：通过。
