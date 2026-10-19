/* AI Engineering Learning Lab: local course reading and learner-owned drafts. */
(() => {
  const el = (id) => document.getElementById(id);
  const state = {
    overview: null,
    detail: null,
    missionKey: null,
    dirty: false,
    tab: 'task',
    returnTo: null,
  };

  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      const error = new Error(payload.detail || `请求失败 ${response.status}`);
      error.status = response.status;
      throw error;
    }
    return response.json();
  }

  function statusLabel(status) {
    return ({
      not_started: '未开始',
      in_progress: '进行中',
      completed: '任务完成',
      needs_review: '需复查',
    })[status] || '未开始';
  }

  function setTab(name) {
    state.tab = name;
    document.querySelectorAll('[data-lab-tab]').forEach((button) => {
      const active = button.dataset.labTab === name;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', String(active));
    });
    document.querySelectorAll('[data-lab-panel]').forEach((panel) => {
      panel.classList.toggle('hidden', panel.dataset.labPanel !== name);
    });
  }

  function renderOverview(data) {
    state.overview = data;
    const course = data.course;
    el('lab-course-title').textContent = course.project_title;
    el('lab-course-summary').textContent = `来源固定到 ${course.course_version.slice(0, 12)} · ${course.license}`;
    el('lab-level').textContent = data.level;
    el('lab-xp').textContent = data.xp;
    el('lab-level-progress-label').textContent = `本级 ${data.level_xp} / ${data.xp_per_level} XP`;
    el('lab-level-progress').value = data.level_xp;
    el('lab-level-progress').max = data.xp_per_level;
    el('lab-mission-progress').textContent = `${data.progress_count} / ${data.progress_total}`;
    el('lab-imported-xp').textContent = data.imported_xp ? `含导入记录 ${data.imported_xp} XP` : '';
    el('lab-runner-reason').textContent = data.runner.reason;
    el('lab-run-disabled-note').textContent = data.runner.reason;
    el('lab-source-link').href = course.source_url;
    el('lab-source-status').textContent = course.source_status.available
      ? `可读 · commit ${course.source_status.commit}`
      : course.source_status.reason;

    const map = el('lab-mission-map');
    map.replaceChildren();
    data.missions.forEach((mission, index) => {
      const item = document.createElement('li');
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'lab-map-item';
      button.dataset.missionKey = mission.mission_key;
      button.setAttribute('aria-current', mission.mission_key === state.missionKey ? 'step' : 'false');
      const number = document.createElement('span');
      number.className = 'lab-map-number';
      number.textContent = mission.kind === 'boss' ? 'B' : String(index + 1).padStart(2, '0');
      const copy = document.createElement('span');
      copy.className = 'lab-map-copy';
      const title = document.createElement('strong');
      title.textContent = mission.title;
      const status = document.createElement('small');
      status.textContent = statusLabel(mission.status);
      copy.append(title, status);
      button.append(number, copy);
      item.append(button);
      map.append(item);
    });
  }

  function renderDocuments(detail) {
    const container = el('lab-source-documents');
    container.replaceChildren();
    if (!detail.source_status.available || !detail.documents.length) {
      const note = document.createElement('p');
      note.className = 'lab-source-error';
      note.textContent = detail.source_status.reason || '固定课程资料当前不可用。';
      container.append(note);
      return;
    }
    detail.documents.forEach((documentInfo) => {
      const details = document.createElement('details');
      details.className = 'lab-source-document';
      const summary = document.createElement('summary');
      summary.textContent = documentInfo.path;
      const pre = document.createElement('pre');
      pre.textContent = documentInfo.content;
      details.append(summary, pre);
      container.append(details);
    });
  }

  function renderMission(detail) {
    state.detail = detail;
    state.missionKey = detail.mission.mission_key;
    state.dirty = false;
    el('lab-mission-type').textContent = detail.mission.kind === 'boss' ? 'BOSS PROJECT' : 'MISSION';
    el('lab-mission-title').textContent = detail.mission.title;
    el('lab-mission-status').textContent = statusLabel(detail.mission.status);
    el('lab-mission-status').dataset.status = detail.mission.status;
    el('lab-mission-adaptation').textContent = detail.adaptation;
    el('lab-question').textContent = detail.question;
    el('lab-prediction-prompt').textContent = detail.prediction_prompt;
    el('lab-acceptance').textContent = detail.acceptance;
    el('lab-prediction').value = detail.progress?.prediction || '';
    el('lab-observation').value = detail.progress?.observation || '';
    el('lab-explanation').value = detail.progress?.explanation || '';
    el('lab-code').value = detail.initial_code || '';
    el('lab-help-level').value = String(detail.progress?.help_level ?? 0);
    el('lab-save-status').textContent = detail.progress?.updated_at
      ? `上次保存 ${detail.progress.updated_at}`
      : '草稿尚未保存。';
    el('lab-conflict').classList.add('hidden');
    renderDocuments(detail);
    renderOverview(state.overview);
    setTab(state.tab);
  }

  function readDraft() {
    return {
      prediction: el('lab-prediction').value,
      observation: el('lab-observation').value,
      explanation: el('lab-explanation').value,
      code_text: el('lab-code').value,
      help_level: Number(el('lab-help-level').value),
      expected_updated_at: state.detail.progress?.updated_at || null,
    };
  }

  async function loadOverview() {
    const data = await api('/api/learning-lab');
    renderOverview(data);
    return data;
  }

  async function loadMission(missionKey) {
    const detail = await api(`/api/learning-lab/missions/${encodeURIComponent(missionKey)}`);
    renderMission(detail);
  }

  async function saveDraft() {
    if (!state.detail) return true;
    el('lab-save-status').textContent = '正在保存…';
    const submitted = readDraft();
    try {
      const saved = await api(`/api/learning-lab/missions/${encodeURIComponent(state.missionKey)}/draft`, {
        method: 'PUT',
        body: JSON.stringify(submitted),
      });
      state.detail.progress = saved;
      state.detail.mission.status = saved.status;
      const fields = ['prediction', 'observation', 'explanation', 'code_text', 'help_level'];
      const current = readDraft();
      state.dirty = fields.some((field) => current[field] !== submitted[field]);
      el('lab-mission-status').textContent = statusLabel(saved.status);
      el('lab-mission-status').dataset.status = saved.status;
      el('lab-save-status').textContent = state.dirty
        ? `已保存 ${saved.updated_at}；保存后又有新修改。`
        : `已保存 ${saved.updated_at} · 任务仍需实际验收。`;
      el('lab-conflict').classList.add('hidden');
      await loadOverview();
      return !state.dirty;
    } catch (error) {
      if (error.status === 409) el('lab-conflict').classList.remove('hidden');
      el('lab-save-status').textContent = `未保存：${error.message}`;
      return false;
    }
  }

  async function selectMission(missionKey) {
    if (missionKey === state.missionKey) return;
    if (state.dirty && !(await saveDraft())) return;
    el('lab-mission-title').textContent = '正在读取任务…';
    try {
      await loadMission(missionKey);
    } catch (error) {
      el('lab-mission-title').textContent = '任务资料暂不可用';
      el('lab-save-status').textContent = error.message;
    }
  }

  function setLabVisible(visible) {
    const lab = el('learning-lab-view');
    const layout = el('layout-main');
    const graph = el('graph-view');
    const mobileNav = document.querySelector('.mobile-bottom-nav');
    if (visible) {
      if (!state.returnTo) {
        state.returnTo = {
          layoutHidden: layout.classList.contains('hidden'),
          graphHidden: graph.classList.contains('hidden'),
          mobileNavHidden: mobileNav.classList.contains('hidden'),
        };
      }
      layout.classList.add('hidden');
      graph.classList.add('hidden');
      mobileNav.classList.add('hidden');
      lab.classList.remove('hidden');
      document.body.classList.add('learning-lab-open');
      return;
    }
    lab.classList.add('hidden');
    document.body.classList.remove('learning-lab-open');
    if (state.returnTo) {
      layout.classList.toggle('hidden', state.returnTo.layoutHidden);
      graph.classList.toggle('hidden', state.returnTo.graphHidden);
      mobileNav.classList.toggle('hidden', state.returnTo.mobileNavHidden);
    }
    state.returnTo = null;
  }

  async function openLab() {
    document.getElementById('mobile-menu')?.classList.add('hidden');
    document.getElementById('btn-mobile-menu')?.setAttribute('aria-expanded', 'false');
    setLabVisible(true);
    try {
      const data = await loadOverview();
      const current = data.missions.find((mission) => mission.status === 'in_progress') || data.missions[0];
      if (!state.detail || !data.missions.some((mission) => mission.mission_key === state.missionKey)) {
        await loadMission(current.mission_key);
      }
    } catch (error) {
      el('lab-source-status').textContent = error.message;
      el('lab-mission-title').textContent = '课程暂不可用';
    }
  }

  async function closeLab() {
    if (state.dirty && !(await saveDraft())) return;
    setLabVisible(false);
  }

  function downloadDraftCopy() {
    const data = {
      course_id: state.overview?.course.course_id,
      course_version: state.overview?.course.course_version,
      mission_key: state.missionKey,
      saved_at: new Date().toISOString(),
      ...readDraft(),
    };
    delete data.expected_updated_at;
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `${state.missionKey || 'learning-lab'}-draft.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  async function reloadSavedDraft() {
    if (!state.missionKey) return;
    try {
      await loadMission(state.missionKey);
      el('lab-save-status').textContent = '已载入当前保存版本。';
    } catch (error) {
      el('lab-save-status').textContent = `无法载入：${error.message}`;
    }
  }

  document.querySelectorAll('[data-lab-tab]').forEach((button) => {
    button.addEventListener('click', () => setTab(button.dataset.labTab));
  });
  el('btn-learning-lab')?.addEventListener('click', openLab);
  el('btn-mobile-learning-lab')?.addEventListener('click', openLab);
  el('btn-learning-lab-close')?.addEventListener('click', closeLab);
  el('btn-lab-save')?.addEventListener('click', saveDraft);
  el('lab-mission-map')?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-mission-key]');
    if (button) selectMission(button.dataset.missionKey);
  });
  ['lab-prediction', 'lab-observation', 'lab-explanation', 'lab-code', 'lab-help-level'].forEach((id) => {
    el(id)?.addEventListener('input', () => {
      state.dirty = true;
      el('lab-save-status').textContent = '有未保存更改。';
    });
    el(id)?.addEventListener('change', () => {
      state.dirty = true;
      el('lab-save-status').textContent = '有未保存更改。';
    });
  });
  el('btn-lab-conflict-download')?.addEventListener('click', downloadDraftCopy);
  el('btn-lab-conflict-reload')?.addEventListener('click', reloadSavedDraft);
  el('lab-code')?.addEventListener('keydown', (event) => {
    const editor = event.currentTarget;
    if (event.key === 'Tab') {
      event.preventDefault();
      const start = editor.selectionStart;
      const end = editor.selectionEnd;
      const value = editor.value;
      if (event.shiftKey && start === end) {
        const lineStart = value.lastIndexOf('\n', start - 1) + 1;
        const indent = value.slice(lineStart, start).match(/^ {1,4}/)?.[0] || '';
        if (!indent) return;
        editor.setRangeText('', lineStart, lineStart + indent.length, 'end');
      } else if (event.shiftKey) {
        const lineStart = value.lastIndexOf('\n', start - 1) + 1;
        const indent = value.slice(lineStart, start).match(/^ {1,4}/)?.[0] || '';
        if (!indent) return;
        editor.setRangeText('', lineStart, lineStart + indent.length, 'select');
      } else {
        editor.setRangeText('    ', start, end, 'end');
      }
      editor.dispatchEvent(new Event('input', { bubbles: true }));
      return;
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
      event.preventDefault();
      saveDraft();
    }
  });
})();
