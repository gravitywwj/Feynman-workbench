/* 费曼学习工作台 — 前端逻辑（原生 JS，无依赖）
 * 左栏 Obsidian 式文件树 · 知识图谱视图 · 状态/重要性标注（写回 wiki frontmatter） */

const state = {
  concepts: [],
  expanded: new Set(),   // 展开的目录路径
  selected: null,        // 选中的概念对象
  currentMeta: null,     // 当前页面 meta（含 status/importance）
  searchMode: false,
  librarySort: 'default',
  libraryStatus: 'all',
  workspace: null,
};

const readingSettingsKey = 'feynman-reading-settings';
const defaultReadingSettings = {
  font: 'sans',
  fontSize: 16,
  lineHeight: 1.85,
  width: 'comfortable',
  theme: 'light',
};
let readingSettings = loadReadingSettings();
let recallStartedAt = null;

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

function playEntryMotion(element, className = 'content-enter') {
  if (!element || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  element.classList.remove(className);
  void element.offsetWidth;
  element.classList.add(className);
  element.addEventListener('animationend', () => element.classList.remove(className), { once: true });
}

/* ===== 顶栏：步骤高亮 ===== */
function setStep(n) {
  document.querySelectorAll('.step').forEach(el => {
    const active = +el.dataset.step === n;
    el.classList.toggle('active', active);
    el.setAttribute('aria-current', active ? 'step' : 'false');
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
    document.getElementById('concept-count').textContent = data.total + ' 页';
    renderTree();
    loadRecentNotes();
    loadReviewReminder();
    loadHomeAction();
    loadTodayStudyTime();
    selectRequestedConcept();
  } catch (e) {
    document.getElementById('concept-tree').innerHTML =
      `<div style="color:var(--err);padding:8px">加载失败：${esc(e.message)}</div>`;
  }
}

function formatStudyTime(seconds) {
  if (!seconds) return '尚无记录';
  if (seconds < 60) return '不足 1 分钟';
  const minutes = Math.round(seconds / 60);
  return minutes < 60 ? `${minutes} 分钟` : `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分`;
}

async function loadTodayStudyTime() {
  const value = document.getElementById('study-time-value');
  try {
    const summary = await api('/api/study/today-summary');
    value.textContent = summary.sessions ? formatStudyTime(summary.elapsed_seconds) : '尚无记录';
  } catch { value.textContent = '暂不可用'; }
}

async function loadHomeAction() {
  const title = document.getElementById('home-action-title');
  const detail = document.getElementById('home-action-detail');
  const button = document.getElementById('btn-home-action');
  const alternative = document.getElementById('btn-home-alternative');
  try {
    const action = await api('/api/study/home');
    setHomeDashboardVisible(!state.selected);
    title.textContent = action.title;
    detail.textContent = action.detail;
    button.disabled = action.type === 'empty';
    button.textContent = { configure: '连接资料或体验示例', review: '开始今日复习', continue: '继续这次学习', start: '开始这个概念', empty: '等待 Wiki 内容' }[action.type] || '开始学习';
    alternative.classList.toggle('hidden', !(action.type === 'start' && action.alternatives?.length));
    playEntryMotion(document.getElementById('page-empty'));
    alternative.onclick = () => {
      const next = action.alternatives?.[0];
      if (!next) return;
      action.page_path = next.path;
      action.title = `改为从「${next.title}」开始`;
      action.detail = `为什么是它：${next.why || '这是同一优先级中的另一项建议。'} 预计 ${next.estimated_minutes || '几'} 分钟。完成后：${next.benefit || '会留下新的学习证据。'}`;
      action.alternatives = action.alternatives.slice(1);
      title.textContent = action.title; detail.textContent = action.detail;
      alternative.classList.toggle('hidden', !action.alternatives.length);
    };
    button.onclick = async () => {
      if (action.type === 'configure') return openWorkspace();
      if (action.type === 'review') return openReviewPlan('scheduled');
      if (action.page_path) await selectConcept(action.page_path);
      if (action.type === 'continue') openRecall('simplify', action.session_id);
    };
    if (!state.selected) loadHomeDashboard();
  } catch {
    setHomeDashboardVisible(!state.selected);
    title.textContent = '从一个知识点开始';
    detail.textContent = '选择一个概念，完成回忆表达与诊断。';
    button.disabled = true;
    alternative.classList.add('hidden');
  }
}

function setHomeDashboardVisible(active) {
  document.body.classList.toggle('home-mode', active);
  document.getElementById('dashboard-rail').classList.toggle('hidden', !active);
  document.getElementById('default-right-rail').classList.toggle('hidden', active);
}

function dashboardExcerpt(value, limit = 52) {
  const text = String(value || '').replace(/\s+/g, ' ').trim();
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
}

function dashboardConceptRow(concept, urgent = false) {
  const mastery = concept.mastery || {};
  return `<button class="dashboard-row${urgent ? ' urgent' : ''}" type="button" data-dashboard-path="${esc(concept.path)}"><span>${esc(concept.title)}</span><small>${esc(mastery.detail || mastery.label || '继续补全理解')}</small></button>`;
}

function dashboardGapRow(gap) {
  return `<button class="dashboard-row urgent" type="button" data-dashboard-path="${esc(gap.page_path)}"><span>${esc(dashboardExcerpt(gap.content))}</span><small>${esc(gap.page_title)} · 待你补充</small></button>`;
}

function renderHomeProgress(report) {
  const completed = Number(report?.summary?.completed_sessions || 0);
  const progress = document.getElementById('home-progress-bar');
  progress.max = Math.max(5, completed || 1);
  progress.value = completed;
  document.getElementById('home-progress-value').textContent = `${completed} 次`;
  document.getElementById('home-progress-hint').textContent = completed
    ? `本周已完成 ${completed} 次回忆表达；下次请继续用自己的话讲清楚。`
    : '本周还没有完成的回忆表达；从今天的这一个知识点开始。';
}

function renderMasteryChanges(report, maintainingCount) {
  const summary = report?.summary || {};
  const rows = [
    `完成 ${Number(summary.completed_sessions || 0)} 次回忆表达`,
    `补全 ${Number(summary.revised_gaps || 0)} 个盲区`,
    `完成 ${Number(summary.reviews || 0)} 次间隔复习`,
  ];
  document.getElementById('dashboard-maintaining-count').textContent = `${maintainingCount} 保持`;
  document.getElementById('dashboard-mastery-hint').textContent = report?.has_evidence
    ? '只记录已完成的学习证据，不把阅读次数当成掌握。'
    : '本周尚无新的掌握证据，完成一次回忆表达后会在这里留下变化。';
  document.getElementById('dashboard-mastery-list').innerHTML = rows.map((row, index) =>
    `<p class="dashboard-evidence${index === 0 && Number(summary.completed_sessions || 0) ? ' gained' : ''}">${esc(row)}</p>`
  ).join('');
}

async function loadHomeDashboard() {
  if (state.selected) return;
  const gapsHint = document.getElementById('dashboard-gaps-hint');
  const masteryHint = document.getElementById('dashboard-mastery-hint');
  const needsHint = document.getElementById('dashboard-needs-hint');
  try {
    const [gapData, report] = await Promise.all([
      api('/api/study/gaps?limit=100&status=open'),
      api('/api/study/weekly-report'),
    ]);
    if (state.selected) return;
    const gaps = gapData.gaps || [];
    const maintainingCount = state.concepts.filter(concept => concept.mastery?.level === 'maintaining').length;
    const levels = { unseen: 0, read: 1, recalled: 2, checked: 3, maintaining: 4 };
    const openGapPaths = new Set(gaps.map(gap => gap.page_path));
    const needs = state.concepts
      .filter(concept => concept.mastery?.level !== 'maintaining' && !openGapPaths.has(concept.path))
      .sort((a, b) => (levels[a.mastery?.level] ?? 9) - (levels[b.mastery?.level] ?? 9) || a.title.localeCompare(b.title, 'zh-Hans-CN'))
      .slice(0, 2);

    document.getElementById('dashboard-gap-count').textContent = String(gaps.length);
    gapsHint.textContent = gaps.length
      ? '先把这些说不清的地方补完整，再继续扩展新知识。'
      : '暂时没有开放盲区；下一次回忆表达会继续帮你发现遗漏。';
    document.getElementById('dashboard-gaps-list').innerHTML = gaps.slice(0, 2).map(dashboardGapRow).join('')
      || '<p class="dashboard-empty">暂无待处理盲区。</p>';

    renderMasteryChanges(report, maintainingCount);
    document.getElementById('dashboard-needs-count').textContent = String(needs.length);
    needsHint.textContent = needs.length
      ? '这些知识点还没有形成可核对的理解，适合安排下一次回忆表达。'
      : '当前已收录的知识点都已有保持记录，继续完成复习即可。';
    document.getElementById('dashboard-needs-list').innerHTML = needs.map((concept, index) => dashboardConceptRow(concept, index === 0)).join('')
      || '<p class="dashboard-empty">暂无需要补洞的知识点。</p>';
    renderHomeProgress(report);
  } catch (e) {
    gapsHint.textContent = '暂时无法读取盲区摘要。';
    masteryHint.textContent = '暂时无法读取掌握变化。';
    needsHint.textContent = '暂时无法读取需要补洞的知识点。';
  }
}

async function loadWorkspace() {
  try {
    state.workspace = await api('/api/study/workspace');
  } catch { state.workspace = null; }
}

async function loadRecentNotes() {
  const rail = {
    count: document.getElementById('recent-count'),
    hint: document.getElementById('recent-hint'),
    list: document.getElementById('recent-list'),
  };
  const home = {
    count: document.getElementById('home-update-count'),
    hint: document.getElementById('home-update-hint'),
    list: document.getElementById('home-update-list'),
  };
  try {
    const data = await api('/api/concepts/recent?days=14&limit=5');
    if (data.total > 10) {
      const dates = [...new Set(data.concepts.map(concept => concept.created).filter(Boolean))];
      const batchHint = `${dates[0] || '近期'} 一次加入 ${data.total} 条资料；阅读状态变化不会让旧笔记重新出现。`;
      rail.count.textContent = `${data.total} 条`;
      rail.hint.textContent = `${batchHint} 完成学习后，此处会优先展示待补充与复习。`;
      rail.list.innerHTML = `<p class="recent-empty">已聚合本次批量导入，避免用旧资料长期占据提醒位。</p>`;
      home.count.textContent = `${data.total} 条`;
      home.hint.textContent = `${batchHint} 已聚合显示，避免占满首页。`;
      home.list.innerHTML = `<p class="home-update-empty">已聚合本次批量加入；可在左侧按加入时间继续查看。</p>`;
      return;
    }
    const items = data.concepts.map(concept => `
      <button class="recent-note" type="button" data-path="${esc(concept.path)}">
        <span>${esc(concept.title)}</span><small>${esc(concept.created)}</small>
      </button>`).join('');
    rail.count.textContent = data.total ? `${data.total} 条` : '暂无';
    rail.hint.textContent = `最近 ${data.days} 天首次加入的笔记。阅读状态变化不会让旧笔记重新出现。`;
    rail.list.innerHTML = items || '<p class="recent-empty">最近没有带加入日期的新笔记。</p>';
    home.count.textContent = data.total ? `${data.total} 条` : '暂无';
    home.hint.textContent = `最近 ${data.days} 天首次加入的笔记；普通编辑和阅读状态变化不会重复提醒。`;
    home.list.innerHTML = items || '<p class="home-update-empty">最近没有带加入日期的新笔记。</p>';
    document.querySelectorAll('#recent-list .recent-note, #home-update-list .recent-note').forEach(button => {
      button.addEventListener('click', () => selectConcept(button.dataset.path));
    });
  } catch (e) {
    [rail, home].forEach(target => {
      target.count.textContent = '—';
      target.hint.textContent = '暂时无法读取新收录提醒。';
      target.list.innerHTML = '';
    });
  }
}

async function loadReviewReminder() {
  const hint = document.getElementById('review-rail-hint');
  try {
    const [data, summary] = await Promise.all([
      api('/api/study/reviews/queue?mode=scheduled&limit=100'), api('/api/study/reviews/summary'),
    ]);
    hint.textContent = data.total
      ? `今天目标 ${summary.goal} 张，已完成 ${summary.completed} 张。还有 ${data.total} 张到期，预计 ${summary.estimated_minutes} 分钟。`
      : '今天没有到期卡。完成学习后，第一次复习会在隔天出现。';
  } catch (e) {
    hint.textContent = '暂时无法读取复习安排。';
  }
}

function impStars(imp) {
  if (imp === 'high') return '<span class="imp-stars">★★★</span>';
  if (imp === 'medium') return '<span class="imp-stars">★★</span>';
  if (imp === 'low') return '<span class="imp-stars">★</span>';
  return '';
}

/* 构建树：{ name, children: Map, pages: [] } */
function buildTree(concepts = state.concepts) {
  const root = { name: '', path: '', children: new Map(), pages: [] };
  for (const c of concepts) {
    const parts = c.path.split('/');
    let node = root;
    for (let i = 0; i < parts.length - 1; i++) {
      const path = parts.slice(0, i + 1).join('/');
      if (!node.children.has(parts[i])) {
        node.children.set(parts[i], { name: parts[i], path, children: new Map(), pages: [] });
      }
      node = node.children.get(parts[i]);
    }
    node.pages.push(c);
  }
  return root;
}

function dirSort(a, b) { return a.name.localeCompare(b.name, 'zh-Hans-CN'); }

function conceptSort(a, b) {
  if (state.librarySort === 'created_desc' || state.librarySort === 'updated_desc') {
    const field = state.librarySort === 'created_desc' ? 'created' : 'updated';
    const byDate = String(b[field] || '').localeCompare(String(a[field] || ''));
    if (byDate) return byDate;
  }
  return a.title.localeCompare(b.title, 'zh-Hans-CN');
}

function nodeLatestDate(node, field) {
  let latest = '';
  for (const page of node.pages) latest = String(page[field] || '') > latest ? String(page[field] || '') : latest;
  for (const child of node.children.values()) {
    const childLatest = nodeLatestDate(child, field);
    latest = childLatest > latest ? childLatest : latest;
  }
  return latest;
}

function sortDirectories(nodes) {
  if (state.librarySort !== 'created_desc' && state.librarySort !== 'updated_desc') return [...nodes].sort(dirSort);
  const field = state.librarySort === 'created_desc' ? 'created' : 'updated';
  return [...nodes].sort((a, b) =>
    nodeLatestDate(b, field).localeCompare(nodeLatestDate(a, field)) || dirSort(a, b));
}

function visibleLibraryConcepts() {
  return state.libraryStatus === 'all'
    ? state.concepts
    : state.concepts.filter(concept => concept.status === state.libraryStatus);
}

function syncLibraryControls() {
  document.getElementById('library-sort').value = state.librarySort;
  document.querySelectorAll('[data-library-status]').forEach(button => {
    const active = button.dataset.libraryStatus === state.libraryStatus;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });
}

let treeRoot = null;

function renderTree() {
  const wrap = document.getElementById('concept-tree');
  const q = document.getElementById('search-input').value.trim().toLowerCase();
  const concepts = visibleLibraryConcepts();
  syncLibraryControls();

  if (q) {
    state.searchMode = true;
    const hits = concepts.filter(c =>
      c.title.toLowerCase().includes(q) || c.path.toLowerCase().includes(q)).sort(conceptSort);
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
  treeRoot = buildTree(concepts);
  wrap.innerHTML = renderDir(sortDirectories(treeRoot.children.values()));
  bindTreeEvents(wrap);
}

function renderDir(nodes) {
  let html = '';
  for (const node of nodes) {
    const path = node.path;
    const isOpen = state.expanded.has(path);
    const subDirs = sortDirectories(node.children.values());
    const pages = [...node.pages].sort(conceptSort);
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
  setHomeDashboardVisible(!state.selected);
  setConceptDrawer(false);
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
    playEntryMotion(document.getElementById('page-content'));
    document.querySelectorAll('#page-body .wikilink.wl-ok').forEach(el => {
      el.addEventListener('click', () => {
        const t = el.dataset.target;
        const found = state.concepts.find(c => c.path === el.dataset.path);
        if (found) selectConcept(found.path);
      });
    });
  } catch (e) {
    document.getElementById('page-body').innerHTML = `<p style="color:var(--err)">${esc(e.message)}</p>`;
  }
}

function selectRequestedConcept() {
  const requested = new URLSearchParams(window.location.search).get('path');
  if (requested && state.concepts.some(concept => concept.path === requested)) selectConcept(requested);
}

function storageGet(key) {
  try { return localStorage.getItem(key) || ''; } catch { return ''; }
}

function storageSet(key, value) {
  try { localStorage.setItem(key, value); } catch (e) { console.warn('无法保存本地学习记录', e); }
}

function loadReadingSettings() {
  try {
    const stored = JSON.parse(localStorage.getItem(readingSettingsKey) || '{}');
    return { ...defaultReadingSettings, ...stored };
  } catch {
    return { ...defaultReadingSettings };
  }
}

function saveReadingSettings() {
  try { localStorage.setItem(readingSettingsKey, JSON.stringify(readingSettings)); } catch (e) { console.warn('无法保存阅读外观设置', e); }
}

function applyReadingSettings() {
  const body = document.getElementById('page-body');
  if (!body) return;
  const dark = readingSettings.theme === 'dark';
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  body.dataset.font = readingSettings.font;
  body.dataset.width = readingSettings.width;
  body.style.setProperty('--reading-font-size', `${readingSettings.fontSize}px`);
  body.style.setProperty('--reading-line-height', String(readingSettings.lineHeight));
  document.getElementById('reading-font').value = readingSettings.font;
  document.getElementById('reading-width').value = readingSettings.width;
  document.getElementById('reading-font-size').value = readingSettings.fontSize;
  document.getElementById('reading-line-height').value = readingSettings.lineHeight;
  document.getElementById('reading-font-size-value').textContent = `${readingSettings.fontSize}px`;
  document.getElementById('reading-line-height-value').textContent = readingSettings.lineHeight.toFixed(2);
  const themeButton = document.getElementById('btn-theme-toggle');
  themeButton.textContent = dark ? '日间阅读' : '夜间阅读';
  themeButton.setAttribute('aria-pressed', String(dark));
  themeButton.setAttribute('aria-label', dark ? '切换至日间阅读界面' : '切换至夜间阅读界面');
  const mobileThemeButton = document.getElementById('btn-mobile-theme');
  if (mobileThemeButton) mobileThemeButton.textContent = dark ? '切换日间阅读' : '切换夜间阅读';
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

let ideaSelectedId = null;

function ideaStatusLabel(status) {
  return { open: '讨论中', draft: '待审核草稿', applied: '已写入 Wiki', kept_local: '仅本地保存', undone: '已撤销' }[status] || status;
}

function ideaListMarkup(item) {
  const active = Number(item.id) === Number(ideaSelectedId);
  return `<button class="idea-list-item ${active ? 'active' : ''}" type="button" data-idea-id="${item.id}" aria-pressed="${active}">
    <span class="idea-status-dot ${esc(item.status)}" aria-hidden="true"></span><span><b>${esc(item.title)}</b><small>${esc(ideaStatusLabel(item.status))} · ${esc(item.updated_at || item.created_at || '')}</small></span>
  </button>`;
}

function ideaAssessmentMarkup(assessment) {
  if (!assessment?.verdict) return '';
  const list = (items) => (items || []).map(item => `<li>${esc(item)}</li>`).join('') || '<li>暂未记录</li>';
  return `<section class="idea-assessment">
    <div class="idea-assessment-head"><span>暂定判断：${esc(assessment.verdict_label || '待验证')}</span><small>${esc(assessment.confidence || '低')}把握 · ${assessment.source === 'llm' ? '依据本地 Wiki 与对话' : '本地结构提示，未核验事实'}</small></div>
    <p>${esc(assessment.summary || '')}</p>
    <div class="idea-assessment-grid"><div><b>支持点</b><ul>${list(assessment.supporting_points)}</ul></div><div><b>疑点与前提</b><ul>${list(assessment.concerns)}</ul></div></div>
    ${assessment.open_questions?.length ? `<div><b>开放问题</b><ul>${list(assessment.open_questions)}</ul></div>` : ''}
    ${assessment.next_question ? `<p class="idea-next-question"><b>下一步：</b>${esc(assessment.next_question)}</p>` : ''}
  </section>`;
}

function wikiReviewMarkup(review) {
  if (!review) return '<p class="knowledge-safeguard">最终草稿尚未审核；当前判断不能直接作为入库依据。</p>';
  const verdict = { supported: '模型判断有原始资料支持', uncertain: '待验证', problematic: '模型发现错误或冲突' }[review.verdict] || '待验证';
  const raw = (review.raw_evidence || []).map(source => `<li><b>${esc(source.path)}</b><p>${esc(source.excerpt || '')}</p></li>`).join('');
  return `<section class="wiki-final-review" aria-live="polite">
    <h4>最终审核：${esc(verdict)}</h4><p>${esc(review.feedback || '')}</p>
    ${review.next_question ? `<p>待核对：${esc(review.next_question)}</p>` : ''}
    ${review.citations?.length ? `<p>核实依据：${review.citations.map(esc).join('、')}</p>` : ''}
    ${raw ? `<details><summary>查看本次使用的原始资料摘录</summary><ul>${raw}</ul></details>` : ''}
    ${review.diff ? `<details open><summary>拟写入页面的差异</summary><pre class="wiki-review-diff">${esc(review.diff)}</pre></details>` : ''}
    ${review.verdict === 'uncertain' ? '<p>写回后只列在“待验证问题”，不能视为事实。</p>' : ''}
  </section>`;
}

function ideaTurnMarkup(turn) {
  const meta = turn.metadata || {};
  return `<article class="idea-turn ${turn.role === 'user' ? 'user' : 'agent'}"><span class="idea-turn-label">${turn.role === 'user' ? '我' : turn.kind === 'summary' ? 'Agent · 讨论总结' : 'Agent'}</span>${historyEscape(turn.content)}${turn.role === 'agent' ? ideaAssessmentMarkup(meta) : ''}</article>`;
}

function ideaComposeMarkup() {
  const related = state.selected;
  return `<div class="idea-compose">
    <p class="eyebrow">新的想法</p><h3>先说出来，不必先证明它</h3>
    <label class="field-label" for="idea-title-input">标题（可选）<input id="idea-title-input" maxlength="120" placeholder="例如：我怀疑上下文长度会改变检索策略"></label>
    <label class="field-label" for="idea-input">想法内容<textarea id="idea-input" maxlength="10000" placeholder="描述你突然想到的判断、直觉、问题或项目联想…"></textarea></label>
    <div class="voice-controls idea-voice-controls"><button id="btn-voice-idea" class="btn btn-quiet" type="button">开始口述</button><span id="idea-voice-status" aria-live="polite">可使用浏览器语音输入，也可继续键入。</span></div>
    <label id="idea-related-label" class="idea-related ${related ? '' : 'hidden'}"><input id="idea-related-current" type="checkbox"> 关联当前学习页「<span id="idea-related-title">${esc(related?.title || '')}</span>」（可选）</label>
    <div class="idea-compose-footer"><span id="idea-compose-status">想法会先保存为独立讨论，不会自动写入 Wiki。</span><button id="btn-create-idea" class="btn btn-primary" type="button">开始讨论</button></div>
  </div>`;
}

function ideaDraftMarkup(item) {
  if (!item.draft_content || !['draft', 'applied', 'kept_local', 'undone'].includes(item.status)) return '';
  if (item.status === 'applied') return `<div class="idea-applied"><p>已写入 Wiki：<b>${esc(item.wiki_path || '')}</b></p><button class="btn btn-quiet btn-undo-idea" type="button" data-idea-id="${item.id}">撤销这次写入</button></div>`;
  if (item.status === 'kept_local') return `<div class="idea-applied"><p>草稿已保留在本地。之后仍可从这里复制或继续整理。</p></div>`;
  if (item.status === 'undone') return `<div class="idea-applied"><p>已撤销 Wiki 写入，原草稿仍保留在本地。</p></div>`;
  const review = item.assessment?.review;
  const reviewed = review && review.content === item.draft_content && review.verdict !== 'problematic';
  const candidates = (item.evidence || []).filter(source => source.path);
  const selected = review?.target_path || item.related_page_path || '';
  return `<section class="idea-draft-card"><h4>可审核的 Wiki 草稿</h4>
    <label class="field-label">讨论标题（本地）<input id="idea-draft-title" maxlength="120" value="${esc(item.draft_title || item.title)}"></label>
    <label class="field-label">页面正文<textarea id="idea-draft-content" maxlength="5000">${esc(item.draft_content)}</textarea></label>
    <label class="field-label">拟更新的学习页<select id="idea-review-target"><option value="">选择已有学习页</option>${candidates.map(source => `<option value="${esc(source.path)}" ${source.path === selected ? 'selected' : ''}>${esc(source.title || source.path)}</option>`).join('')}</select></label>
    ${candidates.length ? '<p class="knowledge-safeguard">审核会将草稿和所选页面的少量原始资料摘录发送至工作台已配置的 AI 服务；本地模式只标记待验证。</p>' : '<p class="knowledge-safeguard">未找到关联学习页；此想法先保留在本地，不新建 Wiki 页。</p>'}
    ${wikiReviewMarkup(review)}
    <div class="idea-draft-actions"><button class="btn btn-quiet btn-keep-idea" type="button" data-idea-id="${item.id}">只保留在本地</button>${candidates.length ? `<button class="btn btn-quiet btn-review-idea" type="button" data-idea-id="${item.id}">审核草稿</button>` : ''}${reviewed ? `<button class="btn btn-primary btn-apply-idea" type="button" data-idea-id="${item.id}" data-mode="${review.kind === 'verified' ? 'append_current' : 'append_pending'}">${review.kind === 'verified' ? '确认写入学习页' : '确认为待验证问题'}</button>` : ''}</div>
  </section>`;
}

function ideaDetailMarkup(item) {
  const canDiscuss = item.status === 'open';
  const turns = (item.turns || []).map(ideaTurnMarkup).join('');
  return `<section class="idea-thread-card" data-idea-id="${item.id}">
    <div class="idea-thread-head"><div><p class="eyebrow">独立想法</p><h3>${esc(item.title)}</h3></div><span class="idea-status-chip ${esc(item.status)}">${esc(ideaStatusLabel(item.status))}</span></div>
    <div class="idea-turns">${turns || '<p class="idea-empty">还没有讨论内容。</p>'}</div>
    ${canDiscuss ? `<div class="idea-message-compose"><label class="field-label" for="idea-message-input">继续补充或追问<textarea id="idea-message-input" maxlength="10000" placeholder="回应 Agent 的判断，补充例子、前提、反例或新的问题…"></textarea></label><div class="voice-controls idea-voice-controls"><button id="btn-voice-idea-message" class="btn btn-quiet" type="button">开始口述</button><span id="idea-message-voice-status" aria-live="polite">可使用浏览器语音输入，也可继续键入。</span></div><div class="idea-message-actions"><span>每一轮都会保存到这条独立想法中。</span><div><button id="btn-generate-idea-draft" class="btn btn-quiet" type="button" data-idea-id="${item.id}">整理为 Wiki 草稿</button><button id="btn-send-idea-message" class="btn btn-primary" type="button" data-idea-id="${item.id}">继续讨论</button></div></div></div>` : ''}
    ${ideaDraftMarkup(item)}
  </section>`;
}

function renderIdeaCompose() {
  const detail = document.getElementById('idea-detail');
  detail.innerHTML = ideaComposeMarkup();
  const relatedLabel = document.getElementById('idea-related-label');
  if (relatedLabel && state.selected) {
    document.getElementById('idea-related-title').textContent = state.selected.title;
  }
  setupIdeaVoiceInputs();
}

async function renderIdeaDetail(ideaId) {
  const detail = document.getElementById('idea-detail');
  try {
    const item = await api(`/api/ideas/${ideaId}`);
    ideaSelectedId = item.id;
    detail.innerHTML = ideaDetailMarkup(item);
    setupIdeaVoiceInputs();
    document.getElementById('ideas-hint').textContent = item.status === 'open'
      ? 'Agent 的判断是暂定的。继续补充前提、例子和反例，直到你愿意把讨论压缩成一条知识。'
      : '这条想法已经形成草稿或完成处理；你仍可以保留本地版本，或撤销已写入的 Wiki 页。';
    document.querySelectorAll('.idea-list-item').forEach(button => {
      const active = Number(button.dataset.ideaId) === Number(ideaSelectedId);
      button.classList.toggle('active', active); button.setAttribute('aria-pressed', String(active));
    });
    detail.querySelector('.idea-turns')?.lastElementChild?.scrollIntoView({ block: 'nearest' });
  } catch (e) {
    detail.innerHTML = `<div class="idea-empty-state"><h3>暂时无法打开这条想法</h3><p>${esc(e.message)}</p></div>`;
  }
}

async function loadIdeaList() {
  const list = document.getElementById('ideas-list');
  try {
    const data = await api('/api/ideas?limit=80');
    const ideas = data.ideas || [];
    list.innerHTML = ideas.map(ideaListMarkup).join('') || '<p class="ideas-empty">还没有想法。可以先记录一个突然想到的判断。</p>';
    if (ideaSelectedId && ideas.some(item => Number(item.id) === Number(ideaSelectedId))) {
      await renderIdeaDetail(ideaSelectedId);
    } else if (ideas.length) {
      await renderIdeaDetail(ideas[0].id);
    } else {
      ideaSelectedId = null; renderIdeaCompose();
    }
  } catch (e) {
    list.innerHTML = `<p class="ideas-empty">无法读取想法：${esc(e.message)}</p>`;
    renderIdeaCompose();
  }
}

function openIdeas() {
  document.getElementById('mobile-menu').classList.add('hidden');
  document.getElementById('btn-mobile-menu').setAttribute('aria-expanded', 'false');
  document.getElementById('ideas-modal').classList.remove('hidden');
  document.getElementById('ideas-hint').textContent = '这里不属于复习会话。随时记录一个想法，Agent 会先给出证据边界内的初步判断。';
  loadIdeaList();
}

function closeIdeas() { document.getElementById('ideas-modal').classList.add('hidden'); }

async function createIdea() {
  const button = document.getElementById('btn-create-idea');
  const input = document.getElementById('idea-input');
  const content = input?.value.trim() || '';
  if (content.length < 4) { document.getElementById('idea-compose-status').textContent = '先写下一条具体的想法，再开始讨论。'; input?.focus(); return; }
  button.disabled = true; button.textContent = 'Agent 正在初评…';
  try {
    const related = document.getElementById('idea-related-current')?.checked && state.selected ? state.selected.path : null;
    const item = await api('/api/ideas', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content, title: document.getElementById('idea-title-input')?.value.trim() || '', persona: recallPersona, related_page_path: related }) });
    ideaSelectedId = item.id;
    await loadIdeaList();
  } catch (e) {
    document.getElementById('idea-compose-status').textContent = `暂时无法开始讨论：${e.message}`;
    button.disabled = false; button.textContent = '开始讨论';
  }
}

async function sendIdeaMessage(button) {
  const input = document.getElementById('idea-message-input');
  const content = input?.value.trim() || '';
  if (content.length < 2) { input?.focus(); return; }
  button.disabled = true; button.textContent = 'Agent 思考中…';
  try {
    const item = await api(`/api/ideas/${button.dataset.ideaId}/messages`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content }) });
    ideaSelectedId = item.id; await loadIdeaList();
  } catch (e) { document.getElementById('ideas-hint').textContent = `这一轮暂未保存：${e.message}`; button.disabled = false; button.textContent = '继续讨论'; }
}

