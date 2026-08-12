/* 费曼学习工作台 — 前端逻辑（原生 JS，无依赖）
 * 左栏 Obsidian 式文件树 · 知识图谱视图 · 状态/重要性标注（写回 wiki frontmatter） */

const state = {
  concepts: [],
  expanded: new Set(),   // 展开的目录路径
  selected: null,        // 选中的概念对象
  currentMeta: null,     // 当前页面 meta（含 status/importance）
  searchMode: false,
};

/* ===== 工具 ===== */
function esc(s) {
  const d = document.createElement('div');
  d.textContent = s ?? '';
  return d.innerHTML;
}
async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err.detail || `请求失败 ${r.status}`);
  }
  return r.json();
}

/* ===== 顶栏：步骤高亮 ===== */
function setStep(n) {
  document.querySelectorAll('.step').forEach(el => {
    el.classList.toggle('active', +el.dataset.step === n);
  });
  document.querySelectorAll('[data-guide-step]').forEach(el => {
    el.classList.toggle('active', +el.dataset.guideStep === n);
  });
}

/* ===== 概念库 ===== */
async function loadConcepts() {
  try {
    const data = await api('/api/concepts');
    state.concepts = data.concepts;
    for (const c of data.concepts) {
      const parts = c.path.split('/');
      for (let i = 1; i < parts.length - 1; i++) state.expanded.add(parts.slice(0, i).join('/'));
    }
    document.getElementById('concept-count').textContent = data.total + ' 页';
    renderTree();
  } catch (e) {
    document.getElementById('concept-tree').innerHTML =
      `<div style="color:var(--err);padding:8px">加载失败：${esc(e.message)}</div>`;
  }
}

function impStars(imp) {
  if (imp === 'high') return '<span class="imp-stars">★★★</span>';
  if (imp === 'medium') return '<span class="imp-stars">★★</span>';
  if (imp === 'low') return '<span class="imp-stars">★</span>';
  return '';
}

/* 构建树：{ name, children: Map, pages: [] } */
function buildTree() {
  const root = { name: '', children: new Map(), pages: [] };
  for (const c of state.concepts) {
    const parts = c.path.split('/');
    let node = root;
    for (let i = 0; i < parts.length - 1; i++) {
      if (!node.children.has(parts[i])) {
        node.children.set(parts[i], { name: parts[i], children: new Map(), pages: [] });
      }
      node = node.children.get(parts[i]);
    }
    node.pages.push(c);
  }
  return root;
}

function dirSort(a, b) { return a.name.localeCompare(b.name, 'zh-Hans-CN'); }

let treeRoot = null;

function renderTree() {
  const wrap = document.getElementById('concept-tree');
  const q = document.getElementById('search-input').value.trim().toLowerCase();

  if (q) {
    state.searchMode = true;
    const hits = state.concepts.filter(c =>
      c.title.toLowerCase().includes(q) || c.path.toLowerCase().includes(q));
    wrap.innerHTML = hits.length
      ? hits.map(c => `
        <div class="search-result ${state.selected && state.selected.path === c.path ? 'selected' : ''}"
             data-path="${esc(c.path)}">
          <div class="sr-title"><span class="status-dot ${esc(c.status)}"></span>${esc(c.title)}${impStars(c.importance)}</div>
          <div class="sr-path">${esc(c.path)}</div>
        </div>`).join('')
      : `<div style="color:var(--text-2);padding:8px;font-size:13px">没有匹配的概念</div>`;
    wrap.querySelectorAll('.search-result').forEach(el => {
      el.addEventListener('click', () => selectConcept(el.dataset.path));
    });
    return;
  }

  state.searchMode = false;
  treeRoot = buildTree();
  wrap.innerHTML = renderDir([...treeRoot.children.values()].sort(dirSort));
  bindTreeEvents(wrap);
}

function renderDir(nodes) {
  let html = '';
  for (const node of nodes) {
    const path = nodePath(node);
    const isOpen = state.expanded.has(path);
    const subDirs = [...node.children.values()].sort(dirSort);
    const pages = [...node.pages].sort((a, b) => a.title.localeCompare(b.title, 'zh-Hans-CN'));
    const childHtml = isOpen ? renderChildren(subDirs, pages) : '';
    html += `
      <div class="tree-dir ${isOpen ? 'open' : ''}" data-dir="${esc(path)}">
        <span class="arrow">▶</span>
        <span class="dir-name">${esc(node.name)}</span>
        <span class="dir-count">${subDirs.length + pages.length}</span>
      </div>
      ${childHtml ? `<div class="tree-children">${childHtml}</div>` : ''}`;
  }
  return html;
}

function renderChildren(subDirs, pages) {
  let html = '';
  for (const d of subDirs) html += renderDir([d]);
  for (const p of pages) {
    html += `
      <div class="tree-item ${state.selected && state.selected.path === p.path ? 'selected' : ''}"
           data-path="${esc(p.path)}" title="${esc(p.path)}">
        <span class="status-dot ${esc(p.status)}" data-cyc="${esc(p.path)}"></span>
        <span class="item-name">${esc(p.title)}</span>
        ${impStars(p.importance)}
      </div>`;
  }
  return html;
}

