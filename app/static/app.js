// =============================================================================
// AEGIS AI Quant Engine — Realtime Frontend Controller & Copilot Assistant
// =============================================================================

const state = {
  symbols: [],
  selectedSymbol: null,
  selectedTimeframe: '1m',
  positions: [],
  predictions: [],
  candles: [],
  activeFilter: 'BUYSELL',
  pollIntervalMs: 2500,
  isPolling: true,
  isChatBusy: false,
};

// ── Helpers ──────────────────────────────────────────────────────────────────
async function fetchApi(url, opts = {}) {
  try {
    const res = await fetch(url, opts);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    console.error(`API Error [${url}]:`, err);
    return null;
  }
}

function fmtPrice(val) {
  if (val === null || val === undefined || isNaN(val)) return '-';
  const n = Number(val);
  return n >= 1000 ? n.toFixed(2) : n >= 1 ? n.toFixed(4) : n.toFixed(6);
}

function fmtUsd(val) {
  if (val === null || val === undefined || isNaN(val)) return '$0.00';
  const n = Number(val);
  const sign = n > 0 ? '+' : '';
  return `${sign}$${n.toFixed(4)}`;
}

function fmtTime(val) {
  if (!val) return '-';
  return val.replace('T', ' ').slice(11, 19);
}

function escapeHtml(str) {
  return String(str ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[ch]));
}

function formatMarkdown(text) {
  if (!text) return '';
  let formatted = escapeHtml(text);
  formatted = formatted.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
  formatted = formatted.replace(/\*(.*?)\*/g, '<em>$1</em>');
  formatted = formatted.replace(/• (.*?)(?:\n|$)/g, '<li>$1</li>');
  if (formatted.includes('<li>')) {
    formatted = formatted.replace(/(<li>.*<\/li>)/gs, '<ul>$1</ul>');
  }
  formatted = formatted.replace(/\n/g, '<br/>');
  return formatted;
}

// ── Topbar Actions ───────────────────────────────────────────────────────────
async function apiAction(url, label) {
  const log = document.getElementById('console_log');
  if (log) log.textContent = `[*] Menjalankan: ${label || url}...`;
  try {
    const res = await fetch(url, { method: 'POST' });
    const data = await res.json();
    if (log) log.textContent = `[✓ Sukses]:\n${JSON.stringify(data, null, 2)}`;
    await syncAll(true);
  } catch (err) {
    if (log) log.textContent = `[✗ Error]: ${err.message}`;
  }
}

async function triggerRetrain() {
  const log = document.getElementById('console_log');
  if (log) log.textContent = '[*] AEGIS Auto-Learner: Melatih ulang model penantang (~5s)...';
  try {
    const res = await fetch('/api/ai/retrain', { method: 'POST' });
    const data = await res.json();
    if (log) log.textContent = `[✓ Retrain Selesai]:\n${JSON.stringify(data, null, 2)}`;
    await refreshAiBadge();
    await syncAll(true);
  } catch (err) {
    if (log) log.textContent = `[✗ Retrain Error]: ${err.message}`;
  }
}

function toggleChatSidebar() {
  const layout = document.getElementById('app_layout');
  if (layout) layout.classList.toggle('chat-collapsed');
}

// ── Badges & Status Sync ─────────────────────────────────────────────────────
async function refreshAiBadge() {
  const data = await fetchApi('/api/ai/status');
  if (!data) return;
  const badge = document.getElementById('ai_badge');
  const ver = data.version || 'v1.0.0-xgb';
  if (badge) badge.textContent = `AEGIS: ${ver} (${data.active ? 'Aktif' : 'Standby'})`;
}