async function generateIdeaDraft(button) {
  button.disabled = true; button.textContent = '正在整理…';
  try { const item = await api(`/api/ideas/${button.dataset.ideaId}/draft`, { method: 'POST' }); ideaSelectedId = item.id; await loadIdeaList(); }
  catch (e) { document.getElementById('ideas-hint').textContent = `暂时无法生成草稿：${e.message}`; button.disabled = false; button.textContent = '整理为 Wiki 草稿'; }
}

async function reviewIdeaDraft(button) {
  const detail = button.closest('.idea-thread-card');
  const content = detail.querySelector('#idea-draft-content')?.value.trim() || '';
  const pagePath = detail.querySelector('#idea-review-target')?.value || '';
  if (!pagePath) { document.getElementById('ideas-hint').textContent = '先选择已有的关联学习页。'; return; }
  button.disabled = true; button.textContent = '正在审核…';
  try {
    const item = await api(`/api/ideas/${button.dataset.ideaId}/review`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content, page_path: pagePath }),
    });
    ideaSelectedId = item.id; await loadIdeaList();
  } catch (e) { document.getElementById('ideas-hint').textContent = `草稿未能审核：${e.message}`; button.disabled = false; button.textContent = '审核草稿'; }
}

async function applyIdea(button, mode) {
  const detail = button.closest('.idea-thread-card');
  const id = button.dataset.ideaId;
  const title = detail.querySelector('#idea-draft-title')?.value.trim() || '';
  const content = detail.querySelector('#idea-draft-content')?.value.trim() || '';
  button.disabled = true; button.textContent = mode === 'keep_local' ? '正在保存…' : '正在写入…';
  try { const item = await api(`/api/ideas/${id}/apply`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode, title, content }) }); ideaSelectedId = item.id; await loadIdeaList(); if (mode !== 'keep_local') loadConcepts(); }
  catch (e) { document.getElementById('ideas-hint').textContent = `未能处理草稿：${e.message}`; button.disabled = false; button.textContent = mode === 'keep_local' ? '只保留在本地' : '确认写回'; }
}

async function undoIdea(button) {
  button.disabled = true; button.textContent = '正在撤销…';
  try { const item = await api(`/api/ideas/${button.dataset.ideaId}/undo`, { method: 'POST' }); ideaSelectedId = item.id; await loadIdeaList(); loadConcepts(); }
  catch (e) { document.getElementById('ideas-hint').textContent = `无法自动撤销：${e.message}`; button.disabled = false; button.textContent = '撤销这次写入'; }
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

async function analyzeCurrentNote() {
  if (!state.selected) return;
  const input = document.getElementById('note-input');
  const content = input.value.trim();
  const button = document.getElementById('btn-analyze-note');
  if (content.length < 4) {
    document.getElementById('notes-status').textContent = '先写下一条具体的理解、疑问或想法，再让 Agent 整理。';
    input.focus();
    return;
  }
  button.disabled = true;
  button.textContent = '正在整理…';
  storageSet(noteKey(state.selected.path), input.value);
  try {
    await api('/api/study/notes?page_path=' + encodeURIComponent(state.selected.path), {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content: input.value }),
    });
    closeNotes();
    await openHistory('knowledge');
    await createKnowledgeUpdate(content);
  } catch (e) {
    document.getElementById('notes-status').textContent = `暂时无法整理笔记：${e.message}`;
  } finally {
    button.disabled = false;
    button.textContent = '交给 Agent 整理';
  }
}

let recallGuideIndex = 0;
let recallBrief = null;
let recallPersona = storageGet('feynman-recall-persona') || 'feynman';

let activeSessionId = null;
let latestOutcome = null;

function setRecallStage(stage) {
  document.getElementById('recall-intro').classList.toggle('hidden', stage !== 'intro');
  document.getElementById('recall-editor').classList.toggle('hidden', stage !== 'editor');
  document.getElementById('recall-complete').classList.toggle('hidden', stage !== 'complete');
  document.getElementById('recall-simplify').classList.toggle('hidden', stage !== 'simplify');
  document.getElementById('recall-outcome').classList.toggle('hidden', stage !== 'outcome');
}

