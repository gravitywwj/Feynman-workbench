# 费曼学习工作台

本地 Web 学习工作台：把费曼学习法四步（选择概念 → 讲解或写出 → 识别盲区 → 复习与简化）变成可操作的界面闭环。

- 个人Wiki作为内容后台：概念来源 + 批改对照；Obsidian 照常阅读。
- 工作台读取 wiki 的 `pages/` 作为学习材料；阅读状态和重要性可受控写回 frontmatter。
- 每次回顾会持久化到本地 SQLite：保存讲解、盲区、下一问及待复习卡；笔记同样保存在本地学习库。
- 可在“学习记录”继续补充待澄清点、查看历史会话、导出本地学习数据；Wiki 页面移动后可手动重新关联记录。
- 配置 `DEEPSEEK_API_KEY` 时使用模型依据参考资料诊断；未配置时使用明确的本地检查规则，学习流程仍可用。

## 运行

```bash
cd D:\feynman-workbench
.venv\Scripts\python -m uvicorn app.main:app --port 8001
# 打开 http://127.0.0.1:8001
```

## 配置

复制 `.env.example` 为 `.env`：

- `DEEPSEEK_API_KEY`：LLM 调用密钥（费曼追问/批改/校验）；留空时使用本地规则诊断
- `FEYNMAN_LLM_MODEL`：默认 `deepseek-v4-flash`
- `FEYNMAN_WIKI_PATH`：wiki 根目录，默认 `D:\LLM wiki`
## 测试

```bash
.venv\Scripts\python -m pytest tests/ -v
# 仅验证浏览器功能流程（不含截图或视觉断言）
.venv\Scripts\python -m pytest -m ui -v
```

## 目录

```
app/          FastAPI 应用（db / routers / services）
app/static/   学习界面、回顾会话、笔记、图谱与复习计划
data/         feynman.db（SQLite，本地学习记录）
tests/        pytest API 与服务测试
```
