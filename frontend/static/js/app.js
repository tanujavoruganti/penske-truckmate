/* ── TruckMate — split-panel frontend ──────────────────────────────
   Left panel: chat (always visible)
   Right panel: form (always visible, auto-filled from chat)
   Results: full-width, replaces workspace
──────────────────────────────────────────────────────────────────── */

const API = '';

// ── State ───────────────────────────────────────────────────────────
const state = {
  messages:   [],   // [{role, content}]
  items:      [],   // cargo items added to the form
  objectsDB:  {},
  moveType:   'household',
  hasCDL:     false,
  priority:   'cost',
};

let _acNames   = [];  // flat sorted list for autocomplete, populated after DB loads
let _acFocused = -1;  // keyboard-nav cursor index

// ── Cargo presets ───────────────────────────────────────────────────
const PRESETS = {
  studio: [
    { key: 'mattress_twin',  qty: 1 }, { key: 'bed_frame_twin',  qty: 1 },
    { key: 'dresser_small',  qty: 1 }, { key: 'sofa_2_seat',     qty: 1 },
    { key: 'coffee_table',   qty: 1 }, { key: 'tv_55in',         qty: 1 },
    { key: 'box_medium',     qty: 12 }, { key: 'box_small',      qty: 6  },
  ],
  '1bed': [
    { key: 'mattress_queen', qty: 1 }, { key: 'bed_frame_queen', qty: 1 },
    { key: 'dresser_large',  qty: 1 }, { key: 'nightstand',      qty: 2 },
    { key: 'sofa_3_seat',    qty: 1 }, { key: 'coffee_table',    qty: 1 },
    { key: 'tv_55in',        qty: 1 }, { key: 'tv_stand',        qty: 1 },
    { key: 'dining_table_small', qty: 1 }, { key: 'dining_chair', qty: 4 },
    { key: 'box_medium',     qty: 20 }, { key: 'box_small',      qty: 10 },
  ],
  '2bed': [
    { key: 'mattress_queen', qty: 1 }, { key: 'mattress_full',   qty: 1 },
    { key: 'bed_frame_queen',qty: 1 }, { key: 'bed_frame_full',  qty: 1 },
    { key: 'dresser_large',  qty: 2 }, { key: 'nightstand',      qty: 4 },
    { key: 'sofa_3_seat',    qty: 1 }, { key: 'armchair',        qty: 1 },
    { key: 'coffee_table',   qty: 1 }, { key: 'tv_55in',         qty: 2 },
    { key: 'tv_stand',       qty: 1 }, { key: 'dining_table_small', qty: 1 },
    { key: 'dining_chair',   qty: 4 }, { key: 'refrigerator_standard', qty: 1 },
    { key: 'washer',         qty: 1 }, { key: 'dryer',           qty: 1 },
    { key: 'box_medium',     qty: 30 }, { key: 'box_large',      qty: 10 },
    { key: 'box_wardrobe',   qty: 2  },
  ],
  '3bed': [
    { key: 'mattress_king',  qty: 1 }, { key: 'mattress_queen',  qty: 1 },
    { key: 'mattress_full',  qty: 1 }, { key: 'bed_frame_king',  qty: 1 },
    { key: 'bed_frame_queen',qty: 1 }, { key: 'bed_frame_full',  qty: 1 },
    { key: 'dresser_large',  qty: 3 }, { key: 'nightstand',      qty: 4 },
    { key: 'wardrobe',       qty: 1 }, { key: 'sofa_3_seat',     qty: 1 },
    { key: 'sectional_sofa', qty: 1 }, { key: 'recliner',        qty: 1 },
    { key: 'coffee_table',   qty: 1 }, { key: 'dining_table_large', qty: 1 },
    { key: 'dining_chair',   qty: 6 }, { key: 'bookshelf_large', qty: 2 },
    { key: 'tv_75in',        qty: 1 }, { key: 'tv_55in',         qty: 2 },
    { key: 'refrigerator_french_door', qty: 1 },
    { key: 'washer',         qty: 1 }, { key: 'dryer',           qty: 1 },
    { key: 'box_medium',     qty: 50 }, { key: 'box_large',      qty: 20 },
    { key: 'box_wardrobe',   qty: 4  },
  ],
};