// ── Metrics & Active Orders Sync ─────────────────────────────────────────────
async function refreshMetricsAndOrders() {
  const [stats, positions] = await Promise.all([
    fetchApi('/api/stats/summary'),
    fetchApi('/api/positions/active'),
  ]);

  if (stats) {
    const totPnl = document.getElementById('total_pnl');
    if (totPnl) {
      totPnl.textContent = fmtUsd(stats.total_pnl_usd);
      totPnl.className = 'm-val ' + (stats.total_pnl_usd >= 0 ? 'good' : 'bad');
    }

    const unPnl = document.getElementById('unrealized_pnl');
    if (unPnl) {
      unPnl.textContent = fmtUsd(stats.unrealized_pnl_usd);
      unPnl.className = stats.unrealized_pnl_usd >= 0 ? 'good' : 'bad';
    }

    const rePnl = document.getElementById('realized_pnl');
    if (rePnl) {
      rePnl.textContent = fmtUsd(stats.realized_pnl_usd);
      rePnl.className = stats.realized_pnl_usd >= 0 ? 'good' : 'bad';
    }

    const dirAcc = document.getElementById('directional_accuracy');
    if (dirAcc) dirAcc.textContent = `${stats.directional_accuracy ?? 0}%`;

    const tpC = document.getElementById('tp_count');
    if (tpC) tpC.textContent = stats.take_profit ?? 0;

    const beC = document.getElementById('be_count');
    if (beC) beC.textContent = stats.breakeven ?? 0;

    const slC = document.getElementById('sl_count');
    if (slC) slC.textContent = (stats.stop_loss ?? 0) + (stats.near_stop ?? 0);

    const pend = document.getElementById('pending_count');
    if (pend) pend.textContent = `${stats.pending ?? 0} Posisi`;

    const totNotional = document.getElementById('total_notional_usd');
    if (totNotional) {
      const notionalPerTrade = stats.position_notional_usd || 50.0;
      totNotional.textContent = `$${((stats.pending || 0) * notionalPerTrade).toFixed(2)}`;
    }

    const guardBadge = document.getElementById('guard_badge');
    if (guardBadge && stats.guard) {
      if (stats.guard.blocked) {
        guardBadge.textContent = '🛡️ GUARD: COOLDOWN (Volatilitas)';
        guardBadge.className = 'guard-pill guard-paused';
      } else {
        guardBadge.textContent = '🛡️ GUARD: AKTIF (Aman)';
        guardBadge.className = 'guard-pill guard-active';
      }
    }
  }

  if (positions) {
    state.positions = Array.isArray(positions) ? positions : [];
    const badge = document.getElementById('active_trade_badge');
    if (badge) badge.textContent = `${state.positions.length} Posisi Berjalan`;
    renderActiveOrdersGrid();

    // Auto-select first active trade on initial load if no selection or current selection is not traded
    if (state.positions.length > 0) {
      const currentTraded = state.positions.some(p => p.symbol === state.selectedSymbol);
      if (!currentTraded) {
        selectSymbol(state.positions[0].symbol, false);
      }
    }
  }

  updateRibbonDisplay();
}

// ── Active Orders Grid Rendering ─────────────────────────────────────────────
function renderActiveOrdersGrid() {
  const container = document.getElementById('active_orders_grid');
  if (!container) return;

  if (!state.positions.length) {
    container.innerHTML = `
      <div class="empty-trades">
        <div class="empty-icon">⏳</div>
        <strong>Belum Ada Posisi Terbuka</strong>
        <p>AEGIS memindai pasar Binance setiap 60 detik. Ketika keyakinan model XGBoost memenuhi syarat, posisi baru akan otomatis muncul di sini.</p>
        <button class="btn btn-predict btn-sm" onclick="apiAction('/api/predict/run-once?force=true', 'Memindai Peluang AI...')">⚡ Scan Peluang AI Sekarang</button>
      </div>
    `;
    return;
  }

  container.innerHTML = state.positions.map(p => {
    const isSelected = p.symbol === state.selectedSymbol ? ' active' : '';
    const isBuy = p.side === 'BUY';
    const sideClass = isBuy ? 'badge-buy' : 'badge-sell';
    const pnlSign = p.unrealized_pnl_usd >= 0 ? 'good' : 'bad';
    const pnlPct = p.unrealized_return_percent !== undefined
      ? `${p.unrealized_return_percent > 0 ? '+' : ''}${p.unrealized_return_percent.toFixed(2)}%`
      : '';

    return `
      <div class="order-card${isSelected}" onclick="selectSymbol('${p.symbol}', true)">
        <div class="order-header">
          <div class="order-symbol-group">
            <strong>${p.symbol}</strong>
            <span class="badge ${sideClass}">${isBuy ? '🟢 BUY' : '🔴 SELL'}</span>
          </div>
          <div class="order-pnl ${pnlSign}">
            <strong>${fmtUsd(p.unrealized_pnl_usd)}</strong>
            <small>(${pnlPct})</small>
          </div>
        </div>

        <div class="order-grid-details">
          <div><small>Harga Masuk</small><b>${fmtPrice(p.entry_price)}</b></div>
          <div><small>Harga Sekarang</small><b>${fmtPrice(p.current_price)}</b></div>
          <div><small>Target TP</small><b class="good">${fmtPrice(p.take_profit_price)}</b></div>
          <div><small>Batas SL</small><b class="bad">${fmtPrice(p.stop_loss_price)}</b></div>
          <div><small>Margin (10x)</small><b>$${p.margin_usd} ($${p.notional_usd})</b></div>
          <div><small>Keyakinan</small><b class="accent-text">${p.confidence}%</b></div>
        </div>

        <div class="order-footer">
          <span>Waktu: ${fmtTime(p.prediction_time)}</span>
          <span class="view-chart-hint">📈 Lihat di chart</span>
        </div>
      </div>
    `;
  }).join('');
}