function nodePath(node) {
  const find = (n, prefix) => {
    if (n === node) return prefix;
    for (const [name, child] of n.children) {
      const p = find(child, prefix ? prefix + '/' + name : name);
      if (p !== null) return p;
    }
    return null;
  };
  return find(treeRoot, '');
}

function bindTreeEvents(wrap) {
  wrap.querySelectorAll('.tree-dir').forEach(el => {
    el.addEventListener('click', () => {
      const path = el.dataset.dir;
      if (state.expanded.has(path)) state.expanded.delete(path);
      else state.expanded.add(path);
      renderTree();
    });
  });
  wrap.querySelectorAll('.tree-item').forEach(el => {
    el.addEventListener('click', (e) => {
      if (e.target.dataset.cyc) return;   // 状态点点击由循环逻辑处理
      selectConcept(el.dataset.path);
    });
  });
  // 树内状态点点击：循环切换 unread → reading → read → unread
  wrap.querySelectorAll('.status-dot[data-cyc]').forEach(dot => {
    dot.addEventListener('click', async (e) => {
      e.stopPropagation();
      const path = dot.dataset.cyc;
      const c = state.concepts.find(x => x.path === path);
      if (!c) return;
      const next = c.status === 'unread' ? 'reading' : c.status === 'reading' ? 'read' : 'unread';
      try {
        await api('/api/concepts/meta', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path, status: next }),
        });
        c.status = next;
        if (state.selected && state.selected.path === path) state.selected.status = next;
        renderTree();
        if (state.selected && state.selected.path === path) syncPageActions();
      } catch (err) {
        console.error(err);
      }
    });
  });
}

/* ===== 选中概念 → 加载正文 ===== */
async function selectConcept(path) {
  state.selected = state.concepts.find(c => c.path === path) || null;
  renderTree();
  setStep(1);
  try {
    const { meta, html } = await api('/api/concepts/page?path=' + encodeURIComponent(path));
    state.currentMeta = meta;
    document.getElementById('page-empty').classList.add('hidden');
    document.getElementById('page-content').classList.remove('hidden');
    document.getElementById('page-title').textContent = meta.title;
    document.getElementById('page-body').innerHTML = html;
    document.getElementById('page-body').classList.remove('expanded');
    document.getElementById('btn-toggle-reference').textContent = '查看完整资料';
    syncNoteButton();
    syncLocalStudyIndicators();
    renderPageMeta();
    syncPageActions();
    document.querySelectorAll('#page-body .wikilink.wl-ok').forEach(el => {
      el.addEventListener('click', () => {
        const t = el.dataset.target;
        const found = state.concepts.find(c => c.title === t || c.path.endsWith('/' + t + '.md'));
        if (found) selectConcept(found.path);
      });
    });
  } catch (e) {
    document.getElementById('page-body').innerHTML = `<p style="color:var(--err)">${esc(e.message)}</p>`;
  }
}

function storageGet(key) {
  try { return localStorage.getItem(key) || ''; } catch { return ''; }
}

function storageSet(key, value) {
  try { localStorage.setItem(key, value); } catch (e) { console.warn('无法保存本地学习记录', e); }
}

function noteKey(path) { return `feynman-note:${path}`; }
function recallKey(path) { return `feynman-recall:${path}`; }
function hasNote(path) { return Boolean(storageGet(noteKey(path)).trim()); }

function syncNoteButton() {
  const btn = document.getElementById('btn-open-notes');
  if (!btn || !state.selected) return;
  btn.textContent = hasNote(state.selected.path) ? '编辑学习笔记' : '学习笔记';
}

function syncLocalStudyIndicators() {
  const hasRecall = state.selected && Boolean(storageGet(recallKey(state.selected.path)).trim());
  const gapText = document.querySelector('.gap-card p');
  const gapChip = document.querySelector('.gap-card .empty-chip');
  if (gapChip?.dataset.diagnosed === 'true') return;
  gapText.textContent = hasRecall
    ? '已保存一份本地回顾。AI 诊断接入后，会依据参考资料在这里标出遗漏、模糊与可能的误解。'
    : '完成回顾后，AI 会依据参考资料在这里指出遗漏、模糊和可能的误解。';
  gapChip.textContent = hasRecall ? '等待 AI 诊断' : '等待你的回顾';
}

async function openNotes() {
  if (!state.selected) return;
  const { path, title } = state.selected;
  document.getElementById('notes-topic').textContent = `关联知识点：${title}`;
  const input = document.getElementById('note-input');
  input.value = storageGet(noteKey(path));
  document.getElementById('notes-status').textContent = '正在读取笔记…';
  document.getElementById('notes-modal').classList.remove('hidden');
  try {
    const note = await api('/api/study/notes?page_path=' + encodeURIComponent(path));
    input.value = note.content || '';
    storageSet(noteKey(path), input.value);
    document.getElementById('notes-status').textContent = note.updated_at ? `已从本地学习库恢复，更新于 ${note.updated_at}。` : '可记录理解、疑问或项目联想。';
  } catch (e) {
    document.getElementById('notes-status').textContent = '学习库暂不可用，当前内容仅保存在浏览器。';
  }
  setTimeout(() => input.focus(), 0);
}