async function loadRecallBrief() {
  if (!state.selected) return;
  const ready = document.getElementById('btn-recall-ready');
  ready.disabled = true;
  document.getElementById('recall-question').textContent = '正在依据当前 Wiki、笔记和待澄清点准备问题…';
  document.getElementById('recall-source').textContent = '准备中';
  try {
    recallBrief = await api('/api/study/recall-brief', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ page_path: state.selected.path, persona: recallPersona }),
    });
    document.getElementById('recall-persona-label').textContent = recallBrief.persona_label;
    document.getElementById('recall-guide').textContent = recallBrief.opening;
    document.getElementById('recall-question').textContent = recallBrief.question;
    document.getElementById('recall-why').textContent = recallBrief.why_now;
    document.getElementById('recall-prompts').innerHTML = recallBrief.follow_ups.map(prompt => `<li>${esc(prompt)}</li>`).join('');
    document.getElementById('recall-hint-text').textContent = recallBrief.hint;
    document.getElementById('recall-editor-guide').textContent = recallBrief.question;
    document.getElementById('recall-input').placeholder = `围绕这个问题表达：${recallBrief.question}`;
    document.getElementById('recall-source').textContent = recallBrief.source === 'llm' ? '已依据资料生成' : '本地资料引导';
    ready.disabled = false;
  } catch (e) {
    recallBrief = null;
    document.getElementById('recall-question').textContent = '暂时无法生成具体问题。请稍后再试，或先从资料中的定义与例子开始回忆。';
    document.getElementById('recall-source').textContent = '未生成';
  }
}

function syncRecallPersona() {
  document.querySelectorAll('input[name="recall-persona"]').forEach(input => {
    input.checked = input.value === recallPersona;
  });
}

function selectedRecallEvidence() {
  return [...document.querySelectorAll('input[name="recall-evidence-key"]:checked')].map(input => input.value);
}

function updateRecallEvidenceStatus() {
  const selected = selectedRecallEvidence();
  const status = document.getElementById('recall-evidence-status');
  if (selected.length < 2) {
    status.textContent = `还需选择 ${2 - selected.length} 项；选择后，要在表达中写出对应内容。`;
  } else if (selected.length > 3) {
    status.textContent = '最多选择 3 项，请留出最能代表本次理解的证据。';
  } else {
    status.textContent = `已选择 ${selected.length} 项。提交时会检查这些内容是否在表达中出现。`;
  }
}

function resetRecallEvidence() {
  document.querySelectorAll('input[name="recall-evidence-key"]').forEach(input => { input.checked = false; });
  document.getElementById('recall-uncertainty').value = '';
  updateRecallEvidenceStatus();
}

function openRecall(stage = 'intro', sessionId = null) {
  if (!state.selected) return;
  const { path, title } = state.selected;
  recallGuideIndex = 0;
  document.getElementById('recall-topic').textContent = `本次回顾：${title}`;
  syncRecallPersona();
  document.getElementById('recall-guide').textContent = '正在准备本次回忆引导…';
  document.getElementById('recall-editor-guide').textContent = '从你最确定的一点开始，卡住是正常的；那正是下一步要核对的地方。';
  document.getElementById('recall-input').value = storageGet(recallKey(path));
  if (stage !== 'simplify') resetRecallEvidence();
  activeSessionId = sessionId;
  recallStartedAt = Date.now();
  if (stage === 'simplify') {
    document.getElementById('simplify-input').value = '';
    setRecallStage('simplify');
    setStep(4);
  } else setRecallStage('intro');
  document.getElementById('recall-modal').classList.remove('hidden');
  setStep(1);
  if (stage !== 'simplify') loadRecallBrief();
}

function beginRecall() {
  if (!recallBrief) return;
  setRecallStage('editor');
  recallStartedAt ||= Date.now();
  setStep(2);
  setTimeout(() => document.getElementById('recall-input').focus(), 0);
}

function nextRecallGuide() {
  const guides = recallBrief?.follow_ups?.length ? recallBrief.follow_ups : [recallBrief?.hint || '先说明它解决什么问题，再补上关键步骤。'];
  recallGuideIndex = (recallGuideIndex + 1) % guides.length;
  document.getElementById('recall-editor-guide').textContent = guides[recallGuideIndex];
}

let speechRecognition = null;
let activeVoiceRecognition = null;

function setupVoiceInput(button, input, status, copy = {}) {
  if (!button || !input || !status) return null;
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const startLabel = copy.startLabel || '开始口述';
  const stopLabel = copy.stopLabel || '停止口述';
  const doneMessage = copy.doneMessage || '口述已结束，可继续编辑。';
  if (!SpeechRecognition) {
    button.disabled = true;
    status.textContent = '当前浏览器不提供语音输入，请继续键入。';
    return null;
  }
  const recognition = new SpeechRecognition();
  recognition.lang = 'zh-CN';
  recognition.interimResults = true;
  recognition.continuous = true;
  let committed = '';
  let recording = false;
  let failed = false;
  recognition.onstart = () => {
    if (activeVoiceRecognition && activeVoiceRecognition !== recognition) activeVoiceRecognition.stop();
    activeVoiceRecognition = recognition;
    recording = true;
    failed = false;
    committed = input.value.trim();
    button.textContent = stopLabel;
    status.textContent = '正在听。停顿后会持续写入输入框。';
  };
  recognition.onresult = (event) => {
    let transcript = '';
    for (let index = event.resultIndex; index < event.results.length; index += 1) transcript += event.results[index][0].transcript;
    input.value = [committed, transcript].filter(Boolean).join(committed ? ' ' : '');
    input.dispatchEvent(new Event('input', { bubbles: true }));
  };
  recognition.onerror = (event) => {
    failed = true;
    status.textContent = `语音输入已停止：${event.error}。可继续键入。`;
  };
  recognition.onend = () => {
    recording = false;
    if (activeVoiceRecognition === recognition) activeVoiceRecognition = null;
    button.textContent = startLabel;
    if (!failed) status.textContent = doneMessage;
  };
  button.addEventListener('click', () => {
    if (recording) {
      recognition.stop();
      return;
    }
    if (activeVoiceRecognition && activeVoiceRecognition !== recognition) activeVoiceRecognition.stop();
    try { recognition.start(); }
    catch (_error) { status.textContent = '语音输入暂时无法启动，请检查浏览器麦克风权限。'; }
  });
  return recognition;
}

function setupVoiceRecall() {
  speechRecognition = setupVoiceInput(
    document.getElementById('btn-voice-recall'),
    document.getElementById('recall-input'),
    document.getElementById('voice-status'),
    { doneMessage: '口述已结束，可继续编辑后生成盲区诊断。' },
  );
}

function setupIdeaVoiceInputs() {
  setupVoiceInput(
    document.getElementById('btn-voice-idea'),
    document.getElementById('idea-input'),
    document.getElementById('idea-voice-status'),
    { doneMessage: '口述已结束，可继续编辑想法。' },
  );
  setupVoiceInput(
    document.getElementById('btn-voice-idea-message'),
    document.getElementById('idea-message-input'),
    document.getElementById('idea-message-voice-status'),
    { doneMessage: '口述已结束，可继续编辑这一轮讨论。' },
  );
}

async function saveRecall() {
  if (!state.selected) return;
  const value = document.getElementById('recall-input').value.trim();
  const evidenceKeys = selectedRecallEvidence();
  if (!value) {
    document.getElementById('recall-editor-guide').textContent = '先从你记得的一点开始表达，再选择要提供的理解证据。';
    document.getElementById('recall-input').focus();
    return;
  }
  if (evidenceKeys.length < 2 || evidenceKeys.length > 3) {
    document.getElementById('recall-editor-guide').textContent = '请先选择 2 到 3 项理解证据；它们会替代原先的字数门槛。';
    updateRecallEvidenceStatus();
    return;
  }
  const button = document.getElementById('btn-save-recall');
  button.disabled = true; button.textContent = '正在生成诊断…';
  storageSet(recallKey(state.selected.path), value);
  try {
    const result = await api('/api/study/sessions', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        page_path: state.selected.path, explanation: value, persona: recallPersona,
        evidence_keys: evidenceKeys, uncertainty: document.getElementById('recall-uncertainty').value.trim(),
        elapsed_seconds: Math.round((Date.now() - (recallStartedAt || Date.now())) / 1000),
      }),
    });
    activeSessionId = result.session.id;
    const localStructure = result.diagnosis.confidence === 'structure_only';
    document.getElementById('diagnosis-strengths-title').textContent = localStructure ? '表达中检测到的结构证据' : '你已经讲清楚的内容';
    document.getElementById('diagnosis-gaps-title').textContent = localStructure ? '可补充的表达结构' : '需要补全的盲区';
    document.getElementById('diagnosis-strengths').innerHTML = (result.diagnosis.strengths.length ? result.diagnosis.strengths : ['已完成第一次表达，可以通过第二次表达继续校准。']).map(item => `<li>${esc(item)}</li>`).join('');
    document.getElementById('diagnosis-gaps').innerHTML = (result.gaps.length ? result.gaps : [{ content: '暂未发现结构性缺口，请用更短的话再讲一次验证记忆。', evidence: '' }]).map(gap => `<li>${esc(gap.content)}<small>${esc(gap.evidence || '')}</small></li>`).join('');
    document.getElementById('diagnosis-next-task').textContent = result.diagnosis.next_task;
    const evidenceLabels = (result.diagnosis.understanding_evidence?.checks || [])
      .filter(item => item.selected).map(item => item.label);
    const uncertainty = result.diagnosis.uncertainty || document.getElementById('recall-uncertainty').value.trim();
    document.getElementById('diagnosis-confidence').textContent = [
      evidenceLabels.length ? `本次可观察证据：${evidenceLabels.join('、')}。` : '',
      uncertainty ? `你标记的不确定点：${uncertainty}。诊断已优先围绕它给出下一步。` : '',
      result.diagnosis.confidence === 'reference_checked'
        ? '反馈已依据当前学习资料核对。'
        : '当前为表达结构提示：列出的是检测到的句子与可补充项，不判断事实准确性。',
    ].filter(Boolean).join(' ');
    const feedback = document.getElementById('diagnosis-feedback');
    feedback.classList.toggle('hidden', !localStructure);
    feedback.dataset.sessionId = String(activeSessionId || '');
    document.querySelector('.gap-card p').textContent = result.gaps.length
      ? `本次识别出 ${result.gaps.length} 个待澄清点，可在下一轮回顾时逐一补全。`
      : '本次讲解结构完整；下一步可回到资料，用更短的话再复述一次。';
    const gapChip = document.querySelector('.gap-card .empty-chip');
    gapChip.textContent = result.gaps.length ? `${result.gaps.length} 个待澄清点` : '本次讲解已保存';
    gapChip.dataset.diagnosed = 'true';
    setRecallStage('complete');
    setStep(3);
  } catch (e) {
    document.getElementById('recall-editor-guide').textContent = `学习会话暂未保存：${e.message}`;
  } finally {
    button.disabled = false; button.textContent = '保存并生成诊断';
  }
  setStep(2);
  syncLocalStudyIndicators();
}

function startSimplify() {
  if (!activeSessionId) return;
  document.getElementById('simplify-input').value = document.getElementById('recall-input').value.trim();
  setRecallStage('simplify');
  setStep(4);
  setTimeout(() => document.getElementById('simplify-input').focus(), 0);
}

async function saveSimplify() {
  if (!activeSessionId) return;
  const input = document.getElementById('simplify-input');
  const explanation = input.value.trim();
  if (!explanation) { input.focus(); return; }
  const button = document.getElementById('btn-save-simplify');
  button.disabled = true; button.textContent = '正在生成学习结果…';
  try {
    latestOutcome = await api(`/api/study/sessions/${activeSessionId}/simplify`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ explanation, elapsed_seconds: Math.round((Date.now() - (recallStartedAt || Date.now())) / 1000) }),
    });
    const outcome = latestOutcome.outcome;
    const masteryPoints = [...outcome.strengths, ...outcome.improvements];
    document.getElementById('outcome-strengths').innerHTML = (masteryPoints.length ? masteryPoints : ['已完成第二次表达。']).map(item => `<li>${esc(item)}</li>`).join('');
    document.getElementById('outcome-gaps').innerHTML = (outcome.remaining_gaps.length ? outcome.remaining_gaps : [{ content: '没有待澄清点' }]).map(gap => `<li>${esc(gap.content)}</li>`).join('');
    document.getElementById('outcome-improvements').innerHTML = outcome.improvements.map(item => `<li>${esc(item)}</li>`).join('');
    document.getElementById('outcome-tradeoffs').innerHTML = (outcome.tradeoffs.length ? outcome.tradeoffs : ['第二次表达保留了已检测到的结构；事实准确性仍需回到资料核对。']).map(item => `<li>${esc(item)}</li>`).join('');
    document.getElementById('outcome-first').textContent = outcome.first_explanation;
    document.getElementById('outcome-second').textContent = outcome.second_explanation;
    document.querySelector('.outcome-detail').open = false;
    const next = outcome.recommended_next;
    document.getElementById('outcome-review').textContent = `下一次复习：${outcome.next_review_date || '将在复习模块安排'}。${next ? `建议下一概念：${next.title}。${next.recommendation_reason || ''}` : ''}`;
    document.getElementById('btn-outcome-next').classList.toggle('hidden', !next);
    ensureOutcomeReflectionButton();
    document.getElementById('btn-outcome-reflection').disabled = false;
    document.getElementById('btn-outcome-reflection').textContent = '保存这次心得';
    setRecallStage('outcome');
    setStep(4);
    loadHomeAction();
    loadReviewReminder();
    loadTodayStudyTime();
  } catch (e) {
    input.setCustomValidity(e.message); input.reportValidity(); input.setCustomValidity('');
  } finally { button.disabled = false; button.textContent = '保存第二次表达'; }
}

function openOutcomeNext() {
  const next = latestOutcome?.outcome?.recommended_next;
  closeRecall();
  if (next) selectConcept(next.path);
}

function ensureOutcomeReflectionButton() {
  if (document.getElementById('btn-outcome-reflection')) return;
  const anchor = document.getElementById('btn-outcome-close');
  if (!anchor) return;
  const button = document.createElement('button');
  button.id = 'btn-outcome-reflection';
  button.className = 'btn btn-quiet';
  button.type = 'button';
  button.textContent = '保存这次心得';
  anchor.before(button);
  button.addEventListener('click', saveOutcomeReflection);
}

function saveOutcomeReflection() {
  const outcome = latestOutcome?.outcome;
  const session = latestOutcome?.session;
  if (!outcome || !session) return;
  const source = outcome.second_explanation || outcome.first_explanation || '';
  reflectionDraft = {
    content: source,
    sessionId: session.id,
    pagePath: session.page_path,
    pageTitle: session.page_title,
  };
  closeRecall();
  openHistory('reflections');
}

function closeRecall() {
  document.getElementById('recall-modal').classList.add('hidden');
}

let historyView = 'gaps';
let reflectionDraft = null;

function ensureReflectionTab() {
  const tabs = document.querySelector('.history-tabs');
  if (!tabs || tabs.querySelector('[data-history-view="reflections"]')) return;
  const button = document.createElement('button');
  button.className = 'history-tab';
  button.type = 'button';
  button.dataset.historyView = 'reflections';
  button.setAttribute('role', 'tab');
  button.setAttribute('aria-selected', 'false');
  button.textContent = '学习心得';
  tabs.append(button);
  button.addEventListener('click', () => openHistory('reflections'));
}

function reflectionSourceLabel(source) {
  return { manual: '手写心得', session: '学习后记录', summary: '阶段总结' }[source] || '学习心得';
}

function reflectionDraftValue() {
  return reflectionDraft?.content || '';
}

function reflectionComposeMarkup() {
  const linkedTitle = reflectionDraft?.pageTitle || state.selected?.title || '';
  const canLink = Boolean(reflectionDraft?.pagePath || state.selected?.path);
  return `
    <section class="reflection-compose" aria-labelledby="reflection-compose-title">
      <div class="reflection-compose-head"><div><p>把值得保留的理解写下来</p><h3 id="reflection-compose-title">新的学习心得</h3></div><span>仅保存在本机</span></div>
      <textarea id="reflection-input" maxlength="10000" placeholder="例如：我原来把两个概念混在一起了。现在我能区分它们的边界，但还想用一个真实例子验证。">${esc(reflectionDraftValue())}</textarea>
      <div class="reflection-compose-actions">
        ${canLink ? `<label class="reflection-link"><input id="reflection-link-current" type="checkbox" checked> 关联「${esc(linkedTitle)}」</label>` : '<span class="reflection-link">未关联特定知识点</span>'}
        <button id="btn-save-reflection" class="btn btn-primary" type="button">保存心得</button>
      </div>
    </section>`;
}

function reflectionItemMarkup(item) {
  const canEdit = item.source !== 'summary';
  const title = item.page_title || (item.source === 'summary' ? '阶段总结' : '未关联知识点');
  return `
    <article class="reflection-item" data-reflection-id="${item.id}">
      <div class="reflection-item-head">
        <label class="reflection-select"><input class="reflection-select-input" type="checkbox" value="${item.id}" aria-label="选择心得：${esc(title)}"><span aria-hidden="true"></span></label>
        <div><p>${esc(title)}</p><small>${esc(reflectionSourceLabel(item.source))} · ${esc(item.created_at || '')}</small></div>
        <span class="reflection-kind ${esc(item.source)}">${esc(reflectionSourceLabel(item.source))}</span>
      </div>
      <p class="reflection-content">${historyEscape(item.content)}</p>
      ${canEdit ? `<details class="reflection-edit"><summary>编辑</summary><label class="sr-only" for="reflection-edit-${item.id}">编辑学习心得</label><textarea id="reflection-edit-${item.id}" class="reflection-edit-input" maxlength="10000">${esc(item.content)}</textarea><button class="btn btn-quiet btn-update-reflection" type="button">保存修改</button></details>` : ''}
    </article>`;
}