// ── Status Ribbon Display ────────────────────────────────────────────────────
function updateRibbonDisplay() {
  const p = state.positions.find(item => item.symbol === state.selectedSymbol);
  const sideEl = document.getElementById('ribbon_side');
  if (!sideEl) return;

  if (p) {
    const isBuy = p.side === 'BUY';
    sideEl.textContent = isBuy ? 'BUY (LONG)' : 'SELL (SHORT)';
    sideEl.className = isBuy ? 'good' : 'bad';
    document.getElementById('ribbon_conf').textContent = `${p.confidence}%`;
    document.getElementById('ribbon_entry').textContent = fmtPrice(p.entry_price);
    document.getElementById('ribbon_tp').textContent = `${fmtPrice(p.take_profit_price)}`;
    document.getElementById('ribbon_sl').textContent = `${fmtPrice(p.stop_loss_price)}`;
    const pnlEl = document.getElementById('ribbon_pnl');
    pnlEl.textContent = `${fmtUsd(p.unrealized_pnl_usd)} (${p.unrealized_return_percent ?? 0}%)`;
    pnlEl.className = p.unrealized_pnl_usd >= 0 ? 'good' : 'bad';
    
    const protEl = document.getElementById('ribbon_prot');
    if (protEl) {
      protEl.textContent = p.risk_status === 'IN_PROFIT' ? '🛡️ PROTEKSI AKTIF' : 'NORMAL';
      protEl.className = p.risk_status === 'IN_PROFIT' ? 'good' : '';
    }
  } else {
    // Show clean standby state with live market information (no empty dashes)
    const latestClose = state.candles && state.candles.length > 0 ? state.candles[state.candles.length - 1].close : null;
    sideEl.textContent = 'STANDBY / HOLD';
    sideEl.className = 'muted';
    document.getElementById('ribbon_conf').textContent = 'Memantau Pasar';
    document.getElementById('ribbon_entry').textContent = latestClose ? fmtPrice(latestClose) : '-';
    document.getElementById('ribbon_tp').textContent = 'Otomatis (+0.50%)';
    document.getElementById('ribbon_sl').textContent = 'Otomatis (-0.25%)';
    const pnlEl = document.getElementById('ribbon_pnl');
    pnlEl.textContent = '$0.00';
    pnlEl.className = 'muted';
    const protEl = document.getElementById('ribbon_prot');
    if (protEl) {
      protEl.textContent = 'SIAP';
      protEl.className = 'muted';
    }
  }
}

// ── Symbols Initialization ───────────────────────────────────────────────────
async function initSymbols() {
  const list = await fetchApi('/api/symbols');
  if (!list || !list.length) return;
  state.symbols = list.map(item => item.symbol);
  if (!state.selectedSymbol) {
    state.selectedSymbol = state.symbols[0];
  }

  const select = document.getElementById('symbol_select');
  if (select) {
    select.innerHTML = state.symbols.map(s => `<option value="${s}">${s}</option>`).join('');
    select.value = state.selectedSymbol;
    select.addEventListener('change', e => {
      selectSymbol(e.target.value, true);
    });
  }
}

function selectSymbol(sym, triggerChart = true) {
  state.selectedSymbol = sym;
  const select = document.getElementById('symbol_select');
  if (select) select.value = sym;
  renderActiveOrdersGrid();
  updateRibbonDisplay();
  if (triggerChart) {
    refreshChart(true);
  }
}

function setTimeframe(tf) {
  state.selectedTimeframe = tf;
  document.querySelectorAll('.tf-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tf === tf);
  });
  refreshChart(true);
}