// ── Utilities ───────────────────────────────────────────────────────
const $  = id  => document.getElementById(id);
const esc = s  => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

function showToast(msg, duration = 5000) {
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), duration);
}

function showLoading(msg = 'Analysing your requirements…') {
  let el = document.querySelector('.loading-overlay');
  if (!el) { el = document.createElement('div'); el.className = 'loading-overlay'; document.body.appendChild(el); }
  el.innerHTML = `<div class="loading-box"><div class="spin"></div><div class="loading-text">${esc(msg)}</div></div>`;
}
function hideLoading() { document.querySelector('.loading-overlay')?.remove(); }

// ── View switching ──────────────────────────────────────────────────
function showResults() {
  $('workspace').classList.add('hidden');
  $('results-view').classList.remove('hidden');
  $('pill-configure').classList.remove('active');
  $('pill-results').classList.add('active');
  $('start-over-btn').classList.remove('hidden');
}

function showWorkspace() {
  $('results-view').classList.add('hidden');
  $('workspace').classList.remove('hidden');
  $('pill-results').classList.remove('active');
  $('pill-configure').classList.add('active');
  $('start-over-btn').classList.add('hidden');
}

// ── Chat ────────────────────────────────────────────────────────────
function appendMsg(role, content) {
  const wrap = $('chat-messages');
  const div  = document.createElement('div');
  div.className = `msg ${role}`;
  div.innerHTML = `
    <div class="msg-avatar">${role === 'assistant' ? '🚛' : '👤'}</div>
    <div class="msg-bubble">${esc(content)}</div>`;
  wrap.appendChild(div);
  wrap.scrollTop = wrap.scrollHeight;
}

function showTyping() {
  const wrap = $('chat-messages');
  const div  = document.createElement('div');
  div.id = 'typing'; div.className = 'msg assistant';
  div.innerHTML = `<div class="msg-avatar">🚛</div>
    <div class="msg-bubble"><div class="typing-dots"><span></span><span></span><span></span></div></div>`;
  wrap.appendChild(div);
  wrap.scrollTop = wrap.scrollHeight;
}
function removeTyping() { $('typing')?.remove(); }

