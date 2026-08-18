/* Budsmet — інтерфейс складання кошторисів. Без збірки: чистий ES2020. */
'use strict';

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = {
  meta: null,
  objects: [],
  currentId: null,
  calc: null,
  preview: null,
  previewFile: null,
  acItems: [],
  acIndex: -1,
};

const money = (v) => (Number(v) || 0).toLocaleString('uk-UA', {
  minimumFractionDigits: 2, maximumFractionDigits: 2,
});
const qty = (v) => {
  const n = Number(v) || 0;
  return n.toLocaleString('uk-UA', { maximumFractionDigits: 4 });
};
const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

let toastTimer;
function toast(message, kind = '') {
  const el = $('#toast');
  el.textContent = message;
  el.className = `toast ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add('hidden'), 4200);
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = `Помилка ${response.status}`;
    try { detail = (await response.json()).detail || detail; } catch (_) { /* not json */ }
    throw new Error(detail);
  }
  return response.status === 204 ? null : response.json();
}

const json = (method, body) => ({
  method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});

/* ------------------------------------------------------------------ навігація */

function showView(name) {
  $$('.view').forEach((v) => v.classList.toggle('active', v.id === `view-${name}`));
  $$('.nav-btn').forEach((b) => b.classList.toggle('active', b.dataset.view === name));
  if (name === 'catalog') loadCatalog();
  if (name === 'history') loadHistory();
}

/* -------------------------------------------------------------------- стартові дані */

async function loadMeta() {
  state.meta = await api('/api/meta');
  const { regions, cities, units, strategies, web_provider: provider } = state.meta;

  $('#regions-list').innerHTML = regions.map((r) => `<option value="${esc(r)}">`).join('');
  $('#cities-list').innerHTML = cities.map((c) => `<option value="${esc(c)}">`).join('');
  $('#units-list').innerHTML = units.map((u) => `<option value="${esc(u)}">`).join('');

  const options = strategies.map((s) => `<option value="${s.key}">${esc(s.label)}</option>`).join('');
  $('#set-strategy').innerHTML = options;
  $('#imp-strategy').innerHTML = options;

  const badge = $('#provider-badge');
  badge.classList.toggle('on', provider.enabled);
  badge.textContent = provider.enabled
    ? `Інтернет-ціни: ${provider.provider}`
    : `Інтернет-ціни вимкнено · ${state.meta.catalog_size} розцінок у довіднику`;
  badge.title = provider.enabled
    ? 'Ціни можуть уточнюватись пошуком в інтернеті по місту об\'єкта'
    : provider.reason + '. Використовуються історія та довідник.';
}

async function loadObjects(selectId) {
  const { items } = await api('/api/objects');
  state.objects = items;
  $('#object-list').innerHTML = items.map((o) => `
    <li data-id="${o.id}" class="${o.id === state.currentId ? 'active' : ''}">
      <span class="oname">${esc(o.name)}</span>
      <span class="osub">${esc(o.city || o.region || '—')} · ${o.positions_count} поз. · ${money(o.totals)} грн</span>
    </li>`).join('') || '<li class="muted" style="cursor:default">Ще немає об\'єктів</li>';

  $$('#object-list li[data-id]').forEach((li) => {
    li.onclick = () => openObject(Number(li.dataset.id));
  });
  if (selectId) openObject(selectId);
  else if (!items.length) {
    $('#object-view').classList.add('hidden');
    $('#object-empty').classList.remove('hidden');
  }
}

/* ------------------------------------------------------------------ об'єкт */

async function openObject(id) {
  state.currentId = id;
  showView('objects');
  state.calc = await api(`/api/objects/${id}`);
  renderObject();
  $$('#object-list li').forEach((li) => li.classList.toggle('active', Number(li.dataset.id) === id));
}

function renderObject() {
  const calc = state.calc;
  const obj = calc.object;
  $('#object-empty').classList.add('hidden');
  $('#object-view').classList.remove('hidden');

  $('#obj-name').textContent = obj.name;
  $('#obj-address').value = obj.address || '';
  $('#obj-city').value = obj.city || '';
  $('#obj-region').value = obj.region || '';
  $('#obj-customer').value = obj.customer || '';
  $('#obj-doccode').value = obj.doc_code || '';

  const s = calc.settings;
  $('#set-strategy').value = s.price_strategy || 'history_first';
  $('#set-overhead').value = s.overhead_pct;
  $('#set-profit').value = s.profit_pct;
  $('#set-admin').value = s.admin_pct;
  $('#set-risk').value = s.risk_pct;
  $('#set-vat').value = s.vat_pct;
  $('#set-materials').checked = !!s.materials_included;

  const base = `/api/objects/${obj.id}/export`;
  $('#exp-xlsx').href = `${base}/xlsx`;
  $('#exp-pdf').href = `${base}/pdf`;
  $('#exp-html').href = `${base}/html`;

  renderDivisionSelect();
  renderTree();
  renderTotals();
}

function renderDivisionSelect() {
  const options = [];
  state.calc.estimates.forEach((est) => {
    est.divisions.forEach((div) => {
      const label = `${est.code || est.title || 'Кошторис'} › ${div.code ? `Розділ ${div.code}. ` : ''}${div.title || 'Роботи'}`;
      options.push(`<option value="${div.id}">${esc(label)}</option>`);
    });
  });
  $('#add-division').innerHTML = options.join('') || '<option value="">новий розділ</option>';
}

const SRC_CLASS = {
  catalog: 'src-catalog', history: 'src-history', web: 'src-web',
  manual: 'src-manual', none: 'src-none', '': 'src-none',
};
const SRC_LABEL = {
  catalog: 'довідник', history: 'історія', web: 'інтернет',
  manual: 'вручну', none: 'не знайдено', '': 'не знайдено',
};

function renderTree() {
  const calc = state.calc;
  if (!calc.estimates.length) {
    $('#estimate-tree').innerHTML =
      '<div class="empty" style="margin:40px auto"><p class="muted">Ще немає жодної позиції. '
      + 'Додайте роботу через рядок вище.</p></div>';
    return;
  }

  const rows = [];
  rows.push(`<thead><tr>
    <th style="width:44px">№</th><th>Найменування робіт та витрат</th>
    <th style="width:70px">Од.</th><th style="width:96px">Кількість</th>
    <th class="money" style="width:104px">Ціна робіт</th>
    <th class="money" style="width:104px">Матеріали</th>
    <th class="money" style="width:96px">Машини</th>
    <th class="money" style="width:116px">Ціна/од.</th>
    <th class="money" style="width:132px">Вартість</th>
    <th style="width:104px">Джерело</th><th style="width:34px"></th>
  </tr></thead><tbody>`);

  calc.estimates.forEach((est) => {
    const title = [est.code, est.title].filter(Boolean).join(' · ') || 'Локальний кошторис';
    rows.push(`<tr class="band band-est"><td colspan="8">Локальний кошторис ${esc(title)}</td>
      <td class="money">${money(est.totals.total)}</td><td colspan="2"></td></tr>`);

    est.divisions.forEach((div) => {
      const head = div.code ? `Розділ ${div.code}. ${div.title || ''}` : (div.title || 'Роботи');
      rows.push(`<tr class="band"><td colspan="8">${esc(head)}</td>
        <td class="money">${money(div.totals.total)}</td><td colspan="2"></td></tr>`);

      div.positions.forEach((p) => {
        const src = p.price_source || '';
        const score = p.match_score && src === 'catalog' && p.match_score < 90
          ? `<div class="score" title="Схожість із розцінкою довідника">збіг ${p.match_score}%</div>` : '';
        rows.push(`<tr data-pos="${p.id}">
          <td class="ctr">${p.number}</td>
          <td>${esc(p.name)}${score}</td>
          <td class="ctr">${esc(p.unit)}</td>
          <td><input class="cellinput" data-field="quantity" value="${qty(p.quantity)}"></td>
          <td><input class="cellinput" data-field="labor_price" value="${money(p.labor_price)}"></td>
          <td><input class="cellinput" data-field="material_price" value="${money(p.material_price)}"></td>
          <td><input class="cellinput" data-field="machines_price" value="${money(p.machines_price)}"></td>
          <td class="money">${money(p.totals.unit_price)}</td>
          <td class="money"><b>${money(p.totals.total)}</b></td>
          <td class="ctr"><span class="src ${SRC_CLASS[src]}">${SRC_LABEL[src]}</span></td>
          <td class="ctr"><button class="rowdel" title="Видалити позицію">×</button></td>
        </tr>`);
      });

      if (div.positions.length) {
        rows.push(`<tr class="band-tot"><td colspan="8">Разом по розділу</td>
          <td class="money">${money(div.totals.total)}</td><td colspan="2"></td></tr>`);
      }
    });
  });
  rows.push('</tbody>');
  $('#estimate-tree').innerHTML = `<table class="grid">${rows.join('')}</table>`;
  bindTreeEvents();
}

function bindTreeEvents() {
  $$('#estimate-tree tr[data-pos]').forEach((tr) => {
    const id = Number(tr.dataset.pos);
    $$('.cellinput', tr).forEach((input) => {
      input.onkeydown = (e) => { if (e.key === 'Enter') input.blur(); };
      input.onblur = async () => {
        const raw = input.value.replace(/\s| /g, '').replace(',', '.');
        const value = Number(raw);
        if (Number.isNaN(value)) { toast('Некоректне число', 'err'); return; }
        try {
          await api(`/api/positions/${id}`, json('PATCH', { [input.dataset.field]: value }));
          await refreshCalc();
        } catch (err) { toast(err.message, 'err'); }
      };
    });
    $('.rowdel', tr).onclick = async () => {
      try {
        await api(`/api/positions/${id}`, { method: 'DELETE' });
        await refreshCalc();
        loadObjects();
      } catch (err) { toast(err.message, 'err'); }
    };
  });
}

function renderTotals() {
  $('#totals').innerHTML = state.calc.lines.map((l) => `
    <tr class="${l.sub ? 'sub' : ''} ${l.key === 'total' ? 'grand' : ''}">
      <td>${esc(l.label)}</td><td>${money(l.value)} грн</td></tr>`).join('');
}

async function refreshCalc() {
  state.calc = await api(`/api/objects/${state.currentId}`);
  renderTree();
  renderTotals();
  renderDivisionSelect();
}

/* ------------------------------------------------- автопідказка найменувань */

let acTimer;
function setupAutocomplete() {
  const input = $('#add-name');
  const list = $('#ac-list');

  const close = () => { list.classList.add('hidden'); state.acIndex = -1; };

  input.addEventListener('input', () => {
    clearTimeout(acTimer);
    const q = input.value.trim();
    if (q.length < 2) { close(); return; }
    acTimer = setTimeout(async () => {
      try {
        const { items } = await api(`/api/catalog?q=${encodeURIComponent(q)}&limit=12`);
        state.acItems = items;
        if (!items.length) { close(); return; }
        list.innerHTML = items.map((it, i) => `
          <div class="ac-item ${i === 0 ? 'sel' : ''}" data-i="${i}">
            <span class="an">${esc(it.name)}</span>
            <span class="am"><b>${esc(it.unit)}</b>
              <span>роботи ${money(it.labor)} грн</span>
              <span>матеріали ${money(it.material)} грн</span>
              <span>${esc(it.category || '')}</span></span>
          </div>`).join('');
        state.acIndex = 0;
        list.classList.remove('hidden');
        $$('.ac-item', list).forEach((el) => {
          el.onclick = () => pickAc(Number(el.dataset.i));
        });
      } catch (_) { close(); }
    }, 180);
  });

  input.addEventListener('keydown', (e) => {
    if (list.classList.contains('hidden')) return;
    const items = $$('.ac-item', list);
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      state.acIndex = (state.acIndex + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
      items.forEach((el, i) => el.classList.toggle('sel', i === state.acIndex));
      items[state.acIndex].scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'Enter' && state.acIndex >= 0) {
      e.preventDefault();
      pickAc(state.acIndex);
    } else if (e.key === 'Escape') close();
  });

  document.addEventListener('click', (e) => {
    if (!e.target.closest('.ac-wrap')) close();
  });

  function pickAc(index) {
    const item = state.acItems[index];
    if (!item) return;
    input.value = item.name;
    $('#add-unit').value = item.unit || '';
    close();
    $('#add-qty').focus();
  }
}

async function addPosition() {
  const name = $('#add-name').value.trim();
  const quantity = Number($('#add-qty').value.replace(',', '.'));
  if (!name) { toast('Вкажіть найменування роботи', 'err'); return; }
  if (!quantity) { toast('Вкажіть обсяг', 'err'); return; }
  const divisionId = $('#add-division').value;
  try {
    await api(`/api/objects/${state.currentId}/positions`, json('POST', {
      name, unit: $('#add-unit').value.trim(), quantity,
      division_id: divisionId ? Number(divisionId) : null,
    }));
    $('#add-name').value = ''; $('#add-unit').value = ''; $('#add-qty').value = '';
    $('#add-name').focus();
    await refreshCalc();
    loadObjects();
  } catch (err) { toast(err.message, 'err'); }
}

/* ------------------------------------------------------- налаштування об'єкта */

async function saveObjectFields() {
  if (!state.currentId) return;
  const payload = {
    name: $('#obj-name').textContent.trim(),
    address: $('#obj-address').value.trim(),
    city: $('#obj-city').value.trim(),
    region: $('#obj-region').value.trim(),
    customer: $('#obj-customer').value.trim(),
    doc_code: $('#obj-doccode').value.trim(),
    settings: {
      price_strategy: $('#set-strategy').value,
      overhead_pct: Number($('#set-overhead').value),
      profit_pct: Number($('#set-profit').value),
      admin_pct: Number($('#set-admin').value),
      risk_pct: Number($('#set-risk').value),
      vat_pct: Number($('#set-vat').value),
      materials_included: $('#set-materials').checked,
    },
  };
  try {
    await api(`/api/objects/${state.currentId}`, json('PATCH', payload));
    await refreshCalc();
    loadObjects();
  } catch (err) { toast(err.message, 'err'); }
}

async function repriceObject() {
  const button = $('#btn-reprice');
  const original = button.textContent;
  button.disabled = true;
  button.innerHTML = '<span class="spinner"></span>Рахую…';
  try {
    const result = await api(`/api/objects/${state.currentId}/reprice`,
      json('POST', { strategy: $('#set-strategy').value, keep_manual: true }));
    state.calc = result.calc;
    renderTree(); renderTotals();
    const s = result.stats;
    const bySource = Object.entries(s.by_source).map(([k, v]) => `${k}: ${v}`).join(', ');
    $('#reprice-stats').textContent =
      `Оцінено ${s.priced} з ${s.total}${s.skipped_manual ? `, вручну ${s.skipped_manual}` : ''}`
      + `${s.not_found ? `, без ціни ${s.not_found}` : ''}. ${bySource}`;
    toast('Ціни перераховано', 'ok');
    loadObjects();
  } catch (err) { toast(err.message, 'err'); } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

/* ------------------------------------------------------------------- імпорт */

function setupImport() {
  const zone = $('#dropzone');
  const input = $('#file-input');

  zone.onclick = (e) => { if (e.target.tagName !== 'BUTTON') input.click(); };
  $('#btn-browse').onclick = (e) => { e.stopPropagation(); input.click(); };
  input.onchange = () => { if (input.files[0]) previewFile(input.files[0]); };

  ['dragenter', 'dragover'].forEach((ev) => zone.addEventListener(ev, (e) => {
    e.preventDefault(); zone.classList.add('over');
  }));
  ['dragleave', 'drop'].forEach((ev) => zone.addEventListener(ev, (e) => {
    e.preventDefault(); zone.classList.remove('over');
  }));
  zone.addEventListener('drop', (e) => {
    const file = e.dataTransfer.files[0];
    if (file) previewFile(file);
  });

  $('#btn-import-create').onclick = createFromImport;
  $$('[data-fill]').forEach((el) => {
    el.onclick = () => fillAndDownload(el.dataset.fill);
  });
}

async function previewFile(file) {
  state.previewFile = file;
  const body = new FormData();
  body.append('file', file);
  toast('Розбираю файл…');
  try {
    const preview = await api('/api/import/preview', { method: 'POST', body });
    state.preview = preview;
    renderPreview();
  } catch (err) { toast(err.message, 'err'); }
}

function renderPreview() {
  const p = state.preview;
  $('#import-preview').classList.remove('hidden');
  $('#prev-name').textContent = p.object_name || state.previewFile.name;
  $('#prev-meta').innerHTML = [
    p.address && `Адреса: ${esc(p.address)}`,
    p.city && `Місто: <b>${esc(p.city)}</b>`,
    p.region && `Область: <b>${esc(p.region)}</b>`,
    p.doc_code && `Шифр: ${esc(p.doc_code)}`,
    `Позицій: <b>${p.total_positions}</b>`,
    `Розділів: <b>${p.sections.length}</b>`,
  ].filter(Boolean).join(' · ');

  if (p.city && !$('#imp-city').value) $('#imp-city').value = p.city;
  if (p.region && !$('#imp-region').value) $('#imp-region').value = p.region;

  const unmatched = p.sections.reduce((acc, s) =>
    acc + s.positions.filter((x) => !x.match).length, 0);
  const warnings = [...p.warnings];
  if (unmatched) {
    warnings.push(`Для ${unmatched} позицій не знайдено розцінки в довіднику — `
      + 'ціну можна ввести вручну або додати розцінку в довідник.');
  }
  $('#prev-warnings').innerHTML = warnings.map((w) => `<div class="warn">${esc(w)}</div>`).join('');

  const rows = ['<table class="grid"><thead><tr><th style="width:44px">№</th>'
    + '<th>Найменування</th><th style="width:70px">Од.</th><th style="width:100px">Кількість</th>'
    + '<th>Розцінка з довідника</th><th style="width:80px">Збіг</th></tr></thead><tbody>'];
  p.sections.forEach((s) => {
    const title = s.kind === 'estimate'
      ? `Локальний кошторис ${[s.code, s.title].filter(Boolean).join(' · ')}`
      : `${s.code ? `Розділ ${s.code}. ` : ''}${s.title}`;
    rows.push(`<tr class="band ${s.kind === 'estimate' ? 'band-est' : ''}"><td colspan="6">${esc(title)}</td></tr>`);
    s.positions.forEach((x) => {
      const m = x.match;
      rows.push(`<tr><td class="ctr">${x.number}</td><td>${esc(x.name)}</td>
        <td class="ctr">${esc(x.unit)}</td><td class="money">${qty(x.quantity)}</td>
        <td class="${m ? '' : 'muted'}">${m ? esc(m.name) : 'не знайдено'}</td>
        <td class="ctr">${m ? `${m.score}%` : '—'}</td></tr>`);
    });
  });
  rows.push('</tbody></table>');
  $('#prev-table').innerHTML = rows.join('');
}

function importFormData() {
  const body = new FormData();
  body.append('file', state.previewFile);
  body.append('city', $('#imp-city').value.trim());
  body.append('region', $('#imp-region').value.trim());
  body.append('strategy', $('#imp-strategy').value);
  return body;
}

async function createFromImport() {
  if (!state.previewFile) { toast('Спершу оберіть файл', 'err'); return; }
  const button = $('#btn-import-create');
  button.disabled = true;
  button.innerHTML = '<span class="spinner"></span>Рахую…';
  try {
    const result = await api('/api/import', { method: 'POST', body: importFormData() });
    toast(`Створено об'єкт: ${result.stats.priced} з ${result.stats.total} позицій оцінено`, 'ok');
    await loadObjects(result.object.id);
  } catch (err) { toast(err.message, 'err'); } finally {
    button.disabled = false;
    button.textContent = 'Створити об\'єкт і порахувати';
  }
}