function closeNotes() {
  document.getElementById('notes-modal').classList.add('hidden');
}

async function saveNotes() {
  if (!state.selected) return;
  const value = document.getElementById('note-input').value;
  storageSet(noteKey(state.selected.path), value);
  try {
    await api('/api/study/notes?page_path=' + encodeURIComponent(state.selected.path), {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content: value }),
    });
    document.getElementById('notes-status').textContent = value.trim() ? '已保存到本地学习库。' : '笔记已清空。';
  } catch (e) {
    document.getElementById('notes-status').textContent = '学习库暂不可用，已保存在浏览器。';
  }
  syncNoteButton();
  if (G.allNodes.length) refreshGraphData(false);
}

const RECALL_GUIDES = [
  '先把参考资料放到一边。不要追求完整，先说出你还记得的部分。',
  '先用一句话说：这个概念主要解决什么问题？然后再补充原因。',
  '想一想它是怎样起作用的。若你能举一个真实例子，理解会更扎实。',
  '如果要向同事解释它，你会先说哪一点？从最确定的部分开始即可。',
];
let recallGuideIndex = 0;

function setRecallStage(stage) {
  document.getElementById('recall-intro').classList.toggle('hidden', stage !== 'intro');
  document.getElementById('recall-editor').classList.toggle('hidden', stage !== 'editor');
  document.getElementById('recall-complete').classList.toggle('hidden', stage !== 'complete');
}

function openRecall() {
  if (!state.selected) return;
  const { path, title } = state.selected;
  recallGuideIndex = 0;
  document.getElementById('recall-topic').textContent = `本次回顾：${title}`;
  document.getElementById('recall-guide').textContent = RECALL_GUIDES[0];
  document.getElementById('recall-editor-guide').textContent = '从你最确定的一点开始，卡住是正常的；那正是下一步要核对的地方。';
  document.getElementById('recall-input').value = storageGet(recallKey(path));
  setRecallStage('intro');
  document.getElementById('recall-modal').classList.remove('hidden');
  setStep(1);
}

function beginRecall() {
  setRecallStage('editor');
  setStep(2);
  setTimeout(() => document.getElementById('recall-input').focus(), 0);
}

function nextRecallGuide() {
  recallGuideIndex = (recallGuideIndex + 1) % RECALL_GUIDES.length;
  document.getElementById('recall-editor-guide').textContent = RECALL_GUIDES[recallGuideIndex];
}

async function saveRecall() {
  if (!state.selected) return;
  const value = document.getElementById('recall-input').value.trim();
  if (value.length < 24) {
    document.getElementById('recall-editor-guide').textContent = '先再多讲一点：至少说明它是什么、为什么重要，或给出一个例子。';
    return;
  }
  const button = document.getElementById('btn-save-recall');
  button.disabled = true; button.textContent = '正在生成诊断…';
  storageSet(recallKey(state.selected.path), value);
  try {
    const result = await api('/api/study/sessions', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ page_path: state.selected.path, explanation: value }),
    });
    const tutor = result.turns.find(turn => turn.role === 'tutor')?.content || '请回到资料页，核对刚才最不确定的地方。';
    const gaps = result.gaps.map(gap => gap.content).join('；') || '这次讲解结构完整，可以尝试用更短的话再复述一次。';
    const sourceHint = result.diagnosis_source === 'llm'
      ? '本次反馈由已配置的学习助手依据参考资料生成。'
      : '当前使用本地结构检查；配置学习助手密钥后，会依据参考资料作更细致的核对。';
    document.querySelector('#recall-complete .assistant-message p').textContent = `已建立本次学习会话。${sourceHint} ${gaps} 接下来想一想：${tutor}`;
    document.querySelector('.gap-card p').textContent = result.gaps.length
      ? `本次识别出 ${result.gaps.length} 个待澄清点，可在下一轮回顾时逐一补全。`
      : '本次讲解结构完整；下一步可回到资料，用更短的话再复述一次。';
    const gapChip = document.querySelector('.gap-card .empty-chip');
    gapChip.textContent = result.gaps.length ? `${result.gaps.length} 个待澄清点` : '本次讲解已保存';
    gapChip.dataset.diagnosed = 'true';
    setRecallStage('complete');
  } catch (e) {
    document.getElementById('recall-editor-guide').textContent = `学习会话暂未保存：${e.message}`;
  } finally {
    button.disabled = false; button.textContent = '保存并生成诊断';
  }
  setStep(2);
  syncLocalStudyIndicators();
}

function closeRecall() {
  document.getElementById('recall-modal').classList.add('hidden');
}

function renderPageMeta() {
  const meta = state.currentMeta;
  const tagHtml = (meta.tags || []).map(t => `<span class="meta-item tag-item">#${esc(t)}</span>`).join('');
  document.getElementById('page-meta').innerHTML = `
    <span class="meta-item">${esc(meta.section || '')}</span>
    <span class="meta-item">${esc(meta.status || 'unread')}</span>
    <span class="meta-item">${esc(meta.read_time || '')}</span>
    <span class="meta-item">更新 ${esc(meta.updated || '')}</span>
    ${tagHtml}`;
}