async function sendMessage() {
  const input = $('chat-input');
  const text  = input.value.trim();
  if (!text) return;

  input.value = '';
  input.disabled = true;
  $('chat-send').disabled = true;

  appendMsg('user', text);
  state.messages.push({ role: 'user', content: text });
  showTyping();

  try {
    const res  = await fetch(`${API}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages: state.messages }),
    });
    if (!res.ok) throw new Error(`Server error ${res.status}`);
    const data = await res.json();

    removeTyping();
    appendMsg('assistant', data.response);
    state.messages.push({ role: 'assistant', content: data.response });

    // Auto-fill the form with anything the agent extracted
    if (data.extracted_data && Object.keys(data.extracted_data).length > 0) {
      applyExtracted(data.extracted_data);
    }
  } catch (err) {
    removeTyping();
    const msg = 'I couldn\'t reach the server. Make sure the backend is running.';
    appendMsg('assistant', msg);
    showToast(err.message);
  } finally {
    input.disabled = false;
    $('chat-send').disabled = false;
    input.focus();
  }
}

// ── Form auto-fill from extracted data ─────────────────────────────
function pulse(el) {
  el.classList.remove('autofill');
  void el.offsetWidth; // force reflow
  el.classList.add('autofill');
  setTimeout(() => el.classList.remove('autofill'), 1200);
}

function setToggle(groupId, value, stateKey) {
  const group = $(groupId);
  const btn   = [...group.querySelectorAll('.tog')].find(b => b.dataset.value === String(value));
  if (!btn || btn.classList.contains('active')) return;
  group.querySelectorAll('.tog').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  btn.classList.add('autofill');
  setTimeout(() => btn.classList.remove('autofill'), 600);
  if (stateKey) state[stateKey] = value === 'true' ? true : value === 'false' ? false : value;
}

function applyExtracted(data) {
  if (data.move_type)      setToggle('move-type-group', data.move_type, 'moveType');
  if (data.has_cdl != null) setToggle('cdl-group', String(data.has_cdl), 'hasCDL');
  if (data.priority)       setToggle('priority-group', data.priority, 'priority');

  if (data.distance_miles != null) {
    const el = $('f-distance');
    el.value = data.distance_miles;
    pulse(el);
  }
  if (data.rental_days != null) {
    const el = $('f-days');
    el.value = data.rental_days;
    pulse(el);
  }

  // Room hint triggers a preset (only if items list is currently empty)
  if (data.room_hint && state.items.length === 0) {
    loadPreset(data.room_hint);
  }

  // Specific items mentioned by user
  if (Array.isArray(data.items) && data.items.length > 0) {
    data.items.forEach(it => addItem(it.name, it.quantity || 1));
  }
}

// ── Object DB helpers ───────────────────────────────────────────────
function findInDB(name) {
  const needle = name.toLowerCase().replace(/_/g, ' ').trim();
  for (const cat of Object.values(state.objectsDB.categories || {})) {
    for (const [key, item] of Object.entries(cat.items)) {
      const kn = key.replace(/_/g, ' ');
      if (kn === needle || kn.includes(needle) || needle.includes(kn)) return { key, ...item };
      for (const alias of (item.aliases || [])) {
        const an = alias.toLowerCase();
        if (an === needle || an.includes(needle) || needle.includes(an)) return { key, ...item };
      }
    }
  }
  return null;
}

function addItem(name, qty = 1, weightOverride = null, volumeOverride = null) {
  const db     = findInDB(name);
  const weight = weightOverride ?? (db?.weight_lb   ?? null);
  const volume = volumeOverride ?? (db?.volume_cuft ?? null);
  const label  = db ? db.key.replace(/_/g, ' ') : name;
  state.items.push({ name: db ? db.key : name, label, quantity: qty, weight_lb: weight, volume_cuft: volume });
  renderItems();
}

function removeItem(idx) { state.items.splice(idx, 1); renderItems(); }

function renderSummary() {
  const summary = $('items-summary');
  if (!state.items.length) { summary.classList.add('hidden'); return; }
  let totalW = 0, totalV = 0;
  state.items.forEach(it => {
    if (it.weight_lb)   totalW += it.weight_lb   * it.quantity;
    if (it.volume_cuft) totalV += it.volume_cuft * it.quantity;
  });
  summary.classList.remove('hidden');
  summary.innerHTML =
    `<span>📦 <strong>${state.items.length}</strong> types</span>` +
    `<span>⚖️ ~<strong>${totalW.toFixed(0)} lb</strong> raw</span>` +
    `<span>📐 ~<strong>${totalV.toFixed(0)} cu ft</strong> raw</span>`;
}

const _editIcon = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>`;

function itemMeta(item) {
  const w = item.weight_lb   ? (item.weight_lb   * item.quantity).toFixed(0) : null;
  const v = item.volume_cuft ? (item.volume_cuft * item.quantity).toFixed(1) : null;
  return [`×${item.quantity}`, w ? `${w} lb` : null, v ? `${v} cu ft` : null].filter(Boolean).join(' · ');
}

function renderItems() {
  const list = $('items-list');
  list.innerHTML = '';

  if (!state.items.length) { $('items-summary').classList.add('hidden'); return; }

  state.items.forEach((item, i) => {
    const row = document.createElement('div');
    row.className = 'item-row';
    row.innerHTML = `
      <div class="item-row-header">
        <span class="item-row-name">${esc(item.label)}</span>
        <span class="item-row-meta">${itemMeta(item)}</span>
        <button type="button" class="item-edit-btn" data-idx="${i}" aria-label="Edit" title="Edit weight / volume">${_editIcon}</button>
        <button class="item-remove"   data-idx="${i}" aria-label="Remove">✕</button>
      </div>
      <div class="item-row-controls">
        <label class="item-ctrl-label">Qty
          <input type="number" class="item-ctrl-input" data-field="quantity" data-idx="${i}"
                 value="${item.quantity}" min="1" step="1">
        </label>
        <label class="item-ctrl-label">lb / unit
          <input type="number" class="item-ctrl-input" data-field="weight_lb" data-idx="${i}"
                 value="${item.weight_lb ?? ''}" min="0" step="1" placeholder="?">
        </label>
        <label class="item-ctrl-label">cu ft / unit
          <input type="number" class="item-ctrl-input" data-field="volume_cuft" data-idx="${i}"
                 value="${item.volume_cuft ?? ''}" min="0" step="0.1" placeholder="?">
        </label>
      </div>`;
    list.appendChild(row);
  });

  list.querySelectorAll('.item-remove').forEach(b =>
    b.addEventListener('click', () => removeItem(+b.dataset.idx))
  );

  list.querySelectorAll('.item-edit-btn').forEach(b =>
    b.addEventListener('click', () => {
      const controls = b.closest('.item-row').querySelector('.item-row-controls');
      const open = controls.classList.toggle('expanded');
      b.classList.toggle('active', open);
      if (open) controls.querySelector('.item-ctrl-input').focus();
    })
  );

  list.querySelectorAll('.item-ctrl-input').forEach(input =>
    input.addEventListener('input', () => {
      const idx   = +input.dataset.idx;
      const field = input.dataset.field;
      if (field === 'quantity') {
        state.items[idx].quantity = Math.max(1, parseInt(input.value, 10) || 1);
      } else {
        state.items[idx][field] = input.value === '' ? null : parseFloat(input.value);
      }
      // update the meta line in place (no full re-render so focus is preserved)
      input.closest('.item-row').querySelector('.item-row-meta').textContent = itemMeta(state.items[idx]);
      renderSummary();
    })
  );

  renderSummary();
}

function loadPreset(key) {
  state.items = [];
  (PRESETS[key] || []).forEach(({ key: name, qty }) => addItem(name, qty));
}

// ── Toggle group helper ─────────────────────────────────────────────
function bindToggle(groupId, onValue) {
  $(groupId).querySelectorAll('.tog').forEach(btn =>
    btn.addEventListener('click', () => {
      $(groupId).querySelectorAll('.tog').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      onValue(btn.dataset.value);
    })
  );
}

// ── Form submission ─────────────────────────────────────────────────
async function submitForm(e) {
  e.preventDefault();

  const distEl = $('f-distance'), daysEl = $('f-days');
  const dist   = parseFloat(distEl.value);
  const days   = parseInt(daysEl.value, 10);

  [distEl, daysEl].forEach(el => el.classList.remove('error'));
  let ok = true;
  if (isNaN(dist) || dist < 0) { distEl.classList.add('error'); ok = false; }
  if (isNaN(days) || days < 1) { daysEl.classList.add('error'); ok = false; }
  if (!ok) { showToast('Please fill in Distance and Rental Days.'); return; }

  const manual = $('ctab-manual').classList.contains('active');
  let payload  = {};

  if (manual) {
    const w = parseFloat($('f-weight').value);
    const v = parseFloat($('f-volume').value);
    if (!isNaN(w) && w > 0) payload.custom_total_weight_lb  = w;
    if (!isNaN(v) && v > 0) payload.custom_total_volume_cuft = v;
    if (!payload.custom_total_weight_lb && !payload.custom_total_volume_cuft) {
      showToast('Enter your total weight or volume in Manual Totals mode.'); return;
    }
    payload.items = [];
  } else {
    if (!state.items.length) { showToast('Add at least one item, or switch to Manual Totals.'); return; }
    payload.items = state.items.map(it => ({
      name: it.name, quantity: it.quantity,
      weight_lb: it.weight_lb || null, volume_cuft: it.volume_cuft || null,
    }));
  }

  const budget = parseFloat($('f-budget').value);
  const body = {
    move_type:      state.moveType,
    distance_miles: dist,
    rental_days:    days,
    has_cdl:        state.hasCDL,
    priority:       state.priority,
    max_budget_usd: (!isNaN(budget) && budget > 0) ? budget : null,
    chat_context:   state.messages.map(m => `${m.role}: ${m.content}`).join('\n'),
    ...payload,
  };

  showLoading('Crunching numbers and consulting the knowledge base…');
  try {
    const res = await fetch(`${API}/api/recommend`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail?.message || `Server error ${res.status}`);
    }
    const data = await res.json();
    hideLoading();
    renderResults(data);
    showResults();
  } catch (err) {
    hideLoading();
    showToast(`Recommendation failed: ${err.message}`);
  }
}

// ── Results ─────────────────────────────────────────────────────────
function utilClass(pct) {
  return pct < 60 ? 'uf-green' : pct < 88 ? 'uf-amber' : 'uf-red';
}

function renderResults(data) {
  const { recommended_truck: top, alternatives, load_summary, explanation, warnings, policy_snippets } = data;
  const cost = top.cost_estimate || {};
  const vU   = Math.min(top.volume_utilization_pct,  100);
  const pU   = Math.min(top.payload_utilization_pct, 100);

  const altCards = (alternatives || []).map(t => {
    const vp = Math.min(t.volume_utilization_pct,  100);
    const pp = Math.min(t.payload_utilization_pct, 100);
    return `<div class="alt-card">
      <div class="alt-name">${esc(t.name)}</div>
      <div class="alt-price">$${t.estimated_total_usd.toFixed(2)}</div>
      <div class="alt-stat">Payload: ${t.payload_utilization_pct}% used</div>
      <div class="alt-bar"><div class="alt-bar-fill" data-w="${pp}" style="width:0"></div></div>
      <div class="alt-stat" style="margin-top:6px">Volume: ${t.volume_utilization_pct}% used</div>
      <div class="alt-bar"><div class="alt-bar-fill" data-w="${vp}" style="width:0"></div></div>
    </div>`;
  }).join('');

  const warnHtml = (warnings?.length)
    ? `<div class="warn-box"><strong>⚠️ Heads up</strong>${warnings.map(w => `<div>${esc(w)}</div>`).join('')}</div>`
    : '';

  const policyHtml = (policy_snippets?.length)
    ? `<div class="policy-accordion">
        <button class="policy-toggle" id="ptoggle">
          📋 Relevant Policy Notes
          <svg class="chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg>
        </button>
        <div class="policy-body" id="pbody">
          ${policy_snippets.map(s => `<p style="margin-bottom:10px">${esc(s)}</p>`).join('')}
        </div>
      </div>`
    : '';

  $('results-shell').innerHTML = `
    <div class="results-grid">
      <div class="primary-card">
        <div class="rec-badge">⭐ Our Recommendation</div>
        <div class="truck-name">${esc(top.name)}</div>
        <div class="truck-cat">${esc(top.category)} Category</div>
        <div class="truck-badges">
          <span class="tbadge">${esc(top.category)}</span>
          <span class="tbadge ${top.cdl_required ? 'cdl' : 'ncdl'}">${top.cdl_required ? '⚠ CDL Required' : '✓ No CDL Needed'}</span>
        </div>
        <div class="util-bars">
          <div>
            <div class="util-label"><span>Payload utilisation</span><span>${top.payload_utilization_pct}%</span></div>
            <div class="util-track"><div class="util-fill ${utilClass(top.payload_utilization_pct)}" data-w="${pU}" style="width:0"></div></div>
          </div>
          <div>
            <div class="util-label"><span>Volume utilisation</span><span>${top.volume_utilization_pct}%</span></div>
            <div class="util-track"><div class="util-fill ${utilClass(top.volume_utilization_pct)}" data-w="${vU}" style="width:0"></div></div>
          </div>
        </div>
        <div class="primary-price">$${top.estimated_total_usd.toFixed(2)}</div>
        <div class="primary-price-sub">estimated total · excl. insurance</div>
      </div>

      <div class="cost-card">
        <div>
          <h4>Cost Breakdown</h4>
          <table class="cost-table">
            <tr><td>Base rate (${cost.days || 1} day${(cost.days||1)>1?'s':''})</td><td>$${(cost.base_rate_usd||0).toFixed(2)}</td></tr>
            <tr><td>Mileage (${cost.miles||0} mi)</td><td>$${(cost.mileage_cost_usd||0).toFixed(2)}</td></tr>
            <tr><td>Fuel estimate (diesel)</td><td>$${(cost.fuel_estimate_usd||0).toFixed(2)}</td></tr>
            <tr><td>Environmental fee</td><td>$${(cost.environmental_fee_usd||0).toFixed(2)}</td></tr>
            <tr><td><strong>Estimated Total</strong></td><td><strong>$${top.estimated_total_usd.toFixed(2)}</strong></td></tr>
          </table>
        </div>
        <p class="cost-note">💡 ${esc(cost.insurance_note || 'Add $14–$29/day for protection plans (not included above).')}</p>
      </div>
    </div>

    <div class="explanation-card">
      <h4>TruckMate Analysis</h4>
      <div class="explanation-text">${esc(explanation || '')}</div>
    </div>

    ${(alternatives||[]).length ? `
    <div class="alt-section">
      <h4>Alternative Options</h4>
      <div class="alt-cards">${altCards}</div>
    </div>` : ''}

    ${warnHtml}
    ${policyHtml}

    <div class="restart-row">
      <button class="btn-restart" id="restart-btn">↩ Start a new search</button>
    </div>`;

  // Animate bars
  requestAnimationFrame(() => setTimeout(() => {
    $('results-shell').querySelectorAll('[data-w]').forEach(el => { el.style.width = `${el.dataset.w}%`; });
  }, 80));

  // Policy accordion
  $('ptoggle')?.addEventListener('click', () => {
    $('ptoggle').classList.toggle('open');
    $('pbody').classList.toggle('open');
  });

  // Restart
  $('restart-btn').addEventListener('click', resetApp);
  $('start-over-btn').addEventListener('click', resetApp);
}

// ── Reset ───────────────────────────────────────────────────────────
function resetApp() {
  state.messages = [];
  state.items    = [];
  state.moveType = 'household';
  state.hasCDL   = false;
  state.priority = 'cost';

  $('chat-messages').innerHTML = '';
  $('items-list').innerHTML    = '';
  $('items-summary').classList.add('hidden');
  $('custom-item-panel').classList.add('hidden');
  $('custom-toggle-btn').textContent = '＋ Add custom item';
  $('custom-name').value = ''; $('custom-qty').value = '1';
  $('custom-weight').value = ''; $('custom-vol').value = '';
  $('results-shell').innerHTML = '';
  $('intake-form').reset();
  $('f-days').value = '1';

  // Reset toggles to first option
  ['move-type-group', 'cdl-group', 'priority-group'].forEach(gid => {
    const btns = $(gid).querySelectorAll('.tog');
    btns.forEach((b, i) => b.classList.toggle('active', i === 0));
  });

  showWorkspace();
  startChat();
}

// ── Init ────────────────────────────────────────────────────────────
async function loadObjects() {
  try {
    const res = await fetch(`${API}/api/objects`);
    state.objectsDB = await res.json();
    const names = new Set();
    for (const cat of Object.values(state.objectsDB.categories || {})) {
      for (const [key, item] of Object.entries(cat.items)) {
        names.add(key.replace(/_/g, ' '));
        (item.aliases || []).forEach(a => names.add(a));
      }
    }
    _acNames = [...names].sort();
  } catch (e) { console.warn('Objects DB load failed:', e.message); }
}

// ── Themed autocomplete ─────────────────────────────────────────────
function setupAutocomplete() {
  const input    = $('item-search');
  const dropdown = $('item-dropdown');

  function filterNames(q) {
    if (!q) return [];
    const lq = q.toLowerCase();
    return _acNames.filter(n => n.toLowerCase().includes(lq)).slice(0, 12);
  }

  function showDropdown(matches) {
    dropdown.innerHTML = '';
    _acFocused = -1;
    if (!matches.length) { dropdown.classList.add('hidden'); return; }
    matches.forEach(name => {
      const opt = document.createElement('div');
      opt.className = 'ac-option';
      opt.textContent = name;
      opt.addEventListener('mousedown', e => {
        e.preventDefault();
        input.value = name;
        dropdown.classList.add('hidden');
        _acFocused = -1;
      });
      dropdown.appendChild(opt);
    });
    dropdown.classList.remove('hidden');
  }

  input.addEventListener('input', () => showDropdown(filterNames(input.value.trim())));

  input.addEventListener('keydown', e => {
    const opts = [...dropdown.querySelectorAll('.ac-option')];
    if (!opts.length) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      _acFocused = Math.min(_acFocused + 1, opts.length - 1);
      opts.forEach((o, i) => o.classList.toggle('focused', i === _acFocused));
      opts[_acFocused]?.scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      _acFocused = Math.max(_acFocused - 1, 0);
      opts.forEach((o, i) => o.classList.toggle('focused', i === _acFocused));
    } else if (e.key === 'Enter' && _acFocused >= 0) {
      e.preventDefault();
      input.value = opts[_acFocused].textContent;
      dropdown.classList.add('hidden');
      _acFocused = -1;
    } else if (e.key === 'Escape') {
      dropdown.classList.add('hidden');
      _acFocused = -1;
    }
  });

  input.addEventListener('blur',  () => setTimeout(() => { dropdown.classList.add('hidden'); _acFocused = -1; }, 150));
  input.addEventListener('focus', () => { const q = input.value.trim(); if (q) showDropdown(filterNames(q)); });
}

function startChat() {
  const greeting = "Hi there! 👋 I'm TruckMate, your Penske rental assistant. Tell me about your move — what are you transporting and roughly how far?";
  state.messages = [{ role: 'assistant', content: greeting }];
  appendMsg('assistant', greeting);
}

function bindUI() {
  // Chat
  $('chat-send').addEventListener('click', sendMessage);
  $('chat-input').addEventListener('keydown', e => { if (e.key === 'Enter') sendMessage(); });

  // Toggle groups
  bindToggle('move-type-group', v => { state.moveType = v; });
  bindToggle('cdl-group',       v => { state.hasCDL   = v === 'true'; });
  bindToggle('priority-group',  v => { state.priority  = v; });

  // Cargo tabs
  $('ctab-list').addEventListener('click', () => {
    $('ctab-list').classList.add('active'); $('ctab-manual').classList.remove('active');
    $('cargo-list-panel').classList.remove('hidden'); $('cargo-manual-panel').classList.add('hidden');
  });
  $('ctab-manual').addEventListener('click', () => {
    $('ctab-manual').classList.add('active'); $('ctab-list').classList.remove('active');
    $('cargo-manual-panel').classList.remove('hidden'); $('cargo-list-panel').classList.add('hidden');
  });

  // Item adder
  $('add-item-btn').addEventListener('click', () => {
    const name = $('item-search').value.trim();
    const qty  = Math.max(1, parseInt($('item-qty').value, 10) || 1);
    if (!name) return;
    addItem(name, qty);
    $('item-search').value = ''; $('item-qty').value = '1'; $('item-search').focus();
  });
  $('item-search').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); $('add-item-btn').click(); } });

  // Presets
  document.querySelectorAll('.qa-btn').forEach(btn =>
    btn.addEventListener('click', () => loadPreset(btn.dataset.preset))
  );

  // Custom item panel
  $('custom-toggle-btn').addEventListener('click', () => {
    const panel = $('custom-item-panel');
    const open  = panel.classList.toggle('hidden') === false;
    $('custom-toggle-btn').textContent = open ? '✕ Cancel' : '＋ Add custom item';
    if (open) $('custom-name').focus();
  });
  $('custom-add-btn').addEventListener('click', () => {
    const name   = $('custom-name').value.trim();
    const qty    = Math.max(1, parseInt($('custom-qty').value, 10) || 1);
    const weight = parseFloat($('custom-weight').value) || null;
    const vol    = parseFloat($('custom-vol').value)    || null;
    if (!name) { $('custom-name').focus(); return; }
    state.items.push({ name, label: name, quantity: qty, weight_lb: weight, volume_cuft: vol });
    renderItems();
    $('custom-name').value = ''; $('custom-qty').value = '1';
    $('custom-weight').value = ''; $('custom-vol').value = '';
    $('custom-item-panel').classList.add('hidden');
    $('custom-toggle-btn').textContent = '＋ Add custom item';
  });

  // Form submit
  $('intake-form').addEventListener('submit', submitForm);

  // Header start-over (shown only in results view)
  $('start-over-btn').addEventListener('click', resetApp);
}

document.addEventListener('DOMContentLoaded', async () => {
  bindUI();
  setupAutocomplete();
  await loadObjects();
  startChat();
});