async function fillAndDownload(fmt) {
  if (!state.previewFile) { toast('Спершу оберіть файл', 'err'); return; }
  const body = importFormData();
  body.append('fmt', fmt);
  body.append('keep', 'true');
  toast('Заповнюю кошторис…');
  try {
    const response = await fetch('/api/import/fill', { method: 'POST', body });
    if (!response.ok) throw new Error((await response.json()).detail || 'Помилка');
    const blob = await response.blob();
    const name = decodeURIComponent(
      (response.headers.get('Content-Disposition') || '').split("filename*=UTF-8''")[1] || `koshtorys.${fmt}`);
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url; link.download = name;
    document.body.appendChild(link); link.click(); link.remove();
    URL.revokeObjectURL(url);
    const total = response.headers.get('X-Budsmet-Total');
    toast(`Готово: ${money(total)} грн з ПДВ`, 'ok');
    loadObjects();
  } catch (err) { toast(err.message, 'err'); }
}

/* ----------------------------------------------------------------- довідник */

async function loadCatalog() {
  const q = $('#cat-search').value.trim();
  const category = $('#cat-category').value;
  const { items } = await api(
    `/api/catalog?q=${encodeURIComponent(q)}&category=${encodeURIComponent(category)}&limit=200`);

  if ($('#cat-category').options.length <= 1 && state.meta) {
    $('#cat-category').innerHTML = '<option value="">усі розділи</option>'
      + state.meta.categories.map((c) => `<option>${esc(c)}</option>`).join('');
  }

  $('#catalog-table').innerHTML = `<table class="grid"><thead><tr>
      <th>Найменування</th><th style="width:70px">Од.</th>
      <th class="money" style="width:110px">Роботи</th>
      <th class="money" style="width:110px">Матеріали</th>
      <th class="money" style="width:100px">Машини</th>
      <th style="width:190px">Розділ</th><th style="width:80px"></th></tr></thead><tbody>
      ${items.map((i) => `<tr>
        <td>${esc(i.name)}</td><td class="ctr">${esc(i.unit)}</td>
        <td class="money">${money(i.labor)}</td><td class="money">${money(i.material)}</td>
        <td class="money">${money(i.machines)}</td>
        <td class="muted">${esc(i.category || '')}</td>
        <td class="ctr"><button class="rowdel" data-edit='${esc(JSON.stringify(i))}' title="Редагувати">✎</button></td>
      </tr>`).join('')}</tbody></table>`;

  $$('#catalog-table [data-edit]').forEach((b) => {
    b.onclick = () => catalogModal(JSON.parse(b.dataset.edit.replace(/&#39;/g, "'")));
  });
}

function catalogModal(item = {}) {
  $('#modal-body').innerHTML = `
    <h2>${item.code ? 'Редагування розцінки' : 'Нова розцінка'}</h2>
    <div class="form-grid">
      <label class="wide">Найменування роботи<input id="m-name" value="${esc(item.name || '')}"></label>
      <label>Одиниця виміру<input id="m-unit" list="units-list" value="${esc(item.unit || '')}"></label>
      <label>Розділ<input id="m-cat" value="${esc(item.category || '')}"></label>
      <label>Роботи, грн/од<input id="m-labor" type="number" step="0.01" value="${item.labor ?? 0}"></label>
      <label>Матеріали, грн/од<input id="m-mat" type="number" step="0.01" value="${item.material ?? 0}"></label>
      <label>Машини, грн/од<input id="m-mach" type="number" step="0.01" value="${item.machines ?? 0}"></label>
    </div>
    <div class="modal-actions">
      <button class="ghost" id="m-cancel">Скасувати</button>
      <button class="primary" id="m-save">Зберегти</button>
    </div>`;
  $('#modal').classList.remove('hidden');
  $('#m-cancel').onclick = () => $('#modal').classList.add('hidden');
  $('#m-save').onclick = async () => {
    try {
      await api('/api/catalog', json('POST', {
        code: item.code || '', name: $('#m-name').value.trim(), unit: $('#m-unit').value.trim(),
        category: $('#m-cat').value.trim(), labor: Number($('#m-labor').value),
        material: Number($('#m-mat').value), machines: Number($('#m-mach').value),
      }));
      $('#modal').classList.add('hidden');
      toast('Розцінку збережено', 'ok');
      await loadMeta();
      loadCatalog();
    } catch (err) { toast(err.message, 'err'); }
  };
}

/* ------------------------------------------------------------------ історія */

async function loadHistory() {
  const { items } = await api(`/api/history?q=${encodeURIComponent($('#hist-search').value.trim())}`);
  $('#history-table').innerHTML = items.length ? `<table class="grid"><thead><tr>
      <th>Найменування</th><th style="width:70px">Од.</th><th style="width:120px">Місто</th>
      <th class="money" style="width:110px">Роботи</th><th class="money" style="width:110px">Матеріали</th>
      <th style="width:150px">Записано</th></tr></thead><tbody>
      ${items.map((h) => `<tr><td>${esc(h.name)}</td><td class="ctr">${esc(h.unit)}</td>
        <td>${esc(h.city || '—')}</td><td class="money">${money(h.labor_price)}</td>
        <td class="money">${money(h.material_price)}</td>
        <td class="muted">${esc(h.created_at)}</td></tr>`).join('')}</tbody></table>`
    : '<div class="empty"><p class="muted">Історія порожня. Відкрийте кошторис і натисніть «В історію», '
      + 'щоб зафіксувати його ціни для майбутніх об\'єктів.</p></div>';
}

/* ---------------------------------------------------------- новий об'єкт */

function newObjectModal() {
  $('#modal-body').innerHTML = `
    <h2>Новий об'єкт</h2>
    <div class="form-grid">
      <label class="wide">Назва об'єкта<input id="n-name" placeholder="Капітальний ремонт …"></label>
      <label class="wide">Адреса<input id="n-address" placeholder="область, район, місто, вулиця, буд."></label>
      <label>Місто<input id="n-city" list="cities-list"></label>
      <label>Область<input id="n-region" list="regions-list"></label>
      <label>Замовник<input id="n-customer"></label>
      <label>Шифр<input id="n-doccode"></label>
    </div>
    <div class="modal-actions">
      <button class="ghost" id="n-cancel">Скасувати</button>
      <button class="primary" id="n-save">Створити</button>
    </div>`;
  $('#modal').classList.remove('hidden');
  $('#n-name').focus();
  $('#n-cancel').onclick = () => $('#modal').classList.add('hidden');
  $('#n-save').onclick = async () => {
    const name = $('#n-name').value.trim();
    if (!name) { toast('Вкажіть назву об\'єкта', 'err'); return; }
    try {
      const obj = await api('/api/objects', json('POST', {
        name, address: $('#n-address').value.trim(), city: $('#n-city').value.trim(),
        region: $('#n-region').value.trim(), customer: $('#n-customer').value.trim(),
        doc_code: $('#n-doccode').value.trim(),
      }));
      $('#modal').classList.add('hidden');
      await loadObjects(obj.id);
      toast('Об\'єкт створено', 'ok');
    } catch (err) { toast(err.message, 'err'); }
  };
}

/* --------------------------------------------------------------------- init */

function init() {
  $$('.nav-btn').forEach((b) => { b.onclick = () => showView(b.dataset.view); });
  $$('[data-goto]').forEach((b) => { b.onclick = () => showView(b.dataset.goto); });
  $('#btn-new-object').onclick = newObjectModal;
  $('#btn-new-object-2').onclick = newObjectModal;
  $('#btn-cat-new').onclick = () => catalogModal();
  $('#btn-add-position').onclick = addPosition;
  $('#btn-reprice').onclick = repriceObject;
  $('#add-qty').onkeydown = (e) => { if (e.key === 'Enter') addPosition(); };

  ['#obj-address', '#obj-city', '#obj-region', '#obj-customer', '#obj-doccode',
    '#set-overhead', '#set-profit', '#set-admin', '#set-risk', '#set-vat'].forEach((sel) => {
    $(sel).onchange = saveObjectFields;
  });
  $('#set-strategy').onchange = saveObjectFields;
  $('#set-materials').onchange = saveObjectFields;
  $('#obj-name').onblur = saveObjectFields;
  $('#obj-name').onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); e.target.blur(); } };

  $('#btn-to-history').onclick = async () => {
    try {
      const r = await api(`/api/objects/${state.currentId}/history`, { method: 'POST' });
      toast(`У історію збережено ${r.saved} позицій`, 'ok');
    } catch (err) { toast(err.message, 'err'); }
  };

  $('#btn-delete-object').onclick = async () => {
    if (!confirm('Видалити об\'єкт разом із кошторисом?')) return;
    await api(`/api/objects/${state.currentId}`, { method: 'DELETE' });
    state.currentId = null;
    $('#object-view').classList.add('hidden');
    $('#object-empty').classList.remove('hidden');
    loadObjects();
  };

  $$('.dropdown > button').forEach((b) => {
    b.onclick = (e) => {
      e.stopPropagation();
      const parent = b.parentElement;
      const open = parent.classList.contains('open');
      $$('.dropdown').forEach((d) => d.classList.remove('open'));
      parent.classList.toggle('open', !open);
    };
  });
  document.addEventListener('click', () => $$('.dropdown').forEach((d) => d.classList.remove('open')));
  $('#modal').onclick = (e) => { if (e.target.id === 'modal') $('#modal').classList.add('hidden'); };

  let catTimer;
  $('#cat-search').oninput = () => { clearTimeout(catTimer); catTimer = setTimeout(loadCatalog, 220); };
  $('#cat-category').onchange = loadCatalog;
  let histTimer;
  $('#hist-search').oninput = () => { clearTimeout(histTimer); histTimer = setTimeout(loadHistory, 220); };

  setupAutocomplete();
  setupImport();

  loadMeta().then(loadObjects).catch((err) => toast(err.message, 'err'));
}

document.addEventListener('DOMContentLoaded', init);