async function renderReflections() {
  const hint = document.getElementById('history-hint');
  const content = document.getElementById('history-content');
  hint.textContent = '正在读取本机保存的学习心得…';
  content.innerHTML = '';
  try {
    const data = await api('/api/study/reflections?limit=100');
    hint.textContent = data.reflections.length
      ? '选中几条心得后可生成阶段总结。只有所选文本会用于 AI 总结。'
      : '从一次学习后的想法开始。心得与学习笔记分开保存，便于以后回看与归纳。';
    content.innerHTML = `${reflectionComposeMarkup()}
      <div class="reflection-summary-actions">
        <span id="reflection-selection-status">尚未选择心得</span>
        <button id="btn-summarize-reflections" class="btn btn-quiet" type="button" disabled>生成阶段总结</button>
      </div>
      <section class="reflection-timeline" aria-label="学习心得时间线">${data.reflections.map(reflectionItemMarkup).join('') || '<p class="review-empty">还没有心得。完成学习后，把一个新的理解、一处疑问或下一步行动写下来。</p>'}</section>`;
    playEntryMotion(content, 'view-enter');
    if (reflectionDraft) setTimeout(() => document.getElementById('reflection-input')?.focus(), 0);
  } catch (e) {
    hint.textContent = `暂时无法读取学习心得：${e.message}`;
  }
}

function selectedReflectionIds() {
  return [...document.querySelectorAll('.reflection-select-input:checked')].map(input => Number(input.value));
}

function syncReflectionSelection() {
  const ids = selectedReflectionIds();
  const button = document.getElementById('btn-summarize-reflections');
  const status = document.getElementById('reflection-selection-status');
  if (button) button.disabled = !ids.length;
  if (status) status.textContent = ids.length ? `已选择 ${ids.length} 条心得` : '尚未选择心得';
}

async function saveReflection() {
  const input = document.getElementById('reflection-input');
  const content = input?.value.trim() || '';
  const button = document.getElementById('btn-save-reflection');
  if (!content) { input?.focus(); return; }
  const context = reflectionDraft || (state.selected ? { pagePath: state.selected.path, pageTitle: state.selected.title } : null);
  const linked = document.getElementById('reflection-link-current')?.checked;
  button.disabled = true;
  button.textContent = '正在保存…';
  try {
    await api('/api/study/reflections', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, page_path: linked ? context?.pagePath || null : null, session_id: linked ? reflectionDraft?.sessionId || null : null }),
    });
    reflectionDraft = null;
    await renderReflections();
  } catch (e) {
    button.disabled = false;
    button.textContent = '保存心得';
    document.getElementById('history-hint').textContent = `未能保存心得：${e.message}`;
  }
}

async function updateReflection(item) {
  const input = item.querySelector('.reflection-edit-input');
  const button = item.querySelector('.btn-update-reflection');
  const content = input?.value.trim() || '';
  if (!content) { input?.focus(); return; }
  button.disabled = true;
  button.textContent = '正在保存…';
  try {
    await api(`/api/study/reflections/${item.dataset.reflectionId}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content }),
    });
    await renderReflections();
  } catch (e) {
    button.disabled = false;
    button.textContent = '保存修改';
    document.getElementById('history-hint').textContent = `未能保存修改：${e.message}`;
  }
}

async function summarizeSelectedReflections() {
  const ids = selectedReflectionIds();
  const button = document.getElementById('btn-summarize-reflections');
  if (!ids.length || !button) return;
  button.disabled = true;
  button.textContent = '正在整理…';
  try {
    const result = await api('/api/study/reflections/summary', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reflection_ids: ids }),
    });
    await renderReflections();
    document.getElementById('history-hint').textContent = result.summary_source === 'llm'
      ? '阶段总结已保存。它只依据你刚才选择的心得生成。'
      : '阶段总结已保存为本地摘录。开启 AI 深度诊断后可生成更结构化的总结。';
  } catch (e) {
    button.disabled = false;
    button.textContent = '生成阶段总结';
    document.getElementById('history-hint').textContent = `未能生成阶段总结：${e.message}`;
  }
}

let knowledgeSelectedId = null;

function ensureKnowledgeTab() {
  const tabs = document.querySelector('.history-tabs');
  if (!tabs || tabs.querySelector('[data-history-view="knowledge"]')) return;
  const button = document.createElement('button');
  button.className = 'history-tab';
  button.type = 'button';
  button.dataset.historyView = 'knowledge';
  button.setAttribute('role', 'tab');
  button.setAttribute('aria-selected', 'false');
  button.textContent = '知识演进';
  tabs.append(button);
  button.addEventListener('click', () => openHistory('knowledge'));
}

function knowledgeStatusLabel(status) {
  return { draft: '待审核', applied: '已写入', kept_local: '仅本地保存', undone: '已撤销' }[status] || status;
}

function knowledgeItemMarkup(item) {
  const active = Number(item.id) === Number(knowledgeSelectedId);
  return `<button class="knowledge-update-select ${active ? 'active' : ''}" type="button" data-knowledge-id="${item.id}" aria-pressed="${active}">
    <span class="knowledge-status-dot ${esc(item.status)}" aria-hidden="true"></span>
    <span><b>${esc(item.page_title)}</b><small>${esc(knowledgeStatusLabel(item.status))} · ${esc(item.created_at || '')}</small></span>
  </button>`;
}

function knowledgeEvidenceMarkup(item) {
  if (!item.evidence?.length) return '<p class="knowledge-empty">没有找到可展示的本地 Wiki 依据。</p>';
  return `<ul class="knowledge-evidence-list">${item.evidence.map(source => `
    <li><button class="text-btn btn-open-evidence" type="button" data-page-path="${esc(source.path)}">${esc(source.title)}</button><p>${esc(source.excerpt || '未截取到相关片段。')}</p></li>
  `).join('')}</ul>`;
}

function knowledgeDetailMarkup(item) {
  const analysis = item.analysis || {};
  const review = analysis.review;
  const reviewed = review && review.content === item.proposal && review.verdict !== 'problematic';
  const applied = item.status === 'applied';
  const canEdit = item.status === 'draft';
  return `<section class="knowledge-detail" data-knowledge-id="${item.id}" aria-labelledby="knowledge-detail-title">
    <div class="knowledge-detail-head"><div><p class="eyebrow">知识库更新草案</p><h3 id="knowledge-detail-title">${esc(item.page_title)}</h3></div><span class="knowledge-kind ${esc(item.status)}">${esc(knowledgeStatusLabel(item.status))}</span></div>
    <section class="knowledge-source"><h4>你的记录</h4><p>${historyEscape(item.source_content)}</p></section>
    <section class="knowledge-analysis"><h4>Agent 分析 <small>${analysis.source === 'llm' ? '仅依据下方本地资料' : '本地整理，不核验事实'}</small></h4><p>${esc(analysis.summary || '已整理为待审核草案。')}</p><p class="knowledge-answer">${esc(analysis.answer || '')}</p>${analysis.open_questions?.length ? `<ul>${analysis.open_questions.map(question => `<li>${esc(question)}</li>`).join('')}</ul>` : ''}</section>
    <section class="knowledge-evidence"><h4>本地 Wiki 依据</h4>${knowledgeEvidenceMarkup(item)}</section>
    <section class="knowledge-proposal"><label for="knowledge-proposal-${item.id}">建议写入内容</label><textarea id="knowledge-proposal-${item.id}" maxlength="5000" ${canEdit ? '' : 'readonly'}>${esc(item.proposal)}</textarea></section>
    ${canEdit ? `<p class="knowledge-safeguard">最终审核会将草稿和当前学习页的少量原始资料摘录发送至工作台已配置的 AI 服务；本地模式只标记待验证。</p>
    ${wikiReviewMarkup(review)}
    <div class="knowledge-actions"><button class="btn btn-quiet btn-keep-knowledge" type="button">只保留在本地</button><button class="btn btn-quiet btn-review-knowledge" type="button">审核草稿</button>${reviewed ? `<button class="btn btn-primary btn-apply-knowledge" type="button" data-mode="${review.kind === 'verified' ? 'append_current' : 'append_pending'}">${review.kind === 'verified' ? '确认写入当前页' : '确认为待验证问题'}</button>` : ''}</div>` : ''}
    ${applied ? `<div class="knowledge-applied"><p>已写入 <b>${esc(item.target_path || item.page_path)}</b>。写入前快照仍可用于恢复。</p><button class="btn btn-quiet btn-undo-knowledge" type="button">撤销这次更新</button></div>` : ''}
  </section>`;
}

async function renderKnowledgeUpdates() {
  const hint = document.getElementById('history-hint');
  const content = document.getElementById('history-content');
  hint.textContent = '正在读取可审核的知识库草案…';
  try {
    const data = await api('/api/study/knowledge-updates?limit=80');
    const updates = data.updates || [];
    if (!knowledgeSelectedId && updates.length) knowledgeSelectedId = updates[0].id;
    const selected = updates.find(item => Number(item.id) === Number(knowledgeSelectedId)) || updates[0];
    if (selected) knowledgeSelectedId = selected.id;
    hint.textContent = updates.length
      ? '草稿需对照原始资料再次审核；只有确认后才会写回已有学习页。'
      : '从“学习笔记”中选择“交给 Agent 整理”，即可检索本地 Wiki 并生成第一份可审核草案。';
    content.innerHTML = `<div class="knowledge-workspace">
      <aside class="knowledge-timeline" aria-label="知识库草案列表">${updates.map(knowledgeItemMarkup).join('') || '<p class="knowledge-empty">还没有草案。</p>'}</aside>
      <div class="knowledge-detail-wrap">${selected ? knowledgeDetailMarkup(selected) : '<section class="knowledge-empty-state"><h3>让一条笔记开始演进</h3><p>记录理解、疑问或联想后，Agent 会列出本地依据并生成可编辑草案。你确认后才会写入 Wiki。</p></section>'}</div>
    </div>`;
    playEntryMotion(content, 'view-enter');
  } catch (e) {
    hint.textContent = `暂时无法读取知识库草案：${e.message}`;
  }
}

async function createKnowledgeUpdate(content) {
  if (!state.selected) return;
  const hint = document.getElementById('history-hint');
  hint.textContent = '正在检索本地 Wiki，并生成可审核草案…';
  try {
    const update = await api('/api/study/knowledge-updates', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, page_path: state.selected.path, persona: recallPersona }),
    });
    knowledgeSelectedId = update.id;
    await renderKnowledgeUpdates();
  } catch (e) {
    hint.textContent = `未能生成草案：${e.message}`;
  }
}

async function reviewKnowledgeUpdate(detail) {
  const id = Number(detail.dataset.knowledgeId);
  const proposal = detail.querySelector('[id^="knowledge-proposal-"]').value.trim();
  const button = detail.querySelector('.btn-review-knowledge');
  button.disabled = true; button.textContent = '正在审核…';
  try {
    await api(`/api/study/knowledge-updates/${id}/review`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ proposal }),
    });
    await renderKnowledgeUpdates();
  } catch (e) { button.disabled = false; button.textContent = '审核草稿'; document.getElementById('history-hint').textContent = `草稿未能审核：${e.message}`; }
}

async function applyKnowledgeUpdate(detail, mode) {
  const id = Number(detail.dataset.knowledgeId);
  const proposal = detail.querySelector('[id^="knowledge-proposal-"]').value.trim();
  const button = detail.querySelector(mode === 'keep_local' ? '.btn-keep-knowledge' : '.btn-apply-knowledge');
  button.disabled = true;
  button.textContent = '正在保存快照并处理…';
  try {
    await api(`/api/study/knowledge-updates/${id}/apply`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target_mode: mode, proposal, proposed_title: '' }),
    });
    await renderKnowledgeUpdates();
    if (mode !== 'keep_local') loadConcepts();
  } catch (e) {
    button.disabled = false;
    button.textContent = mode === 'keep_local' ? '只保留在本地' : '确认写回';
    document.getElementById('history-hint').textContent = `未能处理草案：${e.message}`;
  }
}

async function undoKnowledgeUpdate(detail) {
  const id = Number(detail.dataset.knowledgeId);
  const button = detail.querySelector('.btn-undo-knowledge');
  button.disabled = true;
  button.textContent = '正在恢复快照…';
  try {
    await api(`/api/study/knowledge-updates/${id}/undo`, { method: 'POST' });
    await renderKnowledgeUpdates();
    loadConcepts();
  } catch (e) {
    button.disabled = false;
    button.textContent = '撤销这次更新';
    document.getElementById('history-hint').textContent = `无法自动撤销：${e.message}`;
  }
}

function gapStatusText(status) {
  return { open: '待补充', revised: '已补充，待核对', verified: '已澄清' }[status] || status;
}

function gapPathMarkup(gap) {
  const path = gap.learning_path || {};
  const stage = path.stage || 'practice_ready';
  const type = path.label || '待澄清点';
  const completed = gap.status === 'verified';
  let action = '';
  if (!completed && stage === 'practice_ready') {
    action = `<label class="gap-exercise-label">第 1 步 · 2 分钟微练习<textarea class="gap-revision-input" maxlength="10000" placeholder="${esc(path.practice_prompt || '用自己的话补全这个问题。')}">${esc(gap.revision || '')}</textarea></label><div class="gap-actions"><span class="gap-feedback"></span><button class="btn btn-primary btn-save-gap" type="button">完成微练习</button></div>`;
  } else if (!completed && stage === 'retest_ready') {
    action = `<label class="gap-exercise-label">第 2 步 · 换情境复测<textarea class="gap-retest-input" maxlength="10000" placeholder="${esc(path.retest_prompt || '换一个新情境，再说明你的判断。')}"></textarea></label><div class="gap-actions"><span class="gap-feedback"></span><button class="btn btn-primary btn-retest-gap" type="button">提交复测并核对</button></div>`;
  } else if (!completed && stage === 'retest_wait') {
    action = `<p class="gap-schedule">第 2 步将在 ${esc(path.retest_due || '明天')} 开放：${esc(path.retest_prompt || '')}</p>`;
  } else if (!completed && stage === 'retest_recorded') {
    action = `<p class="gap-revision"><b>异情境复测：</b>${historyEscape(gap.retest_answer || '')}</p><p class="gap-schedule">复测已保存，仍待依据原文或学习助手核对；不会把它直接当成正确答案。</p>`;
  }
  return `<article class="gap-item" data-gap-id="${gap.id}">
    <div class="history-item-head"><span>${esc(gap.page_title)}</span><small class="gap-status ${esc(gap.status)}">${gapStatusText(gap.status)}</small></div>
    <p class="gap-type"><b>${esc(type)}</b> · ${esc(path.goal || '')}</p>
    <p class="gap-original">${historyEscape(gap.content)}</p>
    ${gap.revision ? `<p class="gap-revision"><b>第 1 步记录：</b>${historyEscape(gap.revision)}</p>` : ''}
    <p class="gap-next-action">${esc(path.next_action || '')}</p>${action}
  </article>`;
}

function historyEscape(value) {
  return esc(value || '').replace(/\n/g, '<br>');
}

async function openHistory(view = 'gaps') {
  historyView = view;
  const modal = document.getElementById('history-modal');
  modal.classList.remove('hidden');
  document.querySelectorAll('.history-tab').forEach(tab => {
    const active = tab.dataset.historyView === view;
    tab.classList.toggle('active', active);
    tab.setAttribute('aria-selected', String(active));
  });
  const hint = document.getElementById('history-hint');
  const content = document.getElementById('history-content');
  hint.textContent = '正在读取学习记录…';
  content.innerHTML = '';
  try {
    if (view === 'reflections') {
      await renderReflections();
      return;
    }
    if (view === 'knowledge') {
      await renderKnowledgeUpdates();
      return;
    }
    if (view === 'sessions') {
      const data = await api('/api/study/history?limit=30');
      hint.textContent = data.sessions.length ? '每一轮讲解都会保留，可从待处理盲区继续学习。' : '还没有学习会话。完成一次回顾后，会在这里留下记录。';
      content.innerHTML = data.sessions.map(session => `
        <article class="history-item">
          <div class="history-item-head"><span>${esc(session.page_title)}</span><small>${esc(session.created_at)}</small></div>
          <p>${session.gap_total ? `识别 ${session.gap_total} 个待核对点，其中 ${session.open_gap_total} 个尚未补充。` : '本次讲解结构完整，建议下次用更短的话再复述一次。'}</p>
        </article>`).join('') || '<p class="review-empty">暂无学习历史。</p>';
      return;
    }
    const data = await api('/api/study/gaps?limit=50');
    hint.textContent = data.gaps.length ? '每个盲区都会走“2 分钟微练习 → 隔天换情境复测 → 来源核对”的小型纠错路径；第一次补充不会被当成答案。' : '目前没有待处理盲区。完成一次回顾后，识别出的盲区会在这里出现。';
    content.innerHTML = data.gaps.map(gapPathMarkup).join('') || '<p class="review-empty">暂无待处理盲区。</p>';
  } catch (e) {
    hint.textContent = `暂时无法读取学习记录：${e.message}`;
  }
}

async function saveGapRevision(item) {
  const input = item.querySelector('.gap-revision-input');
  const button = item.querySelector('.btn-save-gap');
  const feedback = item.querySelector('.gap-feedback');
  const revision = input.value.trim();
  if (revision.length < 24) {
    feedback.textContent = '请至少写 24 个字符，说明你如何补全这个问题。';
    return;
  }
  button.disabled = true;
  button.textContent = '正在保存…';
  try {
    const gap = await api(`/api/study/gaps/${item.dataset.gapId}/revision`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision }),
    });
    item.outerHTML = gapPathMarkup(gap);
  } catch (e) {
    feedback.textContent = `未能保存：${e.message}`;
    button.disabled = false;
    button.textContent = '完成微练习';
  }
}

async function saveGapRetest(item) {
  const input = item.querySelector('.gap-retest-input');
  const button = item.querySelector('.btn-retest-gap');
  const feedback = item.querySelector('.gap-feedback');
  const revision = input.value.trim();
  if (revision.length < 24) {
    feedback.textContent = '请至少写 24 个字符，说明新情境下如何判断。';
    return;
  }
  button.disabled = true;
  button.textContent = '正在依据资料核对…';
  try {
    const gap = await api(`/api/study/gaps/${item.dataset.gapId}/retest`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision }),
    });
    item.outerHTML = gapPathMarkup(gap);
  } catch (e) {
    feedback.textContent = `未能提交复测：${e.message}`;
    button.disabled = false;
    button.textContent = '提交复测并核对';
  }
}

async function submitDiagnosisFeedback(verdict) {
  const box = document.getElementById('diagnosis-feedback');
  const sessionId = box.dataset.sessionId;
  if (!sessionId) return;
  box.querySelectorAll('button').forEach(button => { button.disabled = true; });
  try {
    await api(`/api/study/sessions/${sessionId}/diagnosis-feedback`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ verdict }),
    });
    box.textContent = verdict === 'disputed'
      ? '已标记这次提示为误判。它会随学习数据导出，供后续改进规则。'
      : '已记录这次提示有帮助。';
  } catch (e) {
    box.querySelectorAll('button').forEach(button => { button.disabled = false; });
    box.prepend(document.createTextNode(`未能保存反馈：${e.message} `));
  }
}

function downloadLearningExport(payload) {
  const text = JSON.stringify(payload, null, 2);
  const blob = new Blob([text], { type: 'application/json;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `feynman-learning-${new Date().toISOString().slice(0, 10)}.json`;
  link.click();
  URL.revokeObjectURL(url);
}

async function showOrphans() {
  const hint = document.getElementById('history-hint');
  const content = document.getElementById('history-content');
  hint.textContent = '正在检查 Wiki 页面关联…';
  content.innerHTML = '';
  try {
    const data = await api('/api/study/orphans');
    hint.textContent = data.orphans.length ? '这些学习记录关联的原 Wiki 页面已不存在。选择新的页面路径后可重新关联。' : '所有学习记录仍关联到现有 Wiki 页面。';
    content.innerHTML = data.orphans.map(orphan => `
      <article class="orphan-item" data-old-path="${esc(orphan.page_path)}">
        <div class="history-item-head"><span>${esc(orphan.page_title)}</span><small>${esc(orphan.last_activity || '')}</small></div>
        <p>${esc(orphan.page_path)}</p>
        <input class="orphan-new-path" type="text" placeholder="新的 pages 相对路径，例如 AI/rag/new-name.md">
        <div class="gap-actions"><span class="gap-feedback"></span><button class="btn btn-primary btn-relink-page" type="button">重新关联</button></div>
      </article>`).join('') || '<p class="review-empty">没有失联页面。</p>';
  } catch (e) {
    hint.textContent = `无法检查失联页面：${e.message}`;
  }
}

async function relinkPage(item) {
  const newPath = item.querySelector('.orphan-new-path').value.trim();
  const feedback = item.querySelector('.gap-feedback');
  const button = item.querySelector('.btn-relink-page');
  if (!newPath) { feedback.textContent = '请输入新的 pages 相对路径。'; return; }
  button.disabled = true;
  try {
    const result = await api('/api/study/relink', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ old_path: item.dataset.oldPath, new_path: newPath }),
    });
    feedback.textContent = `已关联到：${result.new_path}`;
    button.remove();
  } catch (e) {
    feedback.textContent = `未能重新关联：${e.message}`;
    button.disabled = false;
  }
}

function renderPageMeta() {
  const meta = state.currentMeta;
  const tagHtml = (meta.tags || []).map(t => `<span class="meta-item tag-item">#${esc(t)}</span>`).join('');
  const dateHtml = [
    meta.created ? `<span class="meta-item">加入 ${esc(meta.created)}</span>` : '',
    meta.updated ? `<span class="meta-item">更新 ${esc(meta.updated)}</span>` : '',
  ].join('');
  document.getElementById('page-meta').innerHTML = `
    <span class="meta-item">${esc(meta.section || '')}</span>
    <span class="meta-item">${esc(meta.mastery?.label || '未接触')}</span>
    <span class="meta-item">${esc(meta.read_time || '')}</span>
    ${dateHtml}
    ${tagHtml}`;
}

function syncPageActions() {
  const meta = state.currentMeta;
  if (!meta) return;
  document.querySelectorAll('.act-btn').forEach(btn => {
    const field = btn.dataset.field;
    const value = btn.dataset.value;
    btn.classList.toggle('active', !btn.classList.contains('act-clear') && (meta[field] || '') === value);
    btn.setAttribute('aria-pressed', String(!btn.classList.contains('act-clear') && (meta[field] || '') === value));
  });
  const confidence = meta.mastery?.self_confidence || null;
  document.querySelectorAll('.confidence-btn').forEach(btn => {
    const active = Number(btn.dataset.confidence) === confidence;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-pressed', String(active));
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
    if (field === 'status') {
      state.selected.status = state.currentMeta[field];
      state.selected.mastery = field === 'status' && value !== 'unread'
        ? { ...(state.selected.mastery || {}), level: 'read', label: '已阅读' } : state.selected.mastery;
      renderPageMeta();
    }
    renderTree();
    syncPageActions();
  } catch (err) {
    console.error(err);
  }
}

async function saveConceptConfidence(btn) {
  if (!state.selected || !state.currentMeta) return;
  const confidence = Number(btn.dataset.confidence);
  btn.disabled = true;
  try {
    const result = await api('/api/study/confidence', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ page_path: state.selected.path, confidence }),
    });
    state.currentMeta.mastery = { ...(state.currentMeta.mastery || {}), self_confidence: result.confidence };
    const concept = state.concepts.find(item => item.path === state.selected.path);
    if (concept) concept.mastery = { ...(concept.mastery || {}), self_confidence: result.confidence };
    syncPageActions();
  } catch (e) {
    window.alert(`未能保存当前把握度：${e.message}`);
  } finally { btn.disabled = false; }
}

