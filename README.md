# 费曼学习工作台

本地 Web 学习工作台：把费曼学习法四步（选择概念 → 讲解或写出 → 识别盲区 → 复习与简化）变成可操作的界面闭环。

- Wiki（D:\LLM wiki）作为内容后台：概念来源 + 批改对照；Obsidian 照常阅读。
- 工作台只读 wiki 的 `pages/`，产出（缺口报告/简化修订稿）可导出草稿到 `wiki/review/`，由 Hermes 编译回正式页面。

## 运行

```bash
cd D:\feynman-workbench
.venv\Scripts\python -m uvicorn app.main:app --port 8001
# 打开 http://127.0.0.1:8001
```

## 配置

复制 `.env.example` 为 `.env`：

- `DEEPSEEK_API_KEY`：LLM 调用密钥（费曼追问/批改/校验）
- `FEYNMAN_LLM_MODEL`：默认 `deepseek-v4-flash`
- `FEYNMAN_WIKI_PATH`：wiki 根目录，默认 `D:\LLM wiki`

## 测试

```bash
.venv\Scripts\python -m pytest tests/ -v
```

## 目录

```
app/          FastAPI 应用（db / routers / services）
prompts/      LLM 提示词模板（追问 / 批改 / 校验）
static/       纯静态前端（深色炫酷风）
data/         feynman.db（SQLite）
tests/        pytest（LLM 用 mock）
```