function syncPageActions() {
  const meta = state.currentMeta;
  if (!meta) return;
  document.querySelectorAll('.act-btn').forEach(btn => {
    const field = btn.dataset.field;
    const value = btn.dataset.value;
    btn.classList.toggle('active', (meta[field] || '') === value);
  });
}

async function onActClick(btn) {
  const field = btn.dataset.field;
  let value = btn.dataset.value;
  // 已激活的再点一次 = 取消（清除该字段）
  if (btn.classList.contains('active') && field === 'importance') value = '';
  const path = state.selected.path;
  try {
    const r = await api('/api/concepts/meta', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, [field]: value }),
    });
    state.currentMeta[field] = r.updated[field] ?? value;
    const c = state.concepts.find(x => x.path === path);
    if (c) c[field] = state.currentMeta[field];
    if (field === 'status') { state.selected.status = state.currentMeta[field]; renderPageMeta(); }
    renderTree();
    syncPageActions();
  } catch (err) {
    console.error(err);
  }
}

/* ===== 知识图谱 ===== */
const G = {
  allNodes: [], allLinks: [], nodes: [], links: [], loaded: false,
  running: false, svg: null, group: null, tip: null,
  scale: 1, tx: 0, ty: 0, drag: null, W: 0, H: 0,
};

const IMP_R = { high: 13, medium: 10, low: 7 };
const STATUS_FILL = { unread: '#f3eee4', reading: '#d59a35', read: '#73855d' };
const IMPORTANCE_FILL = { high: '#c65734', medium: '#d59a35', low: '#8c9d79' };
const SECTION_FILL = ['#73855d', '#c65734', '#5978bb', '#ad6c9e', '#bd8a3d', '#557f78'];
const GRAPH_DEFAULTS = {
  sections: [], notesOnly: false, showIsolated: true,
  statuses: { unread: true, reading: true, read: true },
  colorMode: 'status', showLabels: true, labelOpacity: 0.78,
  nodeSize: 1, linkWidth: 1, centerForce: 0.012,
};
let graphSettings = null;

function graphSections() {
  const source = state.concepts.length ? state.concepts : G.allNodes;
  return [...new Set(source.map(c => c.section).filter(Boolean))]
    .sort((a, b) => a.localeCompare(b, 'zh-Hans-CN'));
}

function defaultGraphSettings() {
  const currentSection = state.selected?.section;
  return { ...GRAPH_DEFAULTS, sections: currentSection ? [currentSection] : graphSections(), statuses: { ...GRAPH_DEFAULTS.statuses } };
}

function initGraphSettings() {
  if (graphSettings) return;
  let saved = {};
  try { saved = JSON.parse(storageGet('feynman-graph-settings') || '{}'); } catch { saved = {}; }
  const defaults = defaultGraphSettings();
  graphSettings = {
    ...defaults,
    ...saved,
    sections: Array.isArray(saved.sections) ? saved.sections.filter(s => graphSections().includes(s)) : defaults.sections,
    statuses: { ...defaults.statuses, ...(saved.statuses || {}) },
  };
}

function saveGraphSettings() {
  storageSet('feynman-graph-settings', JSON.stringify(graphSettings));
}

function nodeColor(node) {
  if (graphSettings.colorMode === 'importance') return IMPORTANCE_FILL[node.importance] || '#9ca7ae';
  if (graphSettings.colorMode === 'section') {
    const index = Math.max(0, graphSections().indexOf(node.section));
    return SECTION_FILL[index % SECTION_FILL.length];
  }
  return STATUS_FILL[node.status] || '#9ca7ae';
}

function graphNodeRadius(node) {
  return (IMP_R[node.importance] || 7) * graphSettings.nodeSize;
}

function updateGraphScopeSummary() {
  const title = graphSettings.sections.length === graphSections().length
    ? '全部领域'
    : (graphSettings.sections.length ? graphSettings.sections.join('、') : '未选择领域');
  const links = G.links.length;
  document.getElementById('graph-scope-summary').textContent =
    `当前范围：${title} · ${G.nodes.length} 个知识点 · ${links} 条关联；拖拽节点、滚轮缩放。`;
}

function renderGraphSettings() {
  initGraphSettings();
  const counts = new Map(graphSections().map(s => [s, G.allNodes.filter(n => n.section === s).length]));
  document.getElementById('graph-section-list').innerHTML = graphSections().map(section => `
    <label class="check-row"><input class="graph-section-filter" type="checkbox" value="${esc(section)}" ${graphSettings.sections.includes(section) ? 'checked' : ''}>
      <span>${esc(section)}</span><small>${counts.get(section) || 0}</small></label>`).join('') || '<p class="settings-help">暂无可展示的知识领域。</p>';
  document.getElementById('graph-notes-only').checked = graphSettings.notesOnly;
  document.getElementById('graph-show-isolated').checked = graphSettings.showIsolated;
  document.querySelectorAll('.status-filter input').forEach(el => { el.checked = Boolean(graphSettings.statuses[el.value]); });
  document.getElementById('graph-color-mode').value = graphSettings.colorMode;
  document.getElementById('graph-show-labels').checked = graphSettings.showLabels;
  for (const [id, key, digits] of [
    ['graph-label-opacity', 'labelOpacity', 2], ['graph-node-size', 'nodeSize', 2],
    ['graph-link-width', 'linkWidth', 2], ['graph-center-force', 'centerForce', 3],
  ]) {
    document.getElementById(id).value = graphSettings[key];
    document.getElementById(`${id}-value`).textContent = Number(graphSettings[key]).toFixed(digits);
  }
}