// ── Chart with Smooth Neon Rendering & TP/SL Levels ──────────────────────────
async function refreshChart(force = false) {
  if (!state.selectedSymbol) return;
  const sym = state.selectedSymbol;
  const tf = state.selectedTimeframe || '1m';

  const titleEl = document.getElementById('chart_title');
  if (titleEl) titleEl.textContent = `Grafik ${sym} (${tf})`;

  const candles = await fetchApi(`/api/candles/${sym}/${tf}?limit=100`);
  if (!candles || !candles.length) {
    const statEl = document.getElementById('chart_status');
    if (statEl) statEl.textContent = 'Tidak ada candle tersedia';
    return;
  }
  state.candles = candles;

  const lastCandle = candles[candles.length - 1];
  const statEl = document.getElementById('chart_status');
  if (statEl) {
    statEl.textContent = `Harga: $${fmtPrice(lastCandle.close)} • ${new Date().toLocaleTimeString()}`;
  }

  updateRibbonDisplay();
  drawChartWithLevels(candles);
}

function drawChartWithLevels(candles) {
  const canvas = document.getElementById('price_chart');
  if (!canvas) return;

  const rect = canvas.getBoundingClientRect();
  const parentWidth = canvas.parentElement ? canvas.parentElement.clientWidth : 800;
  const w = rect.width > 20 ? rect.width : parentWidth;
  const h = 280;

  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(w * dpr));
  canvas.height = Math.max(1, Math.floor(h * dpr));

  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const pad = { left: 16, right: 90, top: 25, bottom: 25 };
  const plotW = w - pad.left - pad.right;
  const plotH = h - pad.top - pad.bottom;

  const activePosition = state.positions.find(p => p.symbol === state.selectedSymbol);
  let pricePoints = candles.map(c => Number(c.close)).concat(candles.map(c => Number(c.high)), candles.map(c => Number(c.low)));

  if (activePosition) {
    if (activePosition.entry_price) pricePoints.push(Number(activePosition.entry_price));
    if (activePosition.take_profit_price) pricePoints.push(Number(activePosition.take_profit_price));
    if (activePosition.stop_loss_price) pricePoints.push(Number(activePosition.stop_loss_price));
  }

  let min = Math.min(...pricePoints);
  let max = Math.max(...pricePoints);
  const span = max - min || (max * 0.01);
  min -= span * 0.08;
  max += span * 0.08;

  const nBars = candles.length > 1 ? candles.length - 1 : 1;
  const getX = i => pad.left + (i * plotW / nBars);
  const getY = p => pad.top + (max - p) * plotH / (max - min);

  // Background Grid Lines & Right Price Labels
  ctx.strokeStyle = 'rgba(255, 255, 255, 0.05)';
  ctx.lineWidth = 1;
  ctx.fillStyle = '#64748b';
  ctx.font = '10px sans-serif';

  for (let i = 0; i <= 3; i++) {
    const yVal = pad.top + (i * plotH / 3);
    const pVal = max - (i * (max - min) / 3);
    ctx.beginPath();
    ctx.moveTo(pad.left, yVal);
    ctx.lineTo(w - pad.right, yVal);
    ctx.stroke();
    ctx.fillText(`$${fmtPrice(pVal)}`, w - pad.right + 6, yVal + 3);
  }

  // Draw Price Line with subtle glow
  ctx.save();
  ctx.beginPath();
  ctx.strokeStyle = '#38bdf8';
  ctx.lineWidth = 2;
  ctx.shadowColor = '#38bdf8';
  ctx.shadowBlur = 6;

  for (let i = 0; i < candles.length; i++) {
    const xx = getX(i);
    const yy = getY(Number(candles[i].close));
    if (i === 0) ctx.moveTo(xx, yy);
    else ctx.lineTo(xx, yy);
  }
  ctx.stroke();
  ctx.restore();

  // Subtle Area Gradient Under Price Line
  ctx.beginPath();
  ctx.moveTo(getX(0), getY(Number(candles[0].close)));
  for (let i = 1; i < candles.length; i++) {
    ctx.lineTo(getX(i), getY(Number(candles[i].close)));
  }
  ctx.lineTo(getX(candles.length - 1), h - pad.bottom);
  ctx.lineTo(pad.left, h - pad.bottom);
  ctx.closePath();
  ctx.fillStyle = 'rgba(56, 189, 248, 0.04)';
  ctx.fill();

  // Draw Current Price Dashed Reference Line
  const lastPrice = Number(candles[candles.length - 1].close);
  const lastY = getY(lastPrice);
  ctx.save();
  ctx.beginPath();
  ctx.setLineDash([2, 3]);
  ctx.strokeStyle = 'rgba(56, 189, 248, 0.4)';
  ctx.lineWidth = 1;
  ctx.moveTo(pad.left, lastY);
  ctx.lineTo(w - pad.right, lastY);
  ctx.stroke();

  // Current Price Tag on Right Axis
  ctx.fillStyle = '#0284c7';
  ctx.fillRect(w - pad.right + 2, lastY - 9, 82, 18);
  ctx.fillStyle = '#ffffff';
  ctx.font = 'bold 10px sans-serif';
  ctx.fillText(`$${fmtPrice(lastPrice)}`, w - pad.right + 6, lastY + 4);
  ctx.restore();

  // Draw Trade Execution Levels (Entry, TP, SL) if position exists
  if (activePosition) {
    const drawLevel = (price, label, color) => {
      if (!price) return;
      const yL = getY(price);
      ctx.save();
      ctx.beginPath();
      ctx.setLineDash([4, 4]);
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.5;
      ctx.moveTo(pad.left, yL);
      ctx.lineTo(w - pad.right, yL);
      ctx.stroke();

      // Right label pill
      ctx.setLineDash([]);
      ctx.fillStyle = color;
      ctx.fillRect(w - pad.right + 2, yL - 8, 82, 16);
      ctx.fillStyle = '#070a12';
      ctx.font = 'bold 9px sans-serif';
      ctx.fillText(`${label}: $${fmtPrice(price)}`, w - pad.right + 5, yL + 4);
      ctx.restore();
    };

    drawLevel(activePosition.entry_price, 'ENTRY', '#eab308');
    drawLevel(activePosition.take_profit_price, 'TP', '#22c55e');
    drawLevel(activePosition.stop_loss_price, 'SL', '#f43f5e');
  }
}

