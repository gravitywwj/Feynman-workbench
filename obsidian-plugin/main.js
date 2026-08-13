const { Plugin, Notice } = require('obsidian');

module.exports = class FeynmanWorkbenchLauncher extends Plugin {
  async onload() {
    this.addCommand({
      id: 'open-current-note-in-feynman-workbench',
      name: '用费曼法检查当前笔记',
      checkCallback: (checking) => {
        const file = this.app.workspace.getActiveFile();
        if (!file) return false;
        if (!checking) this.openInWorkbench(file.path);
        return true;
      },
    });
    this.addRibbonIcon('graduation-cap', '用费曼法检查当前笔记', () => {
      const file = this.app.workspace.getActiveFile();
      if (file) this.openInWorkbench(file.path);
      else new Notice('请先打开一篇笔记。');
    });
  }

  async openInWorkbench(path) {
    if (!path.startsWith('pages/')) {
      new Notice('当前笔记不在 pages/ 中。请将它移入当前 Vault 的 pages 文件夹后再打开。');
      return;
    }
    // The workbench addresses notes relative to pages/, while Obsidian gives us
    // the Vault-relative path. Validate before opening so the failure is recoverable.
    const pagePath = path.slice('pages/'.length);
    try {
      const response = await fetch(`http://127.0.0.1:8001/api/concepts/page?path=${encodeURIComponent(pagePath)}`);
      if (response.status === 404) {
        new Notice('工作台已启动，但当前笔记不在它连接的 Wiki 中。请在“资料与设置”连接此 Vault 后重试。');
        return;
      }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
    } catch (_) {
      new Notice('费曼学习工作台未启动。请先访问 http://127.0.0.1:8001/ 启动或确认本地服务。');
      return;
    }
    const url = `http://127.0.0.1:8001/?path=${encodeURIComponent(pagePath)}`;
    const popup = window.open(url, '_blank', 'noopener');
    if (!popup) new Notice('无法打开工作台窗口。请允许 Obsidian 打开本地窗口后重试。');
  }
};