async function loadGraph() {
  initGraphSettings();
  const data = await api('/api/concepts/graph');
  G.allNodes = data.nodes;
  G.allLinks = data.links;
  if (!graphSettings.sections.length && !storageGet('feynman-graph-settings')) {
    graphSettings.sections = defaultGraphSettings().sections;
  }
  G.loaded = true;
  renderGraphSettings();
  refreshGraphData(true);
}

function refreshGraphData(reheat = true) {
  if (!G.loaded) return;
  const canvas = document.getElementById('graph-canvas');
  const rect = canvas.getBoundingClientRect();
  G.W = Math.max(rect.width, 600);
  G.H = Math.max(rect.height, 400);
  const prior = new Map(G.nodes.map(node => [node.id, node]));
  const allowedSections = new Set(graphSettings.sections);
  let visible = G.allNodes.filter(node => allowedSections.has(node.section)
    && graphSettings.statuses[node.status] !== false
    && (!graphSettings.notesOnly || hasNote(node.id)));
  let ids = new Set(visible.map(node => node.id));
  let links = G.allLinks.filter(link => ids.has(link.source) && ids.has(link.target));
  if (!graphSettings.showIsolated) {
    const connected = new Set(links.flatMap(link => [link.source, link.target]));
    visible = visible.filter(node => connected.has(node.id));
    ids = new Set(visible.map(node => node.id));
    links = links.filter(link => ids.has(link.source) && ids.has(link.target));
  }
  const radius = Math.min(G.W, G.H) * 0.35;
  G.nodes = visible.map((node, index) => {
    const old = prior.get(node.id);
    return old && !reheat ? { ...node, x: old.x, y: old.y, vx: old.vx, vy: old.vy } : {
      ...node,
      x: G.W / 2 + Math.cos(index * 2.39996) * radius,
      y: G.H / 2 + Math.sin(index * 2.39996) * radius,
      vx: 0, vy: 0,
    };
  });
  const byId = new Map(G.nodes.map(node => [node.id, node]));
  G.links = links.map(link => ({ s: byId.get(link.source), t: byId.get(link.target) })).filter(link => link.s && link.t);
  if (reheat) { G.scale = 1; G.tx = 0; G.ty = 0; }
  buildGraphSurface();
  updateGraphScopeSummary();
  if (G.nodes.length) reheatGraph();
}

function buildGraphSurface() {
  const canvas = document.getElementById('graph-canvas');
  canvas.replaceChildren();
  G.svg = null; G.group = null;
  if (!G.nodes.length) {
    const empty = document.createElement('p');
    empty.className = 'graph-empty';
    empty.textContent = '当前筛选范围内没有可展示的知识点。请调整领域或内容筛选。';
    canvas.appendChild(empty);
    return;
  }
  G.svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  G.svg.setAttribute('viewBox', `0 0 ${G.W} ${G.H}`);
  G.svg.setAttribute('preserveAspectRatio', 'none');
  G.group = document.createElementNS('http://www.w3.org/2000/svg', 'g');
  G.svg.appendChild(G.group);
  canvas.appendChild(G.svg);
  if (G.tip) canvas.appendChild(G.tip);
  renderGraph();
}

function graphTransform() {
  G.group?.setAttribute('transform', `translate(${G.tx},${G.ty}) scale(${G.scale})`);
}

function renderGraph() {
  if (!G.group) return;
  G.group.replaceChildren();
  for (const link of G.links) {
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    line.classList.add('graph-link');
    line.setAttribute('stroke-width', graphSettings.linkWidth);
    line.setAttribute('x1', link.s.x); line.setAttribute('y1', link.s.y);
    line.setAttribute('x2', link.t.x); line.setAttribute('y2', link.t.y);
    line.dataset.a = link.s.id; line.dataset.b = link.t.id;
    G.group.appendChild(line);
  }
  for (const node of G.nodes) {
    const group = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    group.classList.add('graph-node');
    group.dataset.id = node.id;
    const radius = graphNodeRadius(node);
    const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    circle.setAttribute('r', radius);
    circle.setAttribute('fill', nodeColor(node));
    group.appendChild(circle);
    if (graphSettings.showLabels) {
      const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      label.classList.add('graph-label');
      label.setAttribute('opacity', graphSettings.labelOpacity);
      label.setAttribute('x', 0); label.setAttribute('y', radius + 13);
      label.setAttribute('text-anchor', 'middle');
      label.textContent = node.title.length > 14 ? node.title.slice(0, 13) + '…' : node.title;
      group.appendChild(label);
    }
    group.setAttribute('transform', `translate(${node.x},${node.y})`);
    G.group.appendChild(group);
  }
  graphTransform();
}

function reheatGraph() {
  if (!G.nodes.length) return;
  G.nodes.forEach((node, index) => {
    node.vx = Math.cos(index * 1.7) * 1.2;
    node.vy = Math.sin(index * 1.7) * 1.2;
  });
  if (!G.running) { G.running = true; requestAnimationFrame(tick); }
}