/* ===== 知识图谱 ===== */
const G = {
  allNodes: [], allLinks: [], nodes: [], links: [], loaded: false,
  running: false, svg: null, group: null, tip: null,
  scale: 1, tx: 0, ty: 0, drag: null, W: 0, H: 0, pinned: {},
  selectedId: null,
};

const IMP_R = { high: 13, medium: 10, low: 7 };
const STATUS_FILL = { unread: '#f3eee4', reading: '#d59a35', read: '#73855d' };
const IMPORTANCE_FILL = { high: '#c65734', medium: '#d59a35', low: '#8c9d79' };
const SECTION_FILL = ['#73855d', '#c65734', '#5978bb', '#ad6c9e', '#bd8a3d', '#557f78'];
const MASTERY_RING = { unseen: '#a29f99', read: '#bd8a3d', recalled: '#5978bb', checked: '#5b7d65', maintaining: '#365e49' };
const GRAPH_DEFAULTS = {
  sections: [], notesOnly: false, showIsolated: true,
  scope: 'neighbors',
  statuses: { unread: true, reading: true, read: true },
  colorMode: 'status', showLabels: true, labelOpacity: 1,
  nodeSize: 1, linkWidth: 1, centerForce: 0.012,
};
let graphSettings = null;

function loadSavedGraphViews() {
  try {
    const views = JSON.parse(storageGet('feynman-graph-views') || '{}');
    return views && typeof views === 'object' ? views : {};
  } catch { return {}; }
}

function saveSavedGraphViews(views) { storageSet('feynman-graph-views', JSON.stringify(views)); }

function graphViewSnapshot() {
  return {
    sections: [...graphSettings.sections], notesOnly: graphSettings.notesOnly,
    showIsolated: graphSettings.showIsolated, statuses: { ...graphSettings.statuses },
    scope: graphSettings.scope,
    colorMode: graphSettings.colorMode, showLabels: graphSettings.showLabels,
    labelOpacity: graphSettings.labelOpacity, nodeSize: graphSettings.nodeSize,
    linkWidth: graphSettings.linkWidth, centerForce: graphSettings.centerForce,
  };
}

function renderSavedGraphViews(selected = '') {
  const select = document.getElementById('graph-saved-views');
  const views = loadSavedGraphViews();
  select.innerHTML = '<option value="">选择已保存视图</option>' + Object.keys(views).sort((a, b) => a.localeCompare(b, 'zh-Hans-CN'))
    .map(name => `<option value="${esc(name)}" ${name === selected ? 'selected' : ''}>${esc(name)}</option>`).join('');
  document.getElementById('btn-delete-graph-view').classList.toggle('hidden', !selected || !views[selected]);
}

function graphSections() {
  const source = state.concepts.length ? state.concepts : G.allNodes;
  return [...new Set(source.map(c => c.section).filter(Boolean))]
    .sort((a, b) => a.localeCompare(b, 'zh-Hans-CN'));
}

function defaultGraphSettings() {
  return { ...GRAPH_DEFAULTS, sections: graphSections(), statuses: { ...GRAPH_DEFAULTS.statuses } };
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
    scope: ['neighbors', 'two_hops', 'all'].includes(saved.scope) ? saved.scope : defaults.scope,
    labelOpacity: Math.max(0.72, Number(saved.labelOpacity ?? defaults.labelOpacity)),
  };
}

function saveGraphSettings() {
  storageSet('feynman-graph-settings', JSON.stringify(graphSettings));
}

function loadGraphLayout() {
  try {
    const saved = JSON.parse(storageGet('feynman-graph-layout') || '{}');
    return saved && typeof saved === 'object' ? saved : {};
  } catch { return {}; }
}

function saveGraphLayout() {
  storageSet('feynman-graph-layout', JSON.stringify(G.pinned));
}

function pinNode(node) {
  G.pinned[node.id] = { x: Math.round(node.x), y: Math.round(node.y) };
  saveGraphLayout();
}

function clearGraphLayout() {
  G.pinned = {};
  saveGraphLayout();
  refreshGraphData(true);
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
  const pinned = G.nodes.filter(node => G.pinned[node.id]).length;
  const scope = {
    neighbors: '当前知识点与一跳关联',
    two_hops: '当前知识点与两跳关联',
    all: '全部关联（高级视图）',
  }[graphSettings.scope] || '当前知识点与一跳关联';
  document.getElementById('graph-scope-summary').textContent =
    `当前范围：${title} · ${scope} · ${G.nodes.length} 个知识点 · ${links} 条关联${pinned ? ` · 已固定 ${pinned} 个位置` : ''}。`;
}

function masteryRank(node) {
  return { unseen: 0, read: 1, recalled: 2, checked: 3, maintaining: 4 }[node.mastery?.level] ?? 0;
}

function graphReason(node) {
  if (!node) return '选择一个节点，查看它为什么值得现在处理。';
  if (node.mastery?.due_cards) return `今天有 ${node.mastery.due_cards} 张复习卡到期，应先主动回忆。`;
  if (node.mastery?.open_gaps) return `有 ${node.mastery.open_gaps} 个待澄清点，先补全它们。`;
  return {
    unseen: '尚未留下学习证据，适合从阅读与第一次回忆开始。',
    read: '已经阅读过，趁记忆仍在尝试一次回忆表达。',
    recalled: '已完成第一次回忆，尚未依据原文或明确要点核对。',
    checked: '至少一次回答已依据 Wiki 原文或必备要点核对。',
    maintaining: '已在较长间隔中多次通过来源核对，仍应继续复习。',
  }[node.mastery?.level] || '从这个概念开始建立学习证据。';
}

function graphNodeLabel(node) {
  return node.mastery?.label || '未接触';
}

function recommendedGraphNode() {
  const current = state.selected && G.nodes.find(node => node.id === state.selected.path);
  if (current) return current;
  return [...G.nodes].sort((a, b) =>
    (b.mastery?.due_cards || 0) - (a.mastery?.due_cards || 0)
    || masteryRank(a) - masteryRank(b)
    || a.title.localeCompare(b.title, 'zh-Hans-CN')
  )[0];
}

function graphFocusId(nodes, links) {
  const current = state.selected && nodes.find(node => node.id === state.selected.path);
  if (current) return current.id;
  const degree = new Map(nodes.map(node => [node.id, 0]));
  links.forEach(link => {
    degree.set(link.source, (degree.get(link.source) || 0) + 1);
    degree.set(link.target, (degree.get(link.target) || 0) + 1);
  });
  return [...nodes].sort((a, b) =>
    (b.mastery?.due_cards || 0) - (a.mastery?.due_cards || 0)
    || (b.mastery?.open_gaps || 0) - (a.mastery?.open_gaps || 0)
    || (degree.get(b.id) || 0) - (degree.get(a.id) || 0)
    || masteryRank(a) - masteryRank(b)
    || a.title.localeCompare(b.title, 'zh-Hans-CN')
  )[0]?.id || null;
}

function scopedGraph(nodes, links) {
  if (graphSettings.scope === 'all' || !nodes.length) return { nodes, links };
  const focusId = graphFocusId(nodes, links);
  if (!focusId) return { nodes, links };
  const allowed = new Set([focusId]);
  let frontier = new Set([focusId]);
  const depth = graphSettings.scope === 'two_hops' ? 2 : 1;
  for (let level = 0; level < depth; level += 1) {
    const next = new Set();
    links.forEach(link => {
      if (frontier.has(link.source) && !allowed.has(link.target)) next.add(link.target);
      if (frontier.has(link.target) && !allowed.has(link.source)) next.add(link.source);
    });
    next.forEach(id => allowed.add(id));
    frontier = next;
    if (!frontier.size) break;
  }
  return {
    nodes: nodes.filter(node => allowed.has(node.id)),
    links: links.filter(link => allowed.has(link.source) && allowed.has(link.target)),
  };
}

function syncGraphScopeButtons() {
  const mapping = [
    ['btn-graph-one-hop', 'neighbors'], ['btn-graph-two-hops', 'two_hops'], ['btn-graph-all', 'all'],
  ];
  mapping.forEach(([id, scope]) => {
    const button = document.getElementById(id);
    const active = graphSettings.scope === scope;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });
}

function setGraphScope(scope) {
  graphSettings.scope = scope;
  saveGraphSettings();
  syncGraphScopeButtons();
  refreshGraphData(true);
}

function renderGraphDecision() {
  const candidate = G.nodes.find(node => node.id === G.selectedId) || recommendedGraphNode();
  const title = document.getElementById('graph-next-title');
  const reason = document.getElementById('graph-next-reason');
  const button = document.getElementById('btn-graph-next');
  const list = document.getElementById('graph-node-list');
  if (!candidate) {
    title.textContent = '当前范围没有可用概念'; reason.textContent = '调整展示范围后再选择。'; button.disabled = true;
    list.innerHTML = ''; return;
  }
  G.selectedId = candidate.id;
  title.textContent = candidate.title;
  reason.textContent = graphReason(candidate);
  button.disabled = false;
  button.dataset.path = candidate.id;
  list.innerHTML = G.nodes.slice().sort((a, b) => masteryRank(a) - masteryRank(b) || a.title.localeCompare(b.title, 'zh-Hans-CN')).map(node => `
    <button class="graph-node-option ${node.id === candidate.id ? 'active' : ''}" type="button" data-path="${esc(node.id)}">
      <span class="graph-node-option-title">${esc(node.title)}</span><small>${esc(graphNodeLabel(node))}</small>
    </button>`).join('');
  list.querySelectorAll('.graph-node-option').forEach(option => option.addEventListener('click', () => {
    G.selectedId = option.dataset.path;
    focusGraphNode(G.selectedId);
    renderGraphDecision();
  }));
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
  document.getElementById('graph-wikilink-count').textContent = String(G.allLinks.filter(link => link.relation === 'wikilink').length);
  for (const [id, key, digits] of [
    ['graph-label-opacity', 'labelOpacity', 2], ['graph-node-size', 'nodeSize', 2],
    ['graph-link-width', 'linkWidth', 2], ['graph-center-force', 'centerForce', 3],
  ]) {
    document.getElementById(id).value = graphSettings[key];
    document.getElementById(`${id}-value`).textContent = Number(graphSettings[key]).toFixed(digits);
  }
  renderSavedGraphViews(document.getElementById('graph-saved-views')?.value || '');
  syncGraphScopeButtons();
}

