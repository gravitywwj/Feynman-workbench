# 费曼学习工作台

一个面向自主学习者的本地 Web 工作台。它将个人 Markdown Wiki、主动回忆、费曼式讲解、盲区修订和间隔复习串成可持续的学习流程。

项目优先保存你的学习数据与资料控制权：学习记录、笔记、复习计划和反馈均默认存放在本机；LLM 能力为可选增强，不配置密钥也可以使用基础学习流程。

## 适合做什么

- 从本地 Wiki 的某个知识点开始学习，并用自己的话完成一次讲解。
- 保存讲解、待澄清点和学习心得，回看自己的理解如何变化。
- 用复习计划安排主动回忆，或使用“突击检查”快速检验薄弱点。
- 按学习领域查看可筛选、可拖动的知识图谱，再回到具体笔记继续学习。

## 开始使用

以下步骤以 Windows 为例。命令中的路径均为示例，请替换成你电脑上的实际位置。

1. 获取代码并进入项目目录。

   ```powershell
   git clone https://github.com/gravitywwj/Feynman-workbench.git
   cd Feynman-workbench
   ```

2. 创建 Python 虚拟环境并安装依赖。

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

3. 在项目根目录创建本地配置文件。

   ```powershell
   Copy-Item .env.example .env
   ```

4. 启动工作台。

   最方便的方式是双击项目根目录的 [start.bat](start.bat)。脚本会检查服务是否已经运行；需要时在后台启动服务，并在就绪后打开浏览器。

   也可以在终端中运行：

   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001
   ```

5. 打开 [http://127.0.0.1:8001/](http://127.0.0.1:8001/)，并在“资料与设置”中选择资料库或体验演示资料。

## 准备你的资料库

工作台读取的是 **Wiki 根目录**，而不是单个 Markdown 文件。这个目录至少需要包含一个 `pages` 子目录；页面可以继续按主题建立子文件夹。

```text
我的学习资料库/                  ← 在“资料与设置”中选择这一层
├─ pages/                         ← 存放 Markdown 学习页面
│  ├─ 人工智能/
│  │  └─ 提示词工程.md
│  └─ 金融基础/
│     └─ 现金流.md
└─ assets/                        ← 可选：图片、附件等资料
```

你可通过两种方式指定这个目录：

- 在网页的“资料与设置”中选择本地资料库；选择结果只保存在本机。
- 在 `.env` 中设置 `FEYNMAN_WIKI_PATH`，适合固定部署或启动脚本使用，例如：

  ```dotenv
  FEYNMAN_WIKI_PATH=C:\Users\你的用户名\Documents\我的学习资料库
  ```

## 可选：启用 LLM 辅助

不填写 API 密钥时，工作台仍提供基于表达结构的提示与本地复习流程；它不会把这些提示描述成知识事实的核验。

如要获得基于参考资料的追问、批改和诊断，可在 `.env` 中填写兼容 OpenAI API 格式的服务信息。当前变量名沿用 `DEEPSEEK_`，但模型名称和服务地址由你自行选择：

```dotenv
DEEPSEEK_API_KEY=你的_API_密钥
DEEPSEEK_BASE_URL=https://你的服务地址/v1
FEYNMAN_LLM_MODEL=你的服务所提供的模型标识
```

例如，若所用服务提供 `deepseek-v4-flash`，可将它填入 `FEYNMAN_LLM_MODEL`；这只是一个模型标识示例，并非使用本项目的前提或唯一选择。请确认所选服务与模型支持 OpenAI 兼容的聊天接口。

也可以在网页的“资料与设置 → 学习助手 API”中保存多个连接，例如 DeepSeek、Agnes 或本地模型服务。网页不会回显密钥；它仅保存在当前设备的 `data/llm-settings.json`，不会随学习数据导出，也不会写入 Wiki。网页中已启用的连接优先于 `.env`，可一键切换；`.env` 仅在尚未启用网页连接时作为首次启动或无界面部署的备用配置。点击“测试当前连接”时才会向所选服务发送一次最小请求，最近连通结果会显示在对应连接上。

## 数据与隐私

- 本地学习记录默认保存在 `data/feynman.db`（SQLite）。
- 工作区设置保存在 `data/workspace-settings.json`。
- 通过网页保存的 API 配置存放在 `data/llm-settings.json`，该文件已排除在 Git 之外。
- Wiki 原始资料始终由你本地目录管理；阅读状态等必要元数据可能按你的操作写回页面 frontmatter。
- 若启用 LLM，提交给服务的是当次学习所需的资料片段与输入内容。请根据所用服务的隐私政策决定是否使用。

建议定期备份 `data/` 目录和你的 Wiki 根目录；两者共同构成完整的个人学习档案。

## 项目目录

```text
app/                  FastAPI 应用代码
├─ routers/           页面资料、学习会话等 HTTP 接口
├─ services/          Wiki 解析、复习计划、LLM 辅助等业务逻辑
├─ static/            浏览器学习界面
└─ demo_wiki/         内置演示资料库
data/                 运行后生成的本地数据库与工作区设置（不提交）
tests/                API、服务与浏览器功能流程测试
obsidian-plugin/      可选的 Obsidian 配套插件
start.bat             Windows 快捷启动脚本
```

## 测试

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -v

# 仅运行浏览器功能流程，不包含截图或视觉断言
.\.venv\Scripts\python.exe -m pytest -m ui -v
```

## 开发说明

后端由 FastAPI 提供本地服务，默认监听 `127.0.0.1:8001`。如需变更端口，可在手动启动命令中修改 `--port`；同时请相应更新浏览器访问地址或启动脚本。