function tick() {
  if (!G.running || !G.group) return;
  const n = G.nodes.length;
  if (!n) { G.running = false; return; }
  const C_REP = 5000, C_SPR = 0.045, REST = 100, DAMP = 0.82, MAXV = 6;
  for (let i = 0; i < n; i++) {
    const a = G.nodes[i];
    for (let j = i + 1; j < n; j++) {
      const b = G.nodes[j];
      const dx = a.x - b.x, dy = a.y - b.y;
      const d2 = Math.max(dx * dx + dy * dy, 400);
      const d = Math.sqrt(d2), force = Math.min(C_REP / d2, 25);
      const fx = (dx / d) * force, fy = (dy / d) * force;
      a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy;
    }
  }
  for (const link of G.links) {
    const dx = link.t.x - link.s.x, dy = link.t.y - link.s.y;
    const distance = Math.max(Math.hypot(dx, dy), 1), force = C_SPR * (distance - REST);
    const fx = (dx / distance) * force, fy = (dy / distance) * force;
    link.s.vx += fx; link.s.vy += fy; link.t.vx -= fx; link.t.vy -= fy;
  }
  let energy = 0;
  for (const node of G.nodes) {
    if (G.drag?.id === node.id) continue;
    node.vx += (G.W / 2 - node.x) * graphSettings.centerForce;
    node.vy += (G.H / 2 - node.y) * graphSettings.centerForce;
    node.vx *= DAMP; node.vy *= DAMP;
    const speed = Math.hypot(node.vx, node.vy);
    if (speed > MAXV) { node.vx *= MAXV / speed; node.vy *= MAXV / speed; }
    node.x += node.vx; node.y += node.vy;
    energy += speed;
    if (node.x < 20) { node.x = 20; node.vx = -node.vx * 0.3; }
    if (node.x > G.W - 20) { node.x = G.W - 20; node.vx = -node.vx * 0.3; }
    if (node.y < 20) { node.y = 20; node.vy = -node.vy * 0.3; }
    if (node.y > G.H - 20) { node.y = G.H - 20; node.vy = -node.vy * 0.3; }
  }
  const links = G.group.querySelectorAll('.graph-link');
  G.links.forEach((link, index) => {
    links[index].setAttribute('x1', link.s.x); links[index].setAttribute('y1', link.s.y);
    links[index].setAttribute('x2', link.t.x); links[index].setAttribute('y2', link.t.y);
  });
  const nodes = G.group.querySelectorAll('.graph-node');
  G.nodes.forEach((node, index) => nodes[index].setAttribute('transform', `translate(${node.x},${node.y})`));
  if (!G.drag && energy < n * 0.03) { G.running = false; return; }
  requestAnimationFrame(tick);
}

function graphHover(id) {
  if (!G.group) return;
  const hot = new Set([id]);
  for (const link of G.links) {
    if (link.s.id === id) hot.add(link.t.id);
    if (link.t.id === id) hot.add(link.s.id);
  }
  G.group.querySelectorAll('.graph-node').forEach(node => node.classList.toggle('dim', !hot.has(node.dataset.id)));
  G.group.querySelectorAll('.graph-link').forEach(link => {
    const on = link.dataset.a === id || link.dataset.b === id;
    link.classList.toggle('dim', !on); link.classList.toggle('hot', on);
  });
}

function graphUnhover() {
  if (!G.group) return;
  G.group.querySelectorAll('.graph-node').forEach(node => node.classList.remove('dim'));
  G.group.querySelectorAll('.graph-link').forEach(link => link.classList.remove('dim', 'hot'));
}