async function loadGraph() {
  initGraphSettings();
  G.pinned = loadGraphLayout();
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
  ({ nodes: visible, links } = scopedGraph(visible, links));
  const radius = Math.min(G.W, G.H) * 0.35;
  G.nodes = visible.map((node, index) => {
    const old = prior.get(node.id);
    const pinned = G.pinned[node.id];
    if (pinned) return { ...node, x: pinned.x, y: pinned.y, vx: 0, vy: 0, pinned: true };
    return old && !reheat ? { ...node, x: old.x, y: old.y, vx: old.vx, vy: old.vy, pinned: false } : {
      ...node,
      x: G.W / 2 + Math.cos(index * 2.39996) * radius,
      y: G.H / 2 + Math.sin(index * 2.39996) * radius,
      vx: 0, vy: 0, pinned: false,
    };
  });
  const byId = new Map(G.nodes.map(node => [node.id, node]));
  G.links = links.map(link => ({ s: byId.get(link.source), t: byId.get(link.target) })).filter(link => link.s && link.t);
  if (reheat) { G.scale = 1; G.tx = 0; G.ty = 0; }
  buildGraphSurface();
  updateGraphScopeSummary();
  renderGraphDecision();
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

function fitGraphToView() {
  if (!G.nodes.length) return;
  const padding = 74;
  const minX = Math.min(...G.nodes.map(node => node.x));
  const maxX = Math.max(...G.nodes.map(node => node.x));
  const minY = Math.min(...G.nodes.map(node => node.y));
  const maxY = Math.max(...G.nodes.map(node => node.y));
  const graphWidth = Math.max(maxX - minX, 1);
  const graphHeight = Math.max(maxY - minY, 1);
  G.scale = Math.max(0.45, Math.min(1.35, Math.min((G.W - padding * 2) / graphWidth, (G.H - padding * 2) / graphHeight)));
  G.tx = G.W / 2 - ((minX + maxX) / 2) * G.scale;
  G.ty = G.H / 2 - ((minY + maxY) / 2) * G.scale;
  graphTransform();
}

function focusGraphNode(id) {
  const node = G.nodes.find(item => item.id === id);
  if (!node) return false;
  G.scale = Math.max(G.scale, 1.12);
  G.tx = G.W / 2 - node.x * G.scale;
  G.ty = G.H / 2 - node.y * G.scale;
  graphTransform();
  graphHover(id);
  G.selectedId = id;
  renderGraphDecision();
  window.setTimeout(graphUnhover, 1000);
  return true;
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
    group.setAttribute('role', 'button');
    group.setAttribute('tabindex', '0');
    group.setAttribute('aria-label', `${node.title}，${node.mastery?.label || '未接触'}。${graphReason(node)}`);
    group.classList.toggle('recommend', node.id === G.selectedId || (!G.selectedId && node.id === recommendedGraphNode()?.id));
    group.dataset.id = node.id;
    const radius = graphNodeRadius(node);
    const masteryRing = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    masteryRing.classList.add('graph-mastery-ring');
    masteryRing.setAttribute('r', radius + 3.5);
    masteryRing.setAttribute('fill', 'none');
    masteryRing.setAttribute('stroke', MASTERY_RING[node.mastery?.level] || MASTERY_RING.unseen);
    masteryRing.setAttribute('stroke-width', '1.5');
    masteryRing.setAttribute('stroke-dasharray', node.mastery?.level === 'maintaining' ? '0' : '3 2');
    group.appendChild(masteryRing);
    const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    circle.setAttribute('r', radius);
    circle.setAttribute('fill', nodeColor(node));
    group.appendChild(circle);
    if (node.pinned) {
      const pin = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      pin.classList.add('graph-pin');
      pin.setAttribute('r', Math.max(2.2, radius * 0.2));
      pin.setAttribute('cx', radius * 0.56);
      pin.setAttribute('cy', -radius * 0.56);
      group.appendChild(pin);
    }
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
    if (node.pinned) return;
    node.vx = Math.cos(index * 1.7) * 0.72;
    node.vy = Math.sin(index * 1.7) * 0.72;
  });
  if (!G.running) { G.running = true; requestAnimationFrame(tick); }
}