// ── Predictions Feed Table & Filters ─────────────────────────────────────────
function setFeedFilter(filter) {
  state.activeFilter = filter;
  ['buysell', 'pending', 'done', 'all'].forEach(k => {
    const btn = document.getElementById(`tab_${k}`);
    if (btn) btn.classList.toggle('active', k.toUpperCase() === filter || (filter === 'BUYSELL' && k === 'buysell'));
  });
  renderFeedTable();
}

async function refreshPredictionsFeed() {
  const list = await fetchApi('/api/predictions/recent?limit=50');
  if (list && Array.isArray(list)) {
    state.predictions = list;
    renderFeedTable();
  }
}

function renderFeedTable() {
  const tbody = document.getElementById('feed_tbody');
  if (!tbody) return;

  if (!state.predictions.length) {
    tbody.innerHTML = '<tr><td colspan="9" class="text-center muted" style="padding: 24px;">Belum ada riwayat sinyal di database.</td></tr>';
    return;
  }

  let filtered = state.predictions;
  if (state.activeFilter === 'BUYSELL') {
    filtered = filtered.filter(r => ['BUY', 'SELL', 'UP', 'DOWN'].includes(r.prediction));
  } else if (state.activeFilter === 'PENDING') {
    filtered = filtered.filter(r => r.status === 'PENDING');
  } else if (state.activeFilter === 'DONE') {
    filtered = filtered.filter(r => r.status !== 'PENDING');
  }

  if (!filtered.length) {
    tbody.innerHTML = `<tr><td colspan="9" class="text-center muted" style="padding: 24px;">Tidak ada data untuk filter '${state.activeFilter}'.</td></tr>`;
    return;
  }

  tbody.innerHTML = filtered.map(r => {
    const isBuy = ['BUY', 'UP'].includes(r.prediction);
    const isSell = ['SELL', 'DOWN'].includes(r.prediction);
    const badgeCls = isBuy ? 'badge-buy' : isSell ? 'badge-sell' : 'badge-hold';
    const actionTxt = isBuy ? '🟢 BUY' : isSell ? '🔴 SELL' : '⚪ HOLD';

    let resCls = 'muted';
    let resTxt = r.status === 'PENDING' ? '⏳ BERJALAN' : '-';
    if (r.result === 'CORRECT' || (r.actual_direction && r.actual_direction.startsWith('TP_'))) {
      resCls = 'good';
      resTxt = '🎯 TAKE PROFIT';
    } else if (r.actual_direction && r.actual_direction.startsWith('BE_')) {
      resCls = 'accent-text';
      resTxt = '🛡️ BREAKEVEN';
    } else if (r.result === 'WRONG' || (r.actual_direction && (r.actual_direction.startsWith('SL_') || r.actual_direction.startsWith('NS_')))) {
      resCls = 'bad';
      resTxt = '❌ STOP LOSS';
    }

    const retVal = r.actual_return_percent !== null && r.actual_return_percent !== undefined
      ? `${r.actual_return_percent > 0 ? '+' : ''}${r.actual_return_percent.toFixed(2)}%`
      : (r.status === 'PENDING' ? '<span class="muted">Live</span>' : '-');

    return `
      <tr onclick="selectSymbol('${r.symbol}', true)">
        <td class="muted">${fmtTime(r.prediction_time)}</td>
        <td><strong>${r.symbol}</strong></td>
        <td><span class="badge ${badgeCls}">${actionTxt}</span></td>
        <td><b class="accent-text">${r.confidence ? r.confidence.toFixed(1) + '%' : '-'}</b></td>
        <td>${fmtPrice(r.entry_price)}</td>
        <td><span class="status-pill status-${(r.status || '').toLowerCase()}">${r.status}</span></td>
        <td class="${resCls}"><strong>${resTxt}</strong></td>
        <td class="${resCls}"><strong>${retVal}</strong></td>
        <td class="reason-col" title="${escapeHtml(r.reason_summary)}">${escapeHtml(r.reason_summary || '-')}</td>
      </tr>
    `;
  }).join('');
}

