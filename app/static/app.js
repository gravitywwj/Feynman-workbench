/* 费曼学习工作台 — 前端逻辑（原生 JS，无依赖）
 * 左栏 Obsidian 式文件树 · 知识图谱视图 · 状态/重要性标注（写回 wiki frontmatter） */

const state = {
  concepts: [],
  expanded: new Set(),   // 展开的目录路径
  selected: null,        // 选中的概念对象
  currentMeta: null,     // 当前页面 meta（含 status/importance）
  searchMode: false,
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
    document.body.classList.toggle('home-mode', !state.selected);
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
      action.detail = '这是同一优先级中的另一项建议；你可以按当前目标自由选择。';
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
  } catch {
    title.textContent = '从一个知识点开始';
    detail.textContent = '选择一个概念，完成回忆表达与诊断。';
    button.disabled = true;
    alternative.classList.add('hidden');
  }
}

async function loadWorkspace() {
  try {
    state.workspace = await api('/api/study/workspace');
  } catch { state.workspace = null; }
}

async function loadRecentNotes() {
  const count = document.getElementById('recent-count');
  const hint = document.getElementById('recent-hint');
  const list = document.getElementById('recent-list');
  try {
    const data = await api('/api/concepts/recent?days=14&limit=5');
    if (data.total > 10) {
      const dates = [...new Set(data.concepts.map(concept => concept.created).filter(Boolean))];
      count.textContent = `${data.total} 条`;
      hint.textContent = `${dates[0] || '近期'} 一次导入 ${data.total} 条资料；阅读状态变化不会让旧笔记重新出现。完成学习后，此处会优先展示待补充与复习。`;
      list.innerHTML = `<p class="recent-empty">已聚合本次批量导入，避免用旧资料长期占据提醒位。</p>`;
      return;
    }
    count.textContent = data.total ? `${data.total} 条` : '暂无';
    hint.textContent = `最近 ${data.days} 天首次加入的笔记。阅读状态变化不会让旧笔记重新出现。`;
    list.innerHTML = data.concepts.map(concept => `
      <button class="recent-note" type="button" data-path="${esc(concept.path)}">
        <span>${esc(concept.title)}</span><small>${esc(concept.created)}</small>
      </button>`).join('') || '<p class="recent-empty">最近没有带加入日期的新笔记。</p>';
    list.querySelectorAll('.recent-note').forEach(button => {
      button.addEventListener('click', () => selectConcept(button.dataset.path));
    });
  } catch (e) {
    count.textContent = '—';
    hint.textContent = '暂时无法读取新收录提醒。';
    list.innerHTML = '';
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
function buildTree() {
  const root = { name: '', path: '', children: new Map(), pages: [] };
  for (const c of state.concepts) {
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
    const path = node.path;
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
  document.body.classList.toggle('home-mode', !state.selected);
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

let activeSessionId = null;
let latestOutcome = null;

function setRecallStage(stage) {
  document.getElementById('recall-intro').classList.toggle('hidden', stage !== 'intro');
  document.getElementById('recall-editor').classList.toggle('hidden', stage !== 'editor');
  document.getElementById('recall-complete').classList.toggle('hidden', stage !== 'complete');
  document.getElementById('recall-simplify').classList.toggle('hidden', stage !== 'simplify');
  document.getElementById('recall-outcome').classList.toggle('hidden', stage !== 'outcome');
}

function openRecall(stage = 'intro', sessionId = null) {
  if (!state.selected) return;
  const { path, title } = state.selected;
  recallGuideIndex = 0;
  document.getElementById('recall-topic').textContent = `本次回顾：${title}`;
  document.getElementById('recall-guide').textContent = RECALL_GUIDES[0];
  document.getElementById('recall-editor-guide').textContent = '从你最确定的一点开始，卡住是正常的；那正是下一步要核对的地方。';
  document.getElementById('recall-input').value = storageGet(recallKey(path));
  activeSessionId = sessionId;
  recallStartedAt = Date.now();
  if (stage === 'simplify') {
    document.getElementById('simplify-input').value = '';
    setRecallStage('simplify');
    setStep(4);
  } else setRecallStage('intro');
  document.getElementById('recall-modal').classList.remove('hidden');
  setStep(1);
}

function beginRecall() {
  setRecallStage('editor');
  recallStartedAt ||= Date.now();
  setStep(2);
  setTimeout(() => document.getElementById('recall-input').focus(), 0);
}

function nextRecallGuide() {
  recallGuideIndex = (recallGuideIndex + 1) % RECALL_GUIDES.length;
  document.getElementById('recall-editor-guide').textContent = RECALL_GUIDES[recallGuideIndex];
}

let speechRecognition = null;
function setupVoiceRecall() {
  const button = document.getElementById('btn-voice-recall');
  const status = document.getElementById('voice-status');
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    button.disabled = true;
    status.textContent = '当前浏览器不提供语音输入，请继续键入表达。';
    return;
  }
  speechRecognition = new SpeechRecognition();
  speechRecognition.lang = 'zh-CN';
  speechRecognition.interimResults = true;
  speechRecognition.continuous = true;
  let committed = '';
  speechRecognition.onstart = () => {
    committed = document.getElementById('recall-input').value.trim();
    button.textContent = '停止口述'; status.textContent = '正在听。停顿后会持续写入表达框。';
  };
  speechRecognition.onresult = (event) => {
    let transcript = '';
    for (let index = event.resultIndex; index < event.results.length; index += 1) transcript += event.results[index][0].transcript;
    document.getElementById('recall-input').value = [committed, transcript].filter(Boolean).join(committed ? ' ' : '');
  };
  speechRecognition.onerror = (event) => { status.textContent = `语音输入已停止：${event.error}。可继续键入。`; };
  speechRecognition.onend = () => { button.textContent = '开始口述'; if (!status.textContent.includes('停止')) status.textContent = '口述已结束，可继续编辑后生成盲区诊断。'; };
  button.addEventListener('click', () => {
    if (button.textContent === '停止口述') speechRecognition.stop();
    else speechRecognition.start();
  });
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
      body: JSON.stringify({ page_path: state.selected.path, explanation: value, elapsed_seconds: Math.round((Date.now() - (recallStartedAt || Date.now())) / 1000) }),
    });
    activeSessionId = result.session.id;
    const localStructure = result.diagnosis.confidence === 'structure_only';
    document.getElementById('diagnosis-strengths-title').textContent = localStructure ? '表达中检测到的结构证据' : '你已经讲清楚的内容';
    document.getElementById('diagnosis-gaps-title').textContent = localStructure ? '可补充的表达结构' : '需要补全的盲区';
    document.getElementById('diagnosis-strengths').innerHTML = (result.diagnosis.strengths.length ? result.diagnosis.strengths : ['已完成第一次表达，可以通过第二次表达继续校准。']).map(item => `<li>${esc(item)}</li>`).join('');
    document.getElementById('diagnosis-gaps').innerHTML = (result.gaps.length ? result.gaps : [{ content: '暂未发现结构性缺口，请用更短的话再讲一次验证记忆。', evidence: '' }]).map(gap => `<li>${esc(gap.content)}<small>${esc(gap.evidence || '')}</small></li>`).join('');
    document.getElementById('diagnosis-next-task').textContent = result.diagnosis.next_task;
    document.getElementById('diagnosis-confidence').textContent = result.diagnosis.confidence === 'reference_checked' ? '反馈已依据当前学习资料核对。' : '当前为表达结构提示：列出的是检测到的句子与可补充项，不判断事实准确性。';
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
  if (explanation.length < 24) { input.focus(); return; }
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

function closeRecall() {
  document.getElementById('recall-modal').classList.add('hidden');
}

let historyView = 'gaps';

function gapStatusText(status) {
  return { open: '待补充', revised: '已补充，待核对', verified: '已澄清' }[status] || status;
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
    hint.textContent = data.gaps.length ? '补充你的理解后，系统会保存修订；已配置学习助手时会进一步依据资料核对。' : '目前没有待处理盲区。完成一次回顾后，识别出的盲区会在这里出现。';
    content.innerHTML = data.gaps.map(gap => `
      <article class="gap-item" data-gap-id="${gap.id}">
        <div class="history-item-head"><span>${esc(gap.page_title)}</span><small class="gap-status ${esc(gap.status)}">${gapStatusText(gap.status)}</small></div>
        <p class="gap-original">${historyEscape(gap.content)}</p>
        ${gap.revision ? `<p class="gap-revision"><b>你的补充：</b>${historyEscape(gap.revision)}</p>` : ''}
        ${gap.status === 'verified' ? '' : `<textarea class="gap-revision-input" maxlength="10000" placeholder="用自己的话补全这个问题：说明机制、原因或举一个例子。">${esc(gap.revision || '')}</textarea><div class="gap-actions"><span class="gap-feedback"></span><button class="btn btn-primary btn-save-gap" type="button">保存补充并核对</button></div>`}
      </article>`).join('') || '<p class="review-empty">暂无待处理盲区。</p>';
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
    feedback.textContent = gap.feedback;
    item.querySelector('.gap-status').textContent = gapStatusText(gap.status);
    item.querySelector('.gap-status').className = `gap-status ${gap.status}`;
    if (gap.status === 'verified') {
      item.querySelector('.gap-actions').remove();
      input.remove();
    } else {
      button.textContent = '再次保存补充';
      button.disabled = false;
    }
  } catch (e) {
    feedback.textContent = `未能保存：${e.message}`;
    button.disabled = false;
    button.textContent = '保存补充并核对';
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
        ? { level: 'read', label: '已阅读' } : state.selected.mastery;
      renderPageMeta();
    }
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
  scale: 1, tx: 0, ty: 0, drag: null, W: 0, H: 0, pinned: {},
  selectedId: null,
};

const IMP_R = { high: 13, medium: 10, low: 7 };
const STATUS_FILL = { unread: '#f3eee4', reading: '#d59a35', read: '#73855d' };
const IMPORTANCE_FILL = { high: '#c65734', medium: '#d59a35', low: '#8c9d79' };
const SECTION_FILL = ['#73855d', '#c65734', '#5978bb', '#ad6c9e', '#bd8a3d', '#557f78'];
const MASTERY_RING = { unseen: '#a29f99', read: '#bd8a3d', recalled: '#5978bb', revised: '#5978bb', stable: '#516e49' };
const GRAPH_DEFAULTS = {
  sections: [], notesOnly: false, showIsolated: true,
  scope: 'neighbors',
  statuses: { unread: true, reading: true, read: true },
  colorMode: 'status', showLabels: true, labelOpacity: 0.78,
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
  return { unseen: 0, read: 1, recalled: 2, revised: 3, stable: 4 }[node.mastery?.level] ?? 0;
}

function graphReason(node) {
  if (!node) return '选择一个节点，查看它为什么值得现在处理。';
  if (node.mastery?.due_cards) return `今天有 ${node.mastery.due_cards} 张复习卡到期，应先主动回忆。`;
  if (node.mastery?.open_gaps) return `有 ${node.mastery.open_gaps} 个待澄清点，先补全它们。`;
  return {
    unseen: '尚未留下学习证据，适合从阅读与第一次回忆开始。',
    read: '已经阅读过，趁记忆仍在尝试一次回忆表达。',
    recalled: '已完成第一次回忆，下一步是补充并简化复述。',
    revised: '已完成修订，按计划复习可巩固记忆。',
    stable: '掌握较稳定，可作为关联概念的支点。',
  }[node.mastery?.level] || '从这个概念开始建立学习证据。';
}

function graphNodeLabel(node) {
  return `${node.mastery?.label || '未接触'} · ${graphReason(node)}`;
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
      <span>${esc(node.title)}</span><small>${esc(graphNodeLabel(node))}</small>
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
    masteryRing.setAttribute('stroke-dasharray', node.mastery?.level === 'stable' ? '0' : '3 2');
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
      <p class="review-prompt">不要先看答案。写下你能重建出的机制、条件或例子。</p>
      <textarea class="review-answer-input" maxlength="5000" placeholder="先凭记忆作答，再请教练检查…"></textarea>
      <div class="review-coaches" aria-label="选择检查教练">
        <button class="btn btn-quiet review-coach-btn active" data-agent="feynman" type="button">费曼教练 · 帮我梳理</button>
        <button class="btn btn-quiet review-coach-btn" data-agent="strict" type="button">突击教练 · 直接检查</button>
      </div>
      <div class="review-feedback hidden"></div>
      <p class="review-answer hidden">${escapeMultiline(card.answer)}</p>
      <button class="text-btn review-show-answer" type="button">查看参考答案</button>
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
      box.innerHTML = `<b>${esc(feedback.agent_name)} · ${feedback.verdict === 'pass' ? '通过' : '需要补强'}</b><p>${esc(feedback.feedback)}</p><p>下一问：${esc(feedback.follow_up)}</p>`;
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
    item.querySelector('.review-answer').classList.remove('hidden');
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
    hint.textContent = report.has_evidence
      ? `${report.range.start} 至 ${report.range.end}。重点记录被修正的理解和仍需复查的盲区。`
      : `${report.range.start} 至 ${report.range.end} 还没有足够学习证据；完成一次二次表达或复习后再生成总结。`;
    content.innerHTML = `
      <section class="weekly-report">${report.has_evidence ? `<div class="weekly-summary"><span>完成二次表达 <b>${s.completed_sessions}</b></span><span>补充盲区 <b>${s.revised_gaps}</b></span><span>间隔复习 <b>${s.reviews}</b></span><span>稳定掌握 <b>${s.stable_concepts}</b></span></div>` : '<p class="report-empty">没有把“0”包装成成绩。完成一次回忆表达、二次复述或间隔复习后，这里才会展示基于证据的趋势。</p>'}
      <h3>需要继续核对的理解</h3>${report.corrected_misconceptions.map(item => `<article class="history-item"><div class="history-item-head"><span>${esc(item.title)}</span><small>${item.times > 1 ? `重复 ${item.times} 次` : '待后续验证'}</small></div><p>${esc(item.gap)}</p></article>`).join('') || '<p class="review-empty">本周没有记录到待澄清点。</p>'}
      <h3>稳定掌握</h3>${report.stable_concepts.map(item => `<button class="recent-note report-concept" type="button" data-path="${esc(item.path)}"><span>${esc(item.title)}</span><small>稳定掌握</small></button>`).join('') || '<p class="review-empty">继续完成间隔复习后，这里会出现稳定掌握的概念。</p>'}</section>`;
    content.querySelectorAll('.report-concept').forEach(button => button.addEventListener('click', () => {
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
    const message = `将合并 ${incoming.sessions} 个会话、${incoming.notes} 条笔记和 ${incoming.cards} 张复习卡。当前记录不会被删除。确认导入吗？`;
    if (!window.confirm(message)) { hint.textContent = '已取消导入。'; return; }
    const result = await api('/api/study/import', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ payload }),
    });
    hint.textContent = result.message;
    loadConcepts(); loadHomeAction();
  } catch (e) { hint.textContent = `无法导入学习数据：${e.message}`; }
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
  document.getElementById('workspace-status').textContent = workspace.uses_environment_path
    ? '当前 Wiki 由启动环境指定，保存的路径会在下次未指定环境变量时生效。'
    : (workspace.configured ? '当前资料已连接。' : '尚未连接有效 Wiki，可先使用示例体验。');
  syncWorkspaceFields();
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
document.querySelectorAll('.act-btn').forEach(btn => {
  btn.addEventListener('click', () => onActClick(btn));
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
document.getElementById('notes-modal').addEventListener('click', (e) => {
  if (e.target.id === 'notes-modal') closeNotes();
});
document.getElementById('btn-close-recall').addEventListener('click', closeRecall);
document.getElementById('btn-recall-ready').addEventListener('click', beginRecall);
document.getElementById('btn-recall-hint').addEventListener('click', nextRecallGuide);
document.getElementById('btn-save-recall').addEventListener('click', saveRecall);
document.getElementById('btn-start-simplify').addEventListener('click', startSimplify);
document.getElementById('btn-save-simplify').addEventListener('click', saveSimplify);
document.getElementById('btn-outcome-next').addEventListener('click', openOutcomeNext);
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
document.getElementById('btn-close-history').addEventListener('click', () => {
  document.getElementById('history-modal').classList.add('hidden');
});
document.getElementById('history-modal').addEventListener('click', (e) => {
  if (e.target.id === 'history-modal') document.getElementById('history-modal').classList.add('hidden');
});
document.querySelectorAll('.history-tab').forEach(tab => tab.addEventListener('click', () => openHistory(tab.dataset.historyView)));
document.getElementById('history-content').addEventListener('click', (e) => {
  const button = e.target.closest('.btn-save-gap');
  if (button) saveGapRevision(button.closest('.gap-item'));
  const relinkButton = e.target.closest('.btn-relink-page');
  if (relinkButton) relinkPage(relinkButton.closest('.orphan-item'));
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
    graphSettings[key] = Number(e.target.value);
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