function tick() {
  if (!G.running || !G.group) return;
  const n = G.nodes.length;
  if (!n) { G.running = false; return; }
  const C_REP = 3400, C_SPR = 0.035, REST = 112, DAMP = 0.9, MAXV = 3.8;
  for (let i = 0; i < n; i++) {
    const a = G.nodes[i];
    for (let j = i + 1; j < n; j++) {
      const b = G.nodes[j];
      const dx = a.x - b.x, dy = a.y - b.y;
      const d2 = Math.max(dx * dx + dy * dy, 625);
      const d = Math.sqrt(d2), force = Math.min(C_REP / d2, 11);
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
    if (G.drag?.id === node.id || node.pinned) continue;
    node.vx += (G.W / 2 - node.x) * graphSettings.centerForce * 0.42;
    node.vy += (G.H / 2 - node.y) * graphSettings.centerForce * 0.42;
    node.vx *= DAMP; node.vy *= DAMP;
    const speed = Math.hypot(node.vx, node.vy);
    if (speed > MAXV) { node.vx *= MAXV / speed; node.vy *= MAXV / speed; }
    node.x += node.vx; node.y += node.vy;
    energy += speed;
    if (node.x < 20) { node.x = 20; node.vx *= 0.25; }
    if (node.x > G.W - 20) { node.x = G.W - 20; node.vx *= 0.25; }
    if (node.y < 20) { node.y = 20; node.vy *= 0.25; }
    if (node.y > G.H - 20) { node.y = G.H - 20; node.vy *= 0.25; }
  }
  const links = G.group.querySelectorAll('.graph-link');
  G.links.forEach((link, index) => {
    links[index].setAttribute('x1', link.s.x); links[index].setAttribute('y1', link.s.y);
    links[index].setAttribute('x2', link.t.x); links[index].setAttribute('y2', link.t.y);
  });
  const nodes = G.group.querySelectorAll('.graph-node');
  G.nodes.forEach((node, index) => nodes[index].setAttribute('transform', `translate(${node.x},${node.y})`));
  if (!G.drag && energy < n * 0.018) { G.running = false; return; }
  requestAnimationFrame(tick);
}

function graphHover(id) {
  if (!G.group) return;
  const hot = new Set([id]);
  for (const link of G.links) {
    if (link.s.id === id) hot.add(link.t.id);
    if (link.t.id === id) hot.add(link.s.id);
  }
  G.group.querySelectorAll('.graph-node').forEach(node => {
    node.classList.toggle('dim', !hot.has(node.dataset.id));
    node.classList.toggle('focus', node.dataset.id === id);
  });
  G.group.querySelectorAll('.graph-link').forEach(link => {
    const on = link.dataset.a === id || link.dataset.b === id;
    link.classList.toggle('dim', !on); link.classList.toggle('hot', on);
  });
}

function graphUnhover() {
  if (!G.group) return;
  G.group.querySelectorAll('.graph-node').forEach(node => node.classList.remove('dim', 'focus'));
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
  canvas.addEventListener('pointerdown', (e) => {
    moved = false;
    const target = e.target.closest('.graph-node');
    const rect = canvas.getBoundingClientRect();
    const mx = (e.clientX - rect.left - G.tx) / G.scale;
    const my = (e.clientY - rect.top - G.ty) / G.scale;
    if (target) {
      mode = 'node';
      const id = target.dataset.id;
      const node = G.nodes.find(x => x.id === id);
      if (!node) return;
      G.drag = { id, dx: node.x - mx, dy: node.y - my };
      if (!G.running) { G.running = true; requestAnimationFrame(tick); }
    } else {
      mode = 'pan';
      startX = e.clientX; startY = e.clientY; baseTx = G.tx; baseTy = G.ty;
      G.svg?.classList.add('dragging');
    }
    canvas.setPointerCapture?.(e.pointerId);
  });
  canvas.addEventListener('pointermove', (e) => {
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
  const finishGraphPointer = (e) => {
    if (mode === 'node' && G.drag) {
      const node = G.nodes.find(x => x.id === G.drag.id);
      if (node && moved) { node.pinned = true; pinNode(node); updateGraphScopeSummary(); renderGraph(); }
    }
    if (e?.pointerId !== undefined && canvas.hasPointerCapture?.(e.pointerId)) canvas.releasePointerCapture(e.pointerId);
    mode = null; G.drag = null;
    G.svg?.classList.remove('dragging');
  };
  canvas.addEventListener('pointerup', finishGraphPointer);
  canvas.addEventListener('pointercancel', finishGraphPointer);

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
    tip.innerHTML = `<div>${esc(n.title)}</div><div class="tip-sub">${esc(n.section)} · 阅读${st} · 掌握${esc(n.mastery?.label || '未接触')} · 重要${imp}</div>`;
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
    if (!target || moved) return;
    const id = target.dataset.id;
    const c = state.concepts.find(x => x.path === id);
    if (c) { G.selectedId = id; renderGraphDecision(); }
  });
  canvas.addEventListener('keydown', (e) => {
    const target = e.target.closest('.graph-node');
    if (!target) return;
    const id = target.dataset.id;
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault(); G.selectedId = id; renderGraphDecision();
    }
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown' || e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
      e.preventDefault();
      const index = G.nodes.findIndex(node => node.id === id);
      const offset = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : -1;
      const next = G.nodes[(index + offset + G.nodes.length) % G.nodes.length];
      G.group?.querySelector(`.graph-node[data-id="${CSS.escape(next.id)}"]`)?.focus();
      G.selectedId = next.id; renderGraphDecision();
    }
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
  document.getElementById('btn-mobile-graph')?.classList.toggle('active', showGraph);
  document.getElementById('btn-mobile-today')?.classList.toggle('active', !showGraph);
  if (showGraph) {
    playEntryMotion(document.getElementById('graph-view'), 'view-enter');
    initGraphSettings();
    renderGraphSettings();
    if (!G.loaded) loadGraph().catch(e => console.error(e));
    else refreshGraphData(false);
    setMobileGraphView('list');
  } else {
    playEntryMotion(document.getElementById('layout-main'), 'view-enter');
    G.running = false;
    if (G.svg) G.svg.style.pointerEvents = '';
  }
}

function setConceptDrawer(open) {
  const panel = document.getElementById('concept-panel');
  const backdrop = document.getElementById('concept-drawer-backdrop');
  const button = document.getElementById('btn-mobile-concept-drawer');
  panel.classList.toggle('mobile-open', open);
  backdrop.classList.toggle('hidden', !open);
  button.setAttribute('aria-expanded', String(open));
}

function setMobileGraphView(view) {
  const canvas = document.getElementById('graph-canvas');
  const guide = document.querySelector('.graph-canvas-guide');
  const list = document.querySelector('.graph-list-alternative');
  const showCanvas = view === 'canvas';
  canvas.classList.toggle('mobile-graph-hidden', !showCanvas);
  guide?.classList.toggle('mobile-graph-hidden', !showCanvas);
  list.classList.toggle('mobile-list-focus', !showCanvas);
  document.getElementById('btn-graph-mobile-list').classList.toggle('active', !showCanvas);
  document.getElementById('btn-graph-mobile-list').setAttribute('aria-selected', String(!showCanvas));
  document.getElementById('btn-graph-mobile-canvas').classList.toggle('active', showCanvas);
  document.getElementById('btn-graph-mobile-canvas').setAttribute('aria-selected', String(showCanvas));
  if (showCanvas && G.loaded) window.setTimeout(() => refreshGraphData(false), 0);
}

function escapeMultiline(text) {
  return esc(text).replace(/\n/g, '<br>');
}

let reviewMode = 'scheduled';
let reviewAgent = 'feynman';

function reviewModeCopy(mode) {
  return mode === 'cram'
    ? '从已学习内容中优先挑出到期、较少复习或即将到期的卡片。适合考前快速抽查。'
    : '仅显示今天到期的卡片。完成后，下一次日期会按你的评分调整。';
}

function renderReviewCards(cards) {
  const stack = document.getElementById('review-card-stack');
  stack.innerHTML = cards.length ? cards.map(card => `
    <article class="review-item" data-card-id="${card.id}">
      <div class="review-item-head"><small>${esc(card.page_title)} · ${card.overdue_days ? `已逾期 ${card.overdue_days} 天` : `下次 ${esc(card.due)}`}</small><span class="review-stage">${esc(card.stage)}</span></div>
      <h3>${esc(card.question)}</h3>
      <p class="review-why">为什么现在出现：${esc(card.why_today || '按复习计划安排')}，约 ${card.estimated_minutes || 1} 分钟。</p>
      <p class="review-prompt">不要先看资料摘录。写下你能重建出的机制、条件或例子。</p>
      <textarea class="review-answer-input" maxlength="5000" placeholder="先凭记忆作答，再请教练检查…"></textarea>
      <div class="review-coaches" aria-label="选择检查教练">
        <button class="btn btn-quiet review-coach-btn active" data-agent="feynman" type="button">费曼教练 · 帮我梳理</button>
        <button class="btn btn-quiet review-coach-btn" data-agent="strict" type="button">突击教练 · 直接检查</button>
      </div>
      <div class="review-feedback hidden"></div>
      ${card.can_show_reference ? `
        <section class="review-reference hidden">
          <p><b>${esc(card.reference_label || 'Wiki 原文')}</b><br>${escapeMultiline(card.reference_excerpt)}</p>
          <h4>核对时必须覆盖的要点</h4>
          <ul>${(card.required_points || []).map(point => `<li>${esc(point)}</li>`).join('')}</ul>
          <button class="text-btn review-open-source" type="button" data-source-path="${esc(card.page_path)}">回原文定位</button>
        </section>
        <button class="text-btn review-show-answer" type="button">查看 Wiki 参考与必备要点</button>` : `
        <section class="review-reference review-reference-pending hidden">
          <p><b>${esc(card.reference_label || '资料待核对')}</b><br>这张旧卡没有保存可验证的 Wiki 摘录。请回到原文重新核对；本次不会计入“已核对”或“保持中”。</p>
          <button class="text-btn review-open-source" type="button" data-source-path="${esc(card.page_path)}">回原文定位</button>
        </section>
        <button class="text-btn review-show-answer" type="button">查看原文核对说明</button>`}
      <div class="review-rating hidden">
        <button class="btn btn-quiet" data-rating="again">没记住</button>
        <button class="btn btn-quiet" data-rating="hard">很吃力</button>
        <button class="btn btn-quiet" data-rating="good">记得住</button>
        <button class="btn btn-primary" data-rating="easy">很熟</button>
      </div>
    </article>`).join('') : `<p class="review-empty">${reviewMode === 'cram' ? '还没有可抽查的学习记录。先完成一次费曼回顾。' : '今天没有到期卡。完成学习后，第一次复习会在隔天出现。'}</p>`;
}

async function openReviewPlan(mode = reviewMode) {
  reviewMode = mode;
  const modal = document.getElementById('review-modal');
  const hint = document.getElementById('review-modal-hint');
  const stack = document.getElementById('review-card-stack');
  modal.classList.remove('hidden');
  hint.textContent = '正在准备复习卡。';
  stack.innerHTML = '';
  document.querySelectorAll('.review-mode-tab').forEach(tab => {
    const active = tab.dataset.reviewMode === reviewMode;
    tab.classList.toggle('active', active);
    tab.setAttribute('aria-selected', String(active));
  });
  document.getElementById('review-mode-description').textContent = reviewModeCopy(reviewMode);
  try {
    const [data, summary] = await Promise.all([
      api(`/api/study/reviews/queue?mode=${reviewMode}`), api('/api/study/reviews/summary'),
    ]);
    document.getElementById('review-progress').textContent = `今日目标 ${summary.goal} 张，已完成 ${summary.completed} 张，待处理 ${summary.total} 张，预计 ${summary.estimated_minutes} 分钟。`;
    hint.textContent = data.total
      ? `${reviewMode === 'cram' ? '已选出' : '今天有'} ${data.total} 张卡。先作答，再选择教练核对。`
      : reviewMode === 'cram' ? '还没有可突击检查的学习记录。' : '今天没有到期卡。';
    renderReviewCards(data.cards);
  } catch (e) {
    hint.textContent = `暂时无法读取复习计划：${e.message}`;
  }
}

document.getElementById('review-card-stack').addEventListener('click', async (e) => {
  const item = e.target.closest('.review-item');
  if (!item) return;
  const sourceButton = e.target.closest('.review-open-source');
  if (sourceButton?.dataset.sourcePath) {
    document.getElementById('review-modal').classList.add('hidden');
    await selectConcept(sourceButton.dataset.sourcePath);
    document.getElementById('page-body').scrollIntoView({ block: 'start', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
    return;
  }
  const coach = e.target.closest('.review-coach-btn');
  if (coach) {
    item.querySelectorAll('.review-coach-btn').forEach(button => button.classList.toggle('active', button === coach));
    reviewAgent = coach.dataset.agent;
    const input = item.querySelector('.review-answer-input');
    if (input.value.trim().length < 24) { input.focus(); return; }
    coach.disabled = true;
    const original = coach.textContent;
    coach.textContent = '正在检查…';
    try {
      const feedback = await api(`/api/study/reviews/${item.dataset.cardId}/attempt`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent: reviewAgent, answer: input.value.trim() }),
      });
      const box = item.querySelector('.review-feedback');
      box.classList.remove('hidden');
      box.classList.toggle('strict', feedback.agent === 'strict');
      const evidenceCopy = {
        source_standard: '本次回答已对上 Wiki 必备要点，计为一次来源核对。',
        source_reviewed: '本次回答已由学习助手依据 Wiki 资料核对，计为一次来源核对。',
        unverified: '本次不计入“已核对”或“保持中”；请用当前资料继续核对。',
      }[feedback.evidence_level] || '本次核对状态暂未确定。';
      box.innerHTML = `<b>${esc(feedback.agent_name)} · ${feedback.verdict === 'pass' ? '通过' : '需要补强'}</b><p>${esc(feedback.feedback)}</p><p>${esc(evidenceCopy)}</p><p>下一问：${esc(feedback.follow_up)}</p>`;
    } catch (err) {
      item.querySelector('.review-feedback').innerHTML = `<p>${esc(err.message)}</p>`;
      item.querySelector('.review-feedback').classList.remove('hidden');
    } finally {
      coach.disabled = false;
      coach.textContent = original;
    }
    return;
  }
  if (e.target.classList.contains('review-show-answer')) {
    item.querySelector('.review-reference').classList.remove('hidden');
    item.querySelector('.review-rating').classList.remove('hidden');
    e.target.classList.add('hidden');
    return;
  }
  const rating = e.target.dataset.rating;
  if (!rating) return;
  item.querySelectorAll('button').forEach(button => { button.disabled = true; });
  try {
    const result = await api(`/api/study/reviews/${item.dataset.cardId}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rating }),
    });
    item.remove();
    const remaining = document.querySelectorAll('#review-card-stack .review-item').length;
    document.getElementById('review-modal-hint').textContent = remaining ? `还剩 ${remaining} 张卡。${result.next_review_reason}` : `本轮复习完成。${result.next_review_reason}`;
    if (!remaining) document.getElementById('review-card-stack').innerHTML = '<p class="review-empty">今日复习已完成。</p>';
    loadReviewReminder();
  } catch (err) {
    item.querySelectorAll('button').forEach(button => { button.disabled = false; });
  }
});

async function showWeeklyReport() {
  const hint = document.getElementById('history-hint');
  const content = document.getElementById('history-content');
  hint.textContent = '正在汇总本周学习证据…'; content.innerHTML = '';
  try {
    const report = await api('/api/study/weekly-report');
    const s = report.summary;
    const diagnostic = report.diagnostic || {};
    hint.textContent = report.has_evidence
      ? `${report.range.start} 至 ${report.range.end}。重点记录被修正的理解和仍需复查的盲区。`
      : `${report.range.start} 至 ${report.range.end} 还没有足够学习证据；完成一次二次表达或复习后再生成总结。`;
    content.innerHTML = `
      <section class="weekly-report">${report.has_evidence ? `<div class="weekly-summary"><span>完成二次表达 <b>${s.completed_sessions}</b></span><span>补充盲区 <b>${s.revised_gaps}</b></span><span>间隔复习 <b>${s.reviews}</b></span><span>保持中 <b>${s.maintaining_concepts}</b></span></div>` : '<p class="report-empty">没有把“0”包装成成绩。完成一次回忆表达、二次复述或间隔复习后，这里才会展示基于证据的趋势。</p>'}
      <h3>反复错的概念簇</h3>${(diagnostic.recurring_error_clusters || []).map(item => `<button class="history-item weekly-path-item" type="button" data-path="${esc(item.path)}"><div class="history-item-head"><span>${esc(item.title)}</span><small>${esc(item.label)} · ${item.times} 次</small></div><p>${esc(item.next_action)}</p></button>`).join('') || '<p class="review-empty">本周没有重复出现的错误类型。</p>'}
      <h3>从不会到会的证据</h3>${(diagnostic.progress_evidence || []).map(item => `<button class="history-item weekly-path-item" type="button" data-path="${esc(item.path)}"><div class="history-item-head"><span>${esc(item.title)}</span><small>${esc(item.level)}</small></div><p>${esc(item.evidence)}</p></button>`).join('') || '<p class="review-empty">只有出现“待澄清 → 来源核对”的记录后，这里才会展示变化；不会用活动次数替代掌握证据。</p>'}
      <h3>下周优先解决 1–3 件事</h3>${(diagnostic.next_week_priorities || []).map((item, index) => `<button class="history-item weekly-priority weekly-path-item" type="button" data-path="${esc(item.path)}"><div class="history-item-head"><span>${index + 1}. ${esc(item.title)}</span><small>约 ${item.estimated_minutes} 分钟</small></div><p><b>为什么：</b>${esc(item.why)}</p><p><b>完成后：</b>${esc(item.benefit)}</p></button>`).join('') || '<p class="review-empty">连接资料后，这里会给出下周最少且可执行的优先项。</p>'}
      <h3>仍需继续核对的理解</h3>${report.corrected_misconceptions.map(item => `<article class="history-item"><div class="history-item-head"><span>${esc(item.title)}</span><small>${item.times > 1 ? `重复 ${item.times} 次` : '待后续验证'}</small></div><p>${esc(item.gap)}</p></article>`).join('') || '<p class="review-empty">本周没有记录到待澄清点。</p>'}
      <h3>保持中</h3>${report.maintaining_concepts.map(item => `<button class="recent-note report-concept" type="button" data-path="${esc(item.path)}"><span>${esc(item.title)}</span><small>已通过多次来源核对</small></button>`).join('') || '<p class="review-empty">完成多次来源核对并拉开间隔后，这里会出现保持中的概念。</p>'}</section>`;
    content.querySelectorAll('.report-concept, .weekly-path-item').forEach(button => button.addEventListener('click', () => {
      document.getElementById('history-modal').classList.add('hidden'); selectConcept(button.dataset.path);
    }));
  } catch (e) { hint.textContent = `无法生成本周学习报告：${e.message}`; }
}

async function importLearningFile(file) {
  const hint = document.getElementById('history-hint');
  if (!file) return;
  try {
    const payload = JSON.parse(await file.text());
    const preview = await api('/api/study/import/preview', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ payload }),
    });
    const incoming = preview.incoming;
    const message = `将合并 ${incoming.sessions} 个会话、${incoming.notes} 条笔记、${incoming.reflections || 0} 条心得和 ${incoming.cards} 张复习卡。当前记录不会被删除。确认导入吗？`;
    if (!window.confirm(message)) { hint.textContent = '已取消导入。'; return; }
    const result = await api('/api/study/import', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ payload }),
    });
    hint.textContent = result.message;
    loadConcepts(); loadHomeAction();
  } catch (e) { hint.textContent = `无法导入学习数据：${e.message}`; }
}

let llmEditingProfileId = null;

function llmTestLabel(lastTest) {
  if (!lastTest?.tested_at) return '尚未测试';
  const result = lastTest.ok ? '已连通' : '连接失败';
  return `${result} · ${String(lastTest.tested_at).replace('T', ' ')}`;
}

function llmProfileMarkup(profile) {
  const testing = llmTestLabel(profile.last_test);
  return `<article class="llm-profile ${profile.active ? 'active' : ''}" role="listitem" data-llm-profile-id="${esc(profile.id)}">
    <div class="llm-profile-copy"><div><b>${esc(profile.name)}</b>${profile.active ? '<span class="llm-profile-active">正在使用</span>' : ''}</div><small>${esc(profile.model)} · ${esc(profile.base_url)}</small><p class="${profile.last_test?.ok === false ? 'failed' : ''}">${esc(testing)}</p></div>
    <div class="llm-profile-actions">${profile.active ? '<span class="llm-profile-current">已启用</span>' : '<button class="text-btn" type="button" data-llm-action="activate">一键启用</button>'}<button class="text-btn" type="button" data-llm-action="edit">编辑</button><button class="text-btn llm-danger-action" type="button" data-llm-action="delete">删除</button></div>
  </article>`;
}

function renderLlmProfiles(settings) {
  const list = document.getElementById('llm-profile-list');
  const profiles = settings.profiles || [];
  list.innerHTML = profiles.length
    ? profiles.map(llmProfileMarkup).join('')
    : `<p class="llm-profile-empty">${settings.environment_fallback_available ? '尚无网页保存的连接，当前可使用 .env 备用配置。' : '尚无已保存连接。填写上方信息后保存第一条连接。'}</p>`;
}

function renderLlmSettings(settings, message = '') {
  state.llmSettings = settings;
  const profiles = settings.profiles || [];
  if (llmEditingProfileId && !profiles.some(profile => profile.id === llmEditingProfileId)) llmEditingProfileId = null;
  if (!llmEditingProfileId) llmEditingProfileId = settings.active_profile_id || null;
  const editing = profiles.find(profile => profile.id === llmEditingProfileId);
  document.getElementById('llm-profile-name').value = editing?.name || '';
  document.getElementById('llm-base-url').value = editing?.base_url || settings.base_url || '';
  document.getElementById('llm-model').value = editing?.model || settings.model || '';
  document.getElementById('llm-api-key').value = '';
  const badge = document.getElementById('llm-config-badge');
  badge.classList.toggle('configured', Boolean(settings.configured));
  badge.classList.toggle('environment', settings.source === 'environment');
  badge.textContent = settings.source === 'local'
    ? `正在使用：${settings.active_profile_name}`
    : settings.source === 'environment' ? '.env 备用连接' : '未配置';
  const source = settings.source === 'local'
    ? `当前网页连接「${settings.active_profile_name}」已启用，密钥为 ${settings.api_key_masked}。`
    : settings.source === 'environment'
      ? '当前使用 .env 备用连接。保存任意网页连接后，它会立即优先于 .env。'
      : '尚未配置连接，仍可使用完全本地的学习流程。';
  document.getElementById('llm-config-detail').textContent = `${source} 密钥不会展示、导出或写入 Wiki。`;
  document.getElementById('btn-clear-llm-key').disabled = !settings.active_profile_id;
  renderLlmProfiles(settings);
  const status = document.getElementById('llm-settings-status');
  status.textContent = message;
  status.classList.remove('is-error', 'is-success');
}

async function loadLlmSettings() {
  const status = document.getElementById('llm-settings-status');
  status.textContent = '正在读取本机 API 设置…';
  try {
    renderLlmSettings(await api('/api/study/llm-settings'));
  } catch (e) {
    status.textContent = `暂时无法读取 API 设置：${e.message}`;
  }
}

function llmSettingsPayload() {
  return {
    api_key: document.getElementById('llm-api-key').value,
    base_url: document.getElementById('llm-base-url').value.trim(),
    model: document.getElementById('llm-model').value.trim(),
    profile_name: document.getElementById('llm-profile-name').value.trim(),
    profile_id: llmEditingProfileId,
  };
}

async function saveLlmSettings() {
  const button = document.getElementById('btn-save-llm-settings');
  const status = document.getElementById('llm-settings-status');
  button.disabled = true;
  status.classList.remove('is-error', 'is-success');
  status.textContent = '正在保存并启用这条连接…';
  try {
    const saved = await api('/api/study/llm-settings', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(llmSettingsPayload()),
    });
    llmEditingProfileId = saved.active_profile_id;
    renderLlmSettings(saved, '连接已保存并启用。它现在优先于 .env；如需深度诊断，请同时在上方选择 AI 深度诊断并保存学习空间。');
  } catch (e) {
    status.textContent = `无法保存 API 设置：${e.message}`;
  } finally {
    button.disabled = false;
  }
}

async function testLlmSettings() {
  const button = document.getElementById('btn-test-llm-settings');
  const status = document.getElementById('llm-settings-status');
  button.disabled = true;
  status.classList.remove('is-error', 'is-success');
  status.textContent = '正在向当前配置的模型发送最小连接测试…';
  try {
    const result = await api('/api/study/llm-settings/test', { method: 'POST' });
    const refreshed = await api('/api/study/llm-settings');
    renderLlmSettings(refreshed, result.message);
    status.classList.toggle('is-error', !result.ok);
    status.classList.toggle('is-success', result.ok);
  } catch (e) {
    status.textContent = `无法测试连接：${e.message}`;
    status.classList.add('is-error');
    status.classList.remove('is-success');
  } finally {
    button.disabled = false;
  }
}

function newLlmProfile() {
  llmEditingProfileId = null;
  document.getElementById('llm-profile-name').value = '';
  document.getElementById('llm-base-url').value = '';
  document.getElementById('llm-model').value = '';
  document.getElementById('llm-api-key').value = '';
  const status = document.getElementById('llm-settings-status');
  status.classList.remove('is-error', 'is-success');
  status.textContent = '正在新建连接。填写名称、服务地址、模型标识和 API Key 后保存。';
  document.getElementById('llm-profile-name').focus();
}

async function activateLlmProfile(profileId) {
  const status = document.getElementById('llm-settings-status');
  status.textContent = '正在切换学习助手连接…';
  try {
    const settings = await api(`/api/study/llm-settings/${encodeURIComponent(profileId)}/activate`, { method: 'POST' });
    llmEditingProfileId = settings.active_profile_id;
    renderLlmSettings(settings, `已切换到「${settings.active_profile_name}」。`);
    await loadWorkspace();
  } catch (e) {
    status.textContent = `无法切换连接：${e.message}`;
  }
}

function editLlmProfile(profileId) {
  llmEditingProfileId = profileId;
  const profile = state.llmSettings?.profiles?.find(item => item.id === profileId);
  if (!profile) return;
  document.getElementById('llm-profile-name').value = profile.name;
  document.getElementById('llm-base-url').value = profile.base_url;
  document.getElementById('llm-model').value = profile.model;
  document.getElementById('llm-api-key').value = '';
  document.getElementById('llm-settings-status').textContent = `正在编辑「${profile.name}」。留空 API Key 会保留已保存的密钥；保存后会启用这条连接。`;
}

async function deleteLlmProfile(profileId) {
  const profile = state.llmSettings?.profiles?.find(item => item.id === profileId);
  if (!profile || !window.confirm(`删除连接「${profile.name}」？此操作会移除当前设备保存的密钥，无法撤销。`)) return;
  const status = document.getElementById('llm-settings-status');
  status.classList.remove('is-error', 'is-success');
  status.textContent = '正在删除当前设备保存的连接…';
  try {
    const settings = await api(`/api/study/llm-settings/${encodeURIComponent(profileId)}`, { method: 'DELETE' });
    llmEditingProfileId = settings.active_profile_id;
    renderLlmSettings(settings, `已删除「${profile.name}」。`);
    await loadWorkspace();
  } catch (e) {
    status.textContent = `无法删除连接：${e.message}`;
  }
}

async function clearLocalLlmKey() {
  const current = state.llmSettings;
  if (current?.active_profile_id) deleteLlmProfile(current.active_profile_id);
}

async function openWorkspace() {
  const modal = document.getElementById('workspace-modal');
  modal.classList.remove('hidden');
  document.getElementById('workspace-status').textContent = '正在读取本地设置…';
  await loadWorkspace();
  const workspace = state.workspace;
  if (!workspace) { document.getElementById('workspace-status').textContent = '暂时无法读取本地设置。'; return; }
  document.querySelector(`input[name="workspace-mode"][value="${workspace.mode}"]`).checked = true;
  document.getElementById('workspace-path').value = workspace.mode === 'demo' ? '' : workspace.wiki_path;
  document.querySelector(`input[name="diagnostic-mode"][value="${workspace.diagnostic_mode}"]`).checked = true;
  document.getElementById('daily-review-goal').value = workspace.daily_review_goal;
  document.querySelector(`input[name="learning-goal"][value="${workspace.learning_goal || 'long_term'}"]`).checked = true;
  document.getElementById('exam-date').value = workspace.exam_date || '';
  document.getElementById('available-minutes').value = workspace.available_minutes || 25;
  renderSectionWeights(workspace.section_weights || {});
  document.getElementById('workspace-status').textContent = workspace.uses_environment_path
    ? '当前 Wiki 由启动环境指定，保存的路径会在下次未指定环境变量时生效。'
    : (workspace.configured ? '当前资料已连接。' : '尚未连接有效 Wiki，可先使用示例体验。');
  syncWorkspaceFields();
  await loadLlmSettings();
}

function renderSectionWeights(weights = {}) {
  const list = document.getElementById('section-weight-list');
  const sections = [...new Set(state.concepts.map(concept => concept.section).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'zh-Hans-CN'));
  list.innerHTML = sections.length
    ? sections.map(section => `<label class="section-weight-row"><span>${esc(section)}</span><select data-section-weight="${esc(section)}" aria-label="${esc(section)} 章节权重">${[1, 2, 3, 4, 5].map(weight => `<option value="${weight}" ${Number(weights[section] || 3) === weight ? 'selected' : ''}>${weight}</option>`).join('')}</select></label>`).join('')
    : '<p class="workspace-context-empty">连接资料后，可为每个顶层章节设置权重。</p>';
}

function selectedSectionWeights() {
  return Object.fromEntries([...document.querySelectorAll('[data-section-weight]')].map(input => [input.dataset.sectionWeight, Number(input.value)]));
}

function selectedWorkspaceMode() { return document.querySelector('input[name="workspace-mode"]:checked').value; }
function syncWorkspaceFields() {
  const local = selectedWorkspaceMode() === 'local';
  document.getElementById('workspace-path-label').classList.toggle('hidden', !local);
  document.getElementById('btn-preview-workspace').classList.toggle('hidden', !local);
  document.getElementById('btn-choose-workspace').classList.toggle('hidden', !local);
}

async function chooseWorkspaceDirectory() {
  const result = document.getElementById('workspace-preview-result');
  const button = document.getElementById('btn-choose-workspace');
  button.disabled = true;
  result.textContent = '正在打开系统文件夹选择器…';
  try {
    const picked = await api('/api/study/workspace/pick-folder', { method: 'POST' });
    if (picked.cancelled) { result.textContent = '未选择文件夹。你也可以直接输入 Wiki 根目录。'; return; }
    document.getElementById('workspace-path').value = picked.path || '';
    result.textContent = picked.message;
    if (picked.valid) previewWorkspace();
  } catch (e) {
    result.textContent = `无法打开系统选择器：${e.message}。你仍可输入路径并预览。`;
  } finally { button.disabled = false; }
}

async function previewWorkspace() {
  const result = document.getElementById('workspace-preview-result');
  const path = document.getElementById('workspace-path').value.trim();
  if (!path) { result.textContent = '先输入 Wiki 根目录。'; return; }
  result.textContent = '正在扫描…';
  try {
    const preview = await api('/api/concepts/preview?path=' + encodeURIComponent(path));
    result.textContent = `找到 ${preview.page_count} 页。示例：${preview.examples.join('、') || '暂无 Markdown 页面'}。`;
  } catch (e) { result.textContent = e.message; }
}

async function saveWorkspace() {
  const status = document.getElementById('workspace-status');
  const button = document.getElementById('btn-save-workspace');
  const mode = selectedWorkspaceMode();
  button.disabled = true; status.textContent = '正在保存本地学习空间…';
  try {
    const saved = await api('/api/study/workspace', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        mode,
        wiki_path: mode === 'local' ? document.getElementById('workspace-path').value.trim() : null,
        diagnostic_mode: document.querySelector('input[name="diagnostic-mode"]:checked').value,
        daily_review_goal: Number(document.getElementById('daily-review-goal').value),
        learning_goal: document.querySelector('input[name="learning-goal"]:checked').value,
        exam_date: document.getElementById('exam-date').value,
        available_minutes: Number(document.getElementById('available-minutes').value),
        section_weights: selectedSectionWeights(),
      }),
    });
    state.workspace = saved; status.textContent = '已保存。正在重新加载学习资料…';
    document.getElementById('workspace-modal').classList.add('hidden');
    state.selected = null; state.currentMeta = null; loadConcepts();
  } catch (e) { status.textContent = e.message; }
  finally { button.disabled = false; }
}

/* ===== 事件绑定 ===== */
document.getElementById('search-input').addEventListener('input', renderTree);
document.getElementById('library-sort').addEventListener('change', (event) => {
  state.librarySort = event.target.value;
  renderTree();
});
document.querySelectorAll('[data-library-status]').forEach(button => {
  button.addEventListener('click', () => {
    state.libraryStatus = button.dataset.libraryStatus;
    renderTree();
  });
});
document.querySelectorAll('.act-btn').forEach(btn => {
  btn.addEventListener('click', () => onActClick(btn));
});
document.querySelectorAll('.confidence-btn').forEach(btn => {
  btn.addEventListener('click', () => saveConceptConfidence(btn));
});
document.getElementById('btn-graph').addEventListener('click', toggleGraph);
document.getElementById('btn-mobile-concept-drawer').addEventListener('click', () => {
  const panel = document.getElementById('concept-panel');
  setConceptDrawer(!panel.classList.contains('mobile-open'));
});
document.getElementById('concept-drawer-backdrop').addEventListener('click', () => setConceptDrawer(false));
document.getElementById('btn-start').addEventListener('click', openRecall);
document.getElementById('btn-start-inline').addEventListener('click', openRecall);
document.getElementById('btn-open-notes').addEventListener('click', openNotes);
document.getElementById('btn-close-notes').addEventListener('click', closeNotes);
document.getElementById('btn-save-notes').addEventListener('click', saveNotes);
document.getElementById('btn-analyze-note').addEventListener('click', analyzeCurrentNote);
document.getElementById('notes-modal').addEventListener('click', (e) => {
  if (e.target.id === 'notes-modal') closeNotes();
});
document.getElementById('btn-ideas').addEventListener('click', openIdeas);
document.getElementById('btn-mobile-ideas').addEventListener('click', openIdeas);
document.getElementById('btn-close-ideas').addEventListener('click', closeIdeas);
document.getElementById('ideas-modal').addEventListener('click', (e) => {
  if (e.target.id === 'ideas-modal') closeIdeas();
  const ideaButton = e.target.closest('.idea-list-item');
  if (ideaButton) { ideaSelectedId = Number(ideaButton.dataset.ideaId); renderIdeaDetail(ideaSelectedId); }
  if (e.target.closest('#btn-new-idea')) { ideaSelectedId = null; document.getElementById('ideas-hint').textContent = '写下一个独立想法。它不需要先关联知识点，也不会自动写入 Wiki。'; renderIdeaCompose(); }
  if (e.target.closest('#btn-create-idea')) createIdea();
  if (e.target.closest('#btn-send-idea-message')) sendIdeaMessage(e.target.closest('#btn-send-idea-message'));
  if (e.target.closest('#btn-generate-idea-draft')) generateIdeaDraft(e.target.closest('#btn-generate-idea-draft'));
  if (e.target.closest('.btn-review-idea')) reviewIdeaDraft(e.target.closest('.btn-review-idea'));
  if (e.target.closest('.btn-apply-idea')) { const button = e.target.closest('.btn-apply-idea'); applyIdea(button, button.dataset.mode); }
  if (e.target.closest('.btn-keep-idea')) applyIdea(e.target.closest('.btn-keep-idea'), 'keep_local');
  if (e.target.closest('.btn-undo-idea')) undoIdea(e.target.closest('.btn-undo-idea'));
});
document.getElementById('ideas-modal').addEventListener('input', (e) => {
  if (e.target.matches('#idea-draft-content, #idea-review-target')) e.target.closest('.idea-draft-card')?.querySelector('.btn-apply-idea')?.remove();
});
document.getElementById('btn-close-recall').addEventListener('click', closeRecall);
document.getElementById('btn-recall-ready').addEventListener('click', beginRecall);
document.getElementById('btn-recall-hint').addEventListener('click', nextRecallGuide);
document.getElementById('btn-save-recall').addEventListener('click', saveRecall);
document.querySelectorAll('input[name="recall-evidence-key"]').forEach(input => input.addEventListener('change', () => {
  if (selectedRecallEvidence().length > 3) input.checked = false;
  updateRecallEvidenceStatus();
}));
document.querySelectorAll('input[name="recall-persona"]').forEach(input => input.addEventListener('change', () => {
  recallPersona = input.value;
  storageSet('feynman-recall-persona', recallPersona);
  recallGuideIndex = 0;
  loadRecallBrief();
}));
document.getElementById('btn-start-simplify').addEventListener('click', startSimplify);
document.getElementById('btn-save-simplify').addEventListener('click', saveSimplify);
document.getElementById('btn-outcome-next').addEventListener('click', openOutcomeNext);
ensureOutcomeReflectionButton();
document.getElementById('btn-outcome-close').addEventListener('click', closeRecall);
document.getElementById('recall-modal').addEventListener('click', (e) => {
  if (e.target.id === 'recall-modal') closeRecall();
});
document.getElementById('btn-toggle-reference').addEventListener('click', () => {
  const body = document.getElementById('page-body');
  const expanded = body.classList.toggle('expanded');
  document.getElementById('btn-toggle-reference').textContent = expanded ? '收起完整资料' : '查看完整资料';
});
document.getElementById('btn-reading-settings').addEventListener('click', () => {
  const panel = document.getElementById('reading-settings');
  const open = panel.classList.toggle('hidden') === false;
  document.getElementById('btn-reading-settings').setAttribute('aria-expanded', String(open));
});
document.getElementById('btn-theme-toggle').addEventListener('click', () => {
  readingSettings.theme = readingSettings.theme === 'dark' ? 'light' : 'dark';
  saveReadingSettings();
  applyReadingSettings();
});
document.getElementById('btn-mobile-theme').addEventListener('click', () => {
  readingSettings.theme = readingSettings.theme === 'dark' ? 'light' : 'dark';
  saveReadingSettings(); applyReadingSettings();
});
document.getElementById('btn-history').addEventListener('click', () => openHistory('gaps'));
document.getElementById('btn-mobile-history').addEventListener('click', () => {
  document.getElementById('mobile-menu').classList.add('hidden');
  document.getElementById('btn-mobile-menu').setAttribute('aria-expanded', 'false');
  openHistory('gaps');
});
document.getElementById('btn-weekly-report').addEventListener('click', showWeeklyReport);
document.getElementById('btn-gaps').addEventListener('click', () => openHistory('gaps'));
document.getElementById('btn-dashboard-gaps').addEventListener('click', () => openHistory('gaps'));
document.getElementById('btn-dashboard-report').addEventListener('click', showWeeklyReport);
document.getElementById('btn-dashboard-needs').addEventListener('click', () => {
  document.getElementById('search-input').focus();
  document.getElementById('search-input').scrollIntoView({ block: 'center', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
});
document.getElementById('dashboard-rail').addEventListener('click', (e) => {
  const row = e.target.closest('[data-dashboard-path]');
  if (row?.dataset.dashboardPath) selectConcept(row.dataset.dashboardPath);
});
document.getElementById('btn-close-history').addEventListener('click', () => {
  document.getElementById('history-modal').classList.add('hidden');
});
document.getElementById('history-modal').addEventListener('click', (e) => {
  if (e.target.id === 'history-modal') document.getElementById('history-modal').classList.add('hidden');
});
document.querySelectorAll('.history-tab').forEach(tab => tab.addEventListener('click', () => openHistory(tab.dataset.historyView)));
ensureReflectionTab();
ensureKnowledgeTab();
document.getElementById('history-content').addEventListener('click', (e) => {
  const button = e.target.closest('.btn-save-gap');
  if (button) saveGapRevision(button.closest('.gap-item'));
  const retestButton = e.target.closest('.btn-retest-gap');
  if (retestButton) saveGapRetest(retestButton.closest('.gap-item'));
  const relinkButton = e.target.closest('.btn-relink-page');
  if (relinkButton) relinkPage(relinkButton.closest('.orphan-item'));
  if (e.target.closest('#btn-save-reflection')) saveReflection();
  if (e.target.closest('.btn-update-reflection')) updateReflection(e.target.closest('.reflection-item'));
  if (e.target.closest('#btn-summarize-reflections')) summarizeSelectedReflections();
  const knowledgeChoice = e.target.closest('.knowledge-update-select');
  if (knowledgeChoice) { knowledgeSelectedId = Number(knowledgeChoice.dataset.knowledgeId); renderKnowledgeUpdates(); }
  if (e.target.closest('.btn-review-knowledge')) reviewKnowledgeUpdate(e.target.closest('.knowledge-detail'));
  if (e.target.closest('.btn-apply-knowledge')) { const button = e.target.closest('.btn-apply-knowledge'); applyKnowledgeUpdate(button.closest('.knowledge-detail'), button.dataset.mode); }
  if (e.target.closest('.btn-keep-knowledge')) applyKnowledgeUpdate(e.target.closest('.knowledge-detail'), 'keep_local');
  if (e.target.closest('.btn-undo-knowledge')) undoKnowledgeUpdate(e.target.closest('.knowledge-detail'));
  const evidence = e.target.closest('.btn-open-evidence');
  if (evidence) { document.getElementById('history-modal').classList.add('hidden'); selectConcept(evidence.dataset.pagePath); }
});
document.getElementById('history-content').addEventListener('change', (e) => {
  if (e.target.matches('.reflection-select-input')) syncReflectionSelection();
});
document.getElementById('history-content').addEventListener('input', (e) => {
  if (e.target.matches('[id^="knowledge-proposal-"]')) e.target.closest('.knowledge-detail')?.querySelector('.btn-apply-knowledge')?.remove();
});
document.getElementById('btn-export-data').addEventListener('click', async () => {
  const button = document.getElementById('btn-export-data');
  button.disabled = true;
  try { downloadLearningExport(await api('/api/study/export')); } catch (e) { document.getElementById('history-hint').textContent = `无法导出学习数据：${e.message}`; }
  finally { button.disabled = false; }
});
document.getElementById('btn-orphans').addEventListener('click', showOrphans);
document.getElementById('import-data-file').addEventListener('change', (e) => importLearningFile(e.target.files?.[0]));
document.getElementById('btn-reset-reading').addEventListener('click', () => {
  readingSettings = { ...defaultReadingSettings };
  saveReadingSettings();
  applyReadingSettings();
});
document.getElementById('reading-font').addEventListener('change', (e) => {
  readingSettings.font = e.target.value;
  saveReadingSettings();
  applyReadingSettings();
});
document.getElementById('reading-width').addEventListener('change', (e) => {
  readingSettings.width = e.target.value;
  saveReadingSettings();
  applyReadingSettings();
});
for (const [id, key, output, formatter] of [
  ['reading-font-size', 'fontSize', 'reading-font-size-value', value => `${value}px`],
  ['reading-line-height', 'lineHeight', 'reading-line-height-value', value => value.toFixed(2)],
]) {
  document.getElementById(id).addEventListener('input', (e) => {
    readingSettings[key] = Number(e.target.value);
    document.getElementById(output).textContent = formatter(readingSettings[key]);
    saveReadingSettings();
    applyReadingSettings();
  });
}
document.getElementById('btn-review').addEventListener('click', () => {
  openReviewPlan();
});
document.getElementById('btn-mobile-review').addEventListener('click', () => openReviewPlan());
document.getElementById('btn-workspace').addEventListener('click', openWorkspace);
document.getElementById('btn-mobile-workspace').addEventListener('click', () => {
  document.getElementById('mobile-menu').classList.add('hidden');
  document.getElementById('btn-mobile-menu').setAttribute('aria-expanded', 'false');
  openWorkspace();
});
document.getElementById('btn-mobile-today').addEventListener('click', () => switchView(false));
document.getElementById('btn-mobile-graph').addEventListener('click', toggleGraph);
document.getElementById('btn-mobile-menu').addEventListener('click', () => {
  const menu = document.getElementById('mobile-menu');
  const isOpen = menu.classList.toggle('hidden') === false;
  document.getElementById('btn-mobile-menu').setAttribute('aria-expanded', String(isOpen));
});
document.getElementById('btn-close-workspace').addEventListener('click', () => document.getElementById('workspace-modal').classList.add('hidden'));
document.getElementById('workspace-modal').addEventListener('click', (e) => {
  if (e.target.id === 'workspace-modal') document.getElementById('workspace-modal').classList.add('hidden');
});
document.querySelectorAll('input[name="workspace-mode"]').forEach(input => input.addEventListener('change', syncWorkspaceFields));
document.getElementById('btn-preview-workspace').addEventListener('click', previewWorkspace);
document.getElementById('btn-choose-workspace').addEventListener('click', chooseWorkspaceDirectory);
document.getElementById('btn-save-workspace').addEventListener('click', saveWorkspace);
document.getElementById('btn-save-llm-settings').addEventListener('click', saveLlmSettings);
document.getElementById('btn-new-llm-profile').addEventListener('click', newLlmProfile);
document.getElementById('btn-test-llm-settings').addEventListener('click', testLlmSettings);
document.getElementById('btn-clear-llm-key').addEventListener('click', clearLocalLlmKey);
document.getElementById('llm-profile-list').addEventListener('click', (e) => {
  const button = e.target.closest('[data-llm-action]');
  const profile = e.target.closest('[data-llm-profile-id]');
  if (!button || !profile) return;
  const profileId = profile.dataset.llmProfileId;
  if (button.dataset.llmAction === 'activate') activateLlmProfile(profileId);
  if (button.dataset.llmAction === 'edit') editLlmProfile(profileId);
  if (button.dataset.llmAction === 'delete') deleteLlmProfile(profileId);
});
document.querySelectorAll('.review-mode-tab').forEach(tab => tab.addEventListener('click', () => openReviewPlan(tab.dataset.reviewMode)));
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
  const settings = document.getElementById('graph-settings');
  const isOpen = settings.classList.toggle('hidden') === false;
  document.getElementById('btn-graph-settings').setAttribute('aria-expanded', String(isOpen));
});
document.getElementById('btn-reset-graph').addEventListener('click', () => {
  graphSettings = defaultGraphSettings();
  saveGraphSettings();
  renderGraphSettings();
  refreshGraphData(true);
});
document.getElementById('btn-save-graph-view').addEventListener('click', () => {
  const input = document.getElementById('graph-view-name');
  const name = input.value.trim();
  if (!name) { input.focus(); return; }
  const views = loadSavedGraphViews();
  views[name] = graphViewSnapshot();
  saveSavedGraphViews(views);
  renderSavedGraphViews(name);
  input.value = '';
});
document.getElementById('graph-saved-views').addEventListener('change', (e) => {
  const name = e.target.value;
  const view = loadSavedGraphViews()[name];
  if (!view) { renderSavedGraphViews(); return; }
  graphSettings = {
    ...graphSettings, ...view, sections: [...view.sections], statuses: { ...view.statuses },
    scope: ['neighbors', 'two_hops', 'all'].includes(view.scope) ? view.scope : GRAPH_DEFAULTS.scope,
    labelOpacity: Math.max(0.72, Number(view.labelOpacity ?? graphSettings.labelOpacity)),
  };
  saveGraphSettings(); renderGraphSettings(); refreshGraphData(true);
});
document.getElementById('btn-delete-graph-view').addEventListener('click', () => {
  const select = document.getElementById('graph-saved-views');
  const name = select.value;
  if (!name) return;
  const views = loadSavedGraphViews(); delete views[name]; saveSavedGraphViews(views); renderSavedGraphViews();
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
    graphSettings[key] = key === 'labelOpacity' ? Math.max(0.72, Number(e.target.value)) : Number(e.target.value);
    document.getElementById(`${id}-value`).textContent = graphSettings[key].toFixed(digits);
    saveGraphSettings();
    if (redraw) renderGraph();
    if (key === 'centerForce') reheatGraph();
  });
}
document.getElementById('btn-reheat-graph').addEventListener('click', reheatGraph);
document.getElementById('btn-clear-graph-layout').addEventListener('click', clearGraphLayout);
document.getElementById('btn-graph-back').addEventListener('click', () => switchView(false));
document.getElementById('btn-graph-one-hop').addEventListener('click', () => setGraphScope('neighbors'));
document.getElementById('btn-graph-two-hops').addEventListener('click', () => setGraphScope('two_hops'));
document.getElementById('btn-graph-all').addEventListener('click', () => setGraphScope('all'));
document.getElementById('btn-graph-mobile-list').addEventListener('click', () => setMobileGraphView('list'));
document.getElementById('btn-graph-mobile-canvas').addEventListener('click', () => setMobileGraphView('canvas'));
document.getElementById('btn-graph-fit').addEventListener('click', fitGraphToView);
document.getElementById('btn-graph-focus-current').addEventListener('click', () => {
  if (state.selected) {
    graphSettings.scope = 'neighbors';
    saveGraphSettings();
    syncGraphScopeButtons();
    refreshGraphData(true);
  }
  if (!state.selected || !focusGraphNode(state.selected.path)) {
    document.getElementById('graph-scope-summary').textContent = '当前学习页不在图谱范围内。请在设置中勾选它所在的知识领域。';
  }
});
document.getElementById('btn-graph-next').addEventListener('click', () => {
  const path = document.getElementById('btn-graph-next').dataset.path;
  if (!path) return;
  switchView(false); selectConcept(path);
});
document.getElementById('diagnosis-feedback').addEventListener('click', (e) => {
  const button = e.target.closest('[data-diagnosis-feedback]');
  if (button) submitDiagnosisFeedback(button.dataset.diagnosisFeedback);
});

/* ===== 启动 ===== */
loadWorkspace();
loadConcepts();
bindGraphEvents();
applyReadingSettings();
setupVoiceRecall();
