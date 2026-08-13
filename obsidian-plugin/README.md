# Feynman Workbench Launcher

把此文件夹复制到 Obsidian Vault 的 `.obsidian/plugins/feynman-workbench-launcher/`，在社区插件设置中启用后即可使用命令“用费曼法检查当前笔记”。

它只打开本机 `http://127.0.0.1:8001/`，并将当前笔记相对于 `pages/` 的路径传给工作台。请确保工作台的 Wiki 根目录与该 Vault 一致，且本地服务已经运行。

可恢复提示：

- 工作台打不开：先在浏览器访问 `http://127.0.0.1:8001/`，确认本地服务已启动，再重试命令。
- 当前笔记不在 `pages/`：插件会提示并停止打开；将笔记移到 Vault 的 `pages/` 目录后重试。
- 打开后显示页面不存在：在工作台“资料与设置”中把 Wiki 根目录连接为当前 Vault，再预览并保存。