function bindGraphEvents() {
  const canvas = document.getElementById('graph-canvas');
  const tip = G.tip = document.createElement('div');
  tip.className = 'graph-tip';
  tip.style.display = 'none';
  canvas.appendChild(tip);

  // 拖拽节点 / 空白拖动画布
  let mode = null, moved = false, startX = 0, startY = 0, baseTx = 0, baseTy = 0;
  canvas.addEventListener('mousedown', (e) => {
    moved = false;
    const target = e.target.closest('.graph-node');
    const rect = canvas.getBoundingClientRect();
    const mx = (e.clientX - rect.left - G.tx) / G.scale;
    const my = (e.clientY - rect.top - G.ty) / G.scale;
    if (target) {
      mode = 'node';
      const id = target.dataset.id;
      const node = G.nodes.find(x => x.id === id);
      G.drag = { id, dx: node.x - mx, dy: node.y - my };
      G.running = true;
    } else {
      mode = 'pan';
      startX = e.clientX; startY = e.clientY; baseTx = G.tx; baseTy = G.ty;
      G.svg?.classList.add('dragging');
    }
  });
  window.addEventListener('mousemove', (e) => {
    if (mode === 'node' && G.drag) {
      moved = true;
      const rect = canvas.getBoundingClientRect();
      const mx = (e.clientX - rect.left - G.tx) / G.scale;
      const my = (e.clientY - rect.top - G.ty) / G.scale;
      const node = G.nodes.find(x => x.id === G.drag.id);
      node.x = mx + G.drag.dx; node.y = my + G.drag.dy;
      node.vx = 0; node.vy = 0;
      if (!G.running) { G.running = true; requestAnimationFrame(tick); }
    } else if (mode === 'pan') {
      moved = true;
      G.tx = baseTx + (e.clientX - startX);
      G.ty = baseTy + (e.clientY - startY);
      graphTransform();
    }
  });
  window.addEventListener('mouseup', () => {
    mode = null; G.drag = null;
    G.svg?.classList.remove('dragging');
  });

  // 滚轮缩放（以鼠标为中心）
  canvas.addEventListener('wheel', (e) => {
    e.preventDefault();
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    const factor = e.deltaY < 0 ? 1.1 : 0.9;
    const ns = Math.max(0.3, Math.min(3, G.scale * factor));
    G.tx = mx - (mx - G.tx) * (ns / G.scale);
    G.ty = my - (my - G.ty) * (ns / G.scale);
    G.scale = ns;
    graphTransform();
  }, { passive: false });

  // 悬停高亮 + 提示
  canvas.addEventListener('mousemove', (e) => {
    if (mode) return;
    const target = e.target.closest('.graph-node');
    if (!target) { graphUnhover(); tip.style.display = 'none'; return; }
    const id = target.dataset.id;
    graphHover(id);
    const n = G.nodes.find(x => x.id === id);
    if (!n) return;
    const imp = n.importance ? { high: '高', medium: '中', low: '低' }[n.importance] : '未标注';
    const st = { unread: '未读', reading: '在读', read: '已读' }[n.status] || n.status;
    tip.innerHTML = `<div>${esc(n.title)}</div><div class="tip-sub">${esc(n.section)} · ${st} · 重要${imp}</div>`;
    tip.style.display = 'block';
    const rect = canvas.getBoundingClientRect();
    const tx = e.clientX - rect.left + 14, ty = e.clientY - rect.top + 14;
    tip.style.left = Math.min(tx, rect.width - 320) + 'px';
    tip.style.top = Math.min(ty, rect.height - 60) + 'px';
  });
  canvas.addEventListener('mouseleave', () => { graphUnhover(); tip.style.display = 'none'; });

  // 点击节点 → 打开页面
  canvas.addEventListener('click', (e) => {
    const target = e.target.closest('.graph-node');
    if (!target || moved || mode === 'node') return;
    const id = target.dataset.id;
    const c = state.concepts.find(x => x.path === id);
    if (c) { switchView(false); selectConcept(c.path); }
  });
}

function toggleGraph() {
  const show = document.getElementById('graph-view').classList.contains('hidden');
  switchView(show);
}

function switchView(showGraph) {
  document.getElementById('graph-view').classList.toggle('hidden', !showGraph);
  document.getElementById('layout-main').classList.toggle('hidden', showGraph);
  document.getElementById('btn-graph').classList.toggle('active', showGraph);
  if (showGraph) {
    initGraphSettings();
    renderGraphSettings();
    if (!G.loaded) loadGraph().catch(e => console.error(e));
    else refreshGraphData(false);
  } else {
    G.running = false;
  }
}

function escapeMultiline(text) {
  return esc(text).replace(/\n/g, '<br>');
}

async function openReviewPlan() {
  const modal = document.getElementById('review-modal');
  const hint = document.getElementById('review-modal-hint');
  const stack = document.getElementById('review-card-stack');
  modal.classList.remove('hidden');
  hint.textContent = '正在读取今日到期的复习卡。';
  stack.innerHTML = '';
  try {
    const data = await api('/api/study/reviews/due');
    hint.textContent = data.total ? `今天有 ${data.total} 张卡片等待复习。先回忆，再展开答案核对。` : '今天没有到期卡。完成一次回顾后，系统会自动生成下一轮复习卡。';
    stack.innerHTML = data.cards.length ? data.cards.map(card => `
      <article class="review-item" data-card-id="${card.id}">
        <small>${esc(card.page_title)} · 到期 ${esc(card.due)}</small>
        <h3>${esc(card.question)}</h3>
        <p class="review-answer hidden">${escapeMultiline(card.answer)}</p>
        <button class="text-btn review-show-answer">查看答案</button>
        <div class="review-rating hidden">
          <button class="btn btn-quiet" data-rating="again">不记得</button>
          <button class="btn btn-quiet" data-rating="hard">困难</button>
          <button class="btn btn-quiet" data-rating="good">记得</button>
          <button class="btn btn-primary" data-rating="easy">很熟</button>
        </div>
      </article>`).join('') : '<p class="review-empty">暂无到期卡。完成回顾后，复习卡会出现在这里。</p>';
  } catch (e) {
    hint.textContent = `暂时无法读取复习计划：${e.message}`;
  }
}