// ── Gemini & Copilot Chat Controller ─────────────────────────────────────────
async function handleChatSubmit(e) {
  e.preventDefault();
  const input = document.getElementById('chat_input');
  const msg = input.value.trim();
  if (!msg || state.isChatBusy) return;

  input.value = '';
  await sendChatMessage(msg);
}

function sendQuickPrompt(promptText) {
  if (state.isChatBusy) return;
  sendChatMessage(promptText);
}

async function sendChatMessage(msg) {
  state.isChatBusy = true;
  appendMessage('Anda', msg, false);

  const loadingId = appendLoadingBubble();
  const sendBtn = document.getElementById('chat_send_btn');
  if (sendBtn) sendBtn.disabled = true;

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: msg }),
    });
    const data = await res.json();
    removeLoadingBubble(loadingId);
    appendMessage('AEGIS Copilot', data.reply || 'Maaf, tidak ada respon.', true);
  } catch (err) {
    removeLoadingBubble(loadingId);
    appendMessage('AEGIS Copilot', `Terjadi kendala koneksi: ${err.message}`, true);
  } finally {
    state.isChatBusy = false;
    if (sendBtn) sendBtn.disabled = false;
  }
}

function appendMessage(sender, text, isAi) {
  const container = document.getElementById('chat_messages');
  if (!container) return;

  const bubble = document.createElement('div');
  bubble.className = `chat-bubble ${isAi ? 'bubble-ai' : 'bubble-user'}`;
  bubble.innerHTML = `
    <div class="bubble-sender">${escapeHtml(sender)}</div>
    <div class="bubble-content">${formatMarkdown(text)}</div>
  `;
  container.appendChild(bubble);
  container.scrollTop = container.scrollHeight;
}

function appendLoadingBubble() {
  const container = document.getElementById('chat_messages');
  if (!container) return null;

  const id = 'loading_' + Date.now();
  const bubble = document.createElement('div');
  bubble.id = id;
  bubble.className = 'chat-bubble bubble-ai bubble-loading';
  bubble.innerHTML = `
    <div class="bubble-sender">AEGIS Copilot</div>
    <div class="bubble-content"><span class="typing-indicator">Menganalisis data...</span></div>
  `;
  container.appendChild(bubble);
  container.scrollTop = container.scrollHeight;
  return id;
}

function removeLoadingBubble(id) {
  if (!id) return;
  const el = document.getElementById(id);
  if (el) el.remove();
}

// ── Realtime Synchronization Loop ────────────────────────────────────────────
async function syncAll(forceChart = false) {
  await Promise.all([
    refreshMetricsAndOrders(),
    refreshPredictionsFeed(),
    refreshChart(forceChart),
  ]);
}

// ── Startup & Lifecycle ──────────────────────────────────────────────────────
async function startup() {
  await initSymbols();
  await refreshAiBadge();
  await syncAll(true);

  // Auto-resize canvas on window resize
  window.addEventListener('resize', () => {
    if (state.candles.length > 0) {
      drawChartWithLevels(state.candles);
    }
  });

  // Continuous 2.5-second Realtime Loop
  setInterval(() => {
    if (!document.hidden && state.isPolling) {
      syncAll(false);
    }
  }, state.pollIntervalMs);
}

startup();