document.getElementById('review-card-stack').addEventListener('click', async (e) => {
  const item = e.target.closest('.review-item');
  if (!item) return;
  if (e.target.classList.contains('review-show-answer')) {
    item.querySelector('.review-answer').classList.remove('hidden');
    item.querySelector('.review-rating').classList.remove('hidden');
    e.target.classList.add('hidden');
    return;
  }
  const rating = e.target.dataset.rating;
  if (!rating) return;
  item.querySelectorAll('button').forEach(button => { button.disabled = true; });
  try {
    await api(`/api/study/reviews/${item.dataset.cardId}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rating }),
    });
    item.remove();
    const remaining = document.querySelectorAll('#review-card-stack .review-item').length;
    document.getElementById('review-modal-hint').textContent = remaining ? `还剩 ${remaining} 张到期卡。` : '今日复习完成，下一次会按你的评分安排。';
    if (!remaining) document.getElementById('review-card-stack').innerHTML = '<p class="review-empty">今日复习已完成。</p>';
  } catch (err) {
    item.querySelectorAll('button').forEach(button => { button.disabled = false; });
  }
});

/* ===== 事件绑定 ===== */
document.getElementById('search-input').addEventListener('input', renderTree);
document.querySelectorAll('.act-btn').forEach(btn => {
  btn.addEventListener('click', () => onActClick(btn));
});
document.getElementById('btn-graph').addEventListener('click', toggleGraph);
document.getElementById('btn-start').addEventListener('click', openRecall);
document.getElementById('btn-start-inline').addEventListener('click', openRecall);
document.getElementById('btn-open-notes').addEventListener('click', openNotes);
document.getElementById('btn-close-notes').addEventListener('click', closeNotes);
document.getElementById('btn-save-notes').addEventListener('click', saveNotes);
document.getElementById('notes-modal').addEventListener('click', (e) => {
  if (e.target.id === 'notes-modal') closeNotes();
});
document.getElementById('btn-close-recall').addEventListener('click', closeRecall);
document.getElementById('btn-close-recall-complete').addEventListener('click', closeRecall);
document.getElementById('btn-recall-ready').addEventListener('click', beginRecall);
document.getElementById('btn-recall-hint').addEventListener('click', nextRecallGuide);
document.getElementById('btn-save-recall').addEventListener('click', saveRecall);
document.getElementById('recall-modal').addEventListener('click', (e) => {
  if (e.target.id === 'recall-modal') closeRecall();
});
document.getElementById('btn-toggle-reference').addEventListener('click', () => {
  const body = document.getElementById('page-body');
  const expanded = body.classList.toggle('expanded');
  document.getElementById('btn-toggle-reference').textContent = expanded ? '收起完整资料' : '查看完整资料';
});
document.getElementById('btn-review').addEventListener('click', () => {
  openReviewPlan();
});
document.getElementById('btn-review-inline').addEventListener('click', () => {
  openReviewPlan();
});
document.getElementById('btn-review-start').addEventListener('click', () => {
  openReviewPlan();
});
document.getElementById('btn-close-review').addEventListener('click', () => {
  document.getElementById('review-modal').classList.add('hidden');
});
document.getElementById('review-modal').addEventListener('click', (e) => {
  if (e.target.id === 'review-modal') document.getElementById('review-modal').classList.add('hidden');
});

document.getElementById('btn-graph-settings').addEventListener('click', () => {
  document.getElementById('graph-settings').classList.toggle('hidden');
});
document.getElementById('btn-reset-graph').addEventListener('click', () => {
  graphSettings = defaultGraphSettings();
  saveGraphSettings();
  renderGraphSettings();
  refreshGraphData(true);
});
document.getElementById('graph-section-list').addEventListener('change', (e) => {
  if (!e.target.classList.contains('graph-section-filter')) return;
  graphSettings.sections = [...document.querySelectorAll('.graph-section-filter:checked')].map(el => el.value);
  saveGraphSettings();
  refreshGraphData(true);
});
document.getElementById('graph-notes-only').addEventListener('change', (e) => {
  graphSettings.notesOnly = e.target.checked; saveGraphSettings(); refreshGraphData(true);
});
document.getElementById('graph-show-isolated').addEventListener('change', (e) => {
  graphSettings.showIsolated = e.target.checked; saveGraphSettings(); refreshGraphData(true);
});
document.querySelectorAll('.status-filter input').forEach(input => input.addEventListener('change', (e) => {
  graphSettings.statuses[e.target.value] = e.target.checked; saveGraphSettings(); refreshGraphData(true);
}));
document.getElementById('graph-color-mode').addEventListener('change', (e) => {
  graphSettings.colorMode = e.target.value; saveGraphSettings(); renderGraph();
});
document.getElementById('graph-show-labels').addEventListener('change', (e) => {
  graphSettings.showLabels = e.target.checked; saveGraphSettings(); renderGraph();
});
for (const [id, key, digits, redraw] of [
  ['graph-label-opacity', 'labelOpacity', 2, true], ['graph-node-size', 'nodeSize', 2, true],
  ['graph-link-width', 'linkWidth', 2, true], ['graph-center-force', 'centerForce', 3, false],
]) {
  document.getElementById(id).addEventListener('input', (e) => {
    graphSettings[key] = Number(e.target.value);
    document.getElementById(`${id}-value`).textContent = graphSettings[key].toFixed(digits);
    saveGraphSettings();
    if (redraw) renderGraph();
  });
}
document.getElementById('btn-reheat-graph').addEventListener('click', reheatGraph);

/* ===== 启动 ===== */
loadConcepts();
bindGraphEvents();
