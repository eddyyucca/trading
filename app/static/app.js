const state = {
  symbols: [],
  selectedSymbol: null,
  positions: [],
  predictions: [],
  candles: [],
};

async function fetchJson(url, options = {}) {
  const res = await fetch(url, options);
  const raw = await res.text();
  let data;
  try {
    data = raw ? JSON.parse(raw) : {};
  } catch {
    throw new Error(`HTTP ${res.status} ${res.statusText}\n${raw}`);
  }
  if (!res.ok) {
    throw new Error(JSON.stringify(data, null, 2));
  }
  return data;
}

async function apiPost(url) {
  const box = document.getElementById('last_action');
  box.textContent = 'Memproses ' + url + ' ...';
  try {
    const data = await fetchJson(url, { method: 'POST' });
    box.textContent = JSON.stringify(data, null, 2);
    await refreshAll();
  } catch (err) {
    box.textContent = 'ERROR: ' + err;
  }
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[ch]));
}

function signalSide(prediction) {
  if (['BUY', 'UP'].includes(prediction)) return 'BUY';
  if (['SELL', 'DOWN'].includes(prediction)) return 'SELL';
  return 'HOLD';
}

function signalClass(prediction) {
  const side = signalSide(prediction);
  if (side === 'BUY') return 'up';
  if (side === 'SELL') return 'down';
  return 'neutral-badge';
}

function badge(text, cls) {
  return `<span class="badge ${cls}">${escapeHtml(text || '-')}</span>`;
}

function fmtTime(value) {
  if (!value) return '-';
  return value.replace('T', ' ').slice(0, 19);
}

function fmtPrice(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-';
  const number = Number(value);
  if (number >= 1000) return number.toFixed(2);
  if (number >= 1) return number.toFixed(4);
  return number.toFixed(6);
}

function fmtUsd(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '-';
  const sign = Number(value) > 0 ? '+' : '';
  return `${sign}$${Number(value).toFixed(4)}`;
}

function riskClass(status) {
  if (status === 'NEAR_STOP' || status === 'STOP_LOSS_HIT') return 'wrong';
  if (status === 'TAKE_PROFIT_HIT' || status === 'IN_PROFIT') return 'correct';
  return 'pending';
}

function setMoneyMetric(id, value) {
  const element = document.getElementById(id);
  element.textContent = fmtUsd(value);
  element.className = Number(value) >= 0 ? 'buy-text' : 'sell-text';
}

async function refreshStats() {
  const [s, history] = await Promise.all([
    fetchJson('/api/stats/summary'),
    fetchJson('/api/history/status'),
  ]);
  document.getElementById('total_predictions').textContent = s.total_predictions;
  document.getElementById('pending').textContent = s.pending;
  document.getElementById('correct').textContent = s.correct;
  document.getElementById('wrong').textContent = s.wrong;
  document.getElementById('neutral').textContent = s.neutral;
  document.getElementById('directional_accuracy').textContent = s.directional_accuracy + '%';
  document.getElementById('history_ready').textContent = history.ready ? 'READY' : 'LOADING';
  document.getElementById('history_ready').className = history.ready ? 'buy-text' : 'hold-text';
  setMoneyMetric('realized_pnl', s.realized_pnl_usd);
  setMoneyMetric('unrealized_pnl', s.unrealized_pnl_usd);
  setMoneyMetric('total_pnl', s.total_pnl_usd);
  document.getElementById('tp_sl_count').textContent = `${s.take_profit} / ${s.stop_loss + s.near_stop}`;
  document.getElementById('futures_mode').textContent = s.futures_mode ? `${s.futures_leverage}x` : 'OFF';
  document.getElementById('futures_mode').className = s.futures_mode ? 'buy-text' : 'hold-text';
  document.getElementById('sample_warning').classList.toggle('hidden', !s.sample_warning);
}

async function refreshSymbols() {
  const rows = await fetchJson('/api/symbols');
  state.symbols = rows.map(row => row.symbol);
  const previousSymbol = state.selectedSymbol;
  if (!state.selectedSymbol && state.symbols.length) {
    const firstPosition = state.positions[0]?.symbol;
    state.selectedSymbol = firstPosition || state.symbols[0];
  }

  const select = document.getElementById('symbol_select');
  const current = select.value;
  select.innerHTML = state.symbols.map(symbol => (
    `<option value="${escapeHtml(symbol)}">${escapeHtml(symbol)}</option>`
  )).join('');
  select.value = state.selectedSymbol || current || '';
  if (state.selectedSymbol !== previousSymbol) {
    renderPositions();
    renderSelectedPosition();
  }
}

async function refreshPositions() {
  state.positions = await fetchJson('/api/positions/active');
  document.getElementById('positions_count').textContent = `${state.positions.length} posisi`;
  renderPositions();
  renderSelectedPosition();
}

function renderPositions() {
  const box = document.getElementById('active_positions');
  if (!state.positions.length) {
    box.innerHTML = '<div class="empty-state">Belum ada posisi BUY/SELL aktif. Sistem akan menampilkan posisi setelah sinyal lolos filter confidence.</div>';
    return;
  }

  box.innerHTML = state.positions.map(position => {
    const active = position.symbol === state.selectedSymbol ? ' active' : '';
    const cls = position.side === 'BUY' ? 'up' : 'down';
    const horizons = [...new Set(position.horizons)].sort((a, b) => a - b).join('m, ') + 'm';
    return `<button class="position-item${active}" onclick="selectSymbol('${escapeHtml(position.symbol)}')">
      <span class="position-row"><strong>${escapeHtml(position.symbol)}</strong>${badge(position.side, cls)}</span>
      <span class="position-row"><small>Status</small>${badge(position.risk_status, riskClass(position.risk_status))}</span>
      <span class="position-row"><small>Margin / Lev</small><b>$${Number(position.margin_usd).toFixed(2)} / ${position.leverage}x</b></span>
      <span class="position-row"><small>Notional</small><b>$${Number(position.notional_usd).toFixed(2)}</b></span>
      <span class="position-row"><small>Entry</small><b>${fmtPrice(position.entry_price)}</b></span>
      <span class="position-row"><small>TP</small><b>${fmtPrice(position.take_profit_price)} / ${fmtUsd(position.take_profit_pnl_usd)}</b></span>
      <span class="position-row"><small>SL</small><b>${fmtPrice(position.stop_loss_price)} / ${fmtUsd(position.stop_loss_pnl_usd)}</b></span>
      <span class="position-row"><small>Liq</small><b>${fmtPrice(position.liquidation_price)}</b></span>
      <span class="position-row"><small>PnL</small><b class="${position.unrealized_pnl_usd >= 0 ? 'buy-text' : 'sell-text'}">${fmtUsd(position.unrealized_pnl_usd)}</b></span>
      <span class="position-row"><small>Horizon</small><b>${escapeHtml(horizons)}</b></span>
    </button>`;
  }).join('');
}

function renderSelectedPosition() {
  const position = state.positions.find(item => item.symbol === state.selectedSymbol);
  document.getElementById('selected_side').textContent = position?.side || 'HOLD';
  document.getElementById('selected_side').className = position?.side === 'BUY' ? 'buy-text' : position?.side === 'SELL' ? 'sell-text' : 'hold-text';
  document.getElementById('selected_margin').textContent = position ? `$${Number(position.margin_usd).toFixed(2)} / ${position.leverage}x` : '-';
  document.getElementById('selected_notional').textContent = position ? `$${Number(position.notional_usd).toFixed(2)}` : '-';
  document.getElementById('selected_entry').textContent = fmtPrice(position?.entry_price);
  document.getElementById('selected_tp').textContent = position ? `${fmtPrice(position.take_profit_price)} (${fmtUsd(position.take_profit_pnl_usd)})` : '-';
  document.getElementById('selected_sl').textContent = position ? `${fmtPrice(position.stop_loss_price)} (${fmtUsd(position.stop_loss_pnl_usd)})` : '-';
  document.getElementById('selected_liq').textContent = fmtPrice(position?.liquidation_price);
  document.getElementById('selected_pnl').textContent = position ? fmtUsd(position.unrealized_pnl_usd) : '-';
  document.getElementById('selected_pnl').className = position?.unrealized_pnl_usd >= 0 ? 'buy-text' : position ? 'sell-text' : 'hold-text';
  document.getElementById('selected_confidence').textContent = position ? `${position.confidence}%` : '-';
  document.getElementById('selected_due').textContent = position ? fmtTime(position.next_due_time).slice(11, 19) : '-';
}

function selectSymbol(symbol) {
  state.selectedSymbol = symbol;
  document.getElementById('symbol_select').value = symbol;
  renderPositions();
  renderSelectedPosition();
  refreshChart();
}

async function refreshChart() {
  if (!state.selectedSymbol) return;
  const symbol = state.selectedSymbol;
  const [candles, predictions] = await Promise.all([
    fetchJson(`/api/candles/${symbol}/1m?limit=160`),
    fetchJson(`/api/predictions/recent?symbol=${symbol}&limit=80`),
  ]);
  state.candles = candles;
  state.predictions = predictions;
  document.getElementById('chart_status').textContent = `${symbol} - ${candles.length} candle - ${new Date().toLocaleTimeString()}`;
  drawChart(document.getElementById('price_chart'), candles, predictions);
}

function drawChart(canvas, candles, predictions) {
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(rect.width * dpr));
  canvas.height = Math.max(1, Math.floor(rect.height * dpr));
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const width = rect.width;
  const height = rect.height;
  ctx.clearRect(0, 0, width, height);

  if (!candles.length) {
    ctx.fillStyle = '#a7a99b';
    ctx.font = '14px Bahnschrift, Segoe UI, sans-serif';
    ctx.fillText('Belum ada candle. Klik Backfill atau tunggu collector berjalan.', 24, 42);
    return;
  }

  const pad = { left: 62, right: 18, top: 18, bottom: 34 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const lows = candles.map(c => Number(c.low));
  const highs = candles.map(c => Number(c.high));
  let min = Math.min(...lows);
  let max = Math.max(...highs);
  const span = max - min || max * 0.01 || 1;
  min -= span * 0.08;
  max += span * 0.08;

  const x = index => pad.left + (candles.length === 1 ? plotW : index * plotW / (candles.length - 1));
  const y = price => pad.top + (max - price) * plotH / (max - min);

  ctx.strokeStyle = 'rgba(242, 241, 232, 0.08)';
  ctx.lineWidth = 1;
  ctx.font = '11px Bahnschrift, Segoe UI, sans-serif';
  ctx.fillStyle = '#a7a99b';
  for (let i = 0; i <= 4; i += 1) {
    const yy = pad.top + i * plotH / 4;
    const price = max - i * (max - min) / 4;
    ctx.beginPath();
    ctx.moveTo(pad.left, yy);
    ctx.lineTo(width - pad.right, yy);
    ctx.stroke();
    ctx.fillText(fmtPrice(price), 8, yy + 4);
  }

  const candleW = Math.max(3, Math.min(9, plotW / candles.length * 0.55));
  candles.forEach((candle, index) => {
    const open = Number(candle.open);
    const close = Number(candle.close);
    const high = Number(candle.high);
    const low = Number(candle.low);
    const xx = x(index);
    const color = close >= open ? '#30d158' : '#ff453a';
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.globalAlpha = 0.9;
    ctx.beginPath();
    ctx.moveTo(xx, y(high));
    ctx.lineTo(xx, y(low));
    ctx.stroke();
    const top = Math.min(y(open), y(close));
    const bodyH = Math.max(2, Math.abs(y(open) - y(close)));
    ctx.fillRect(xx - candleW / 2, top, candleW, bodyH);
    ctx.globalAlpha = 1;
  });

  const candleTimes = candles.map(c => new Date(c.time).getTime());
  predictions.slice().reverse().forEach(prediction => {
    const side = signalSide(prediction.prediction);
    if (!['BUY', 'SELL'].includes(side)) return;
    const targetTime = new Date(prediction.prediction_time).getTime();
    let bestIndex = 0;
    let bestDistance = Number.POSITIVE_INFINITY;
    candleTimes.forEach((time, index) => {
      const distance = Math.abs(time - targetTime);
      if (distance < bestDistance) {
        bestDistance = distance;
        bestIndex = index;
      }
    });
    const xx = x(bestIndex);
    const yy = y(Number(prediction.entry_price));
    ctx.fillStyle = side === 'BUY' ? '#30d158' : '#ff453a';
    ctx.strokeStyle = '#10110f';
    ctx.lineWidth = 2;
    ctx.beginPath();
    if (side === 'BUY') {
      ctx.moveTo(xx, yy - 12);
      ctx.lineTo(xx - 7, yy + 3);
      ctx.lineTo(xx + 7, yy + 3);
    } else {
      ctx.moveTo(xx, yy + 12);
      ctx.lineTo(xx - 7, yy - 3);
      ctx.lineTo(xx + 7, yy - 3);
    }
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
  });

  const position = state.positions.find(item => item.symbol === state.selectedSymbol);
  if (position) {
    [
      ['TP', Number(position.take_profit_price), '#30d158'],
      ['SL', Number(position.stop_loss_price), '#ff453a'],
    ].forEach(([label, price, color]) => {
      const yy = y(price);
      if (yy < pad.top || yy > height - pad.bottom) return;
      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.lineWidth = 1.5;
      ctx.setLineDash([6, 5]);
      ctx.beginPath();
      ctx.moveTo(pad.left, yy);
      ctx.lineTo(width - pad.right, yy);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillText(`${label} ${fmtPrice(price)}`, width - pad.right - 92, yy - 6);
    });
  }

  const last = candles[candles.length - 1];
  ctx.fillStyle = '#f2f1e8';
  ctx.font = '13px Bahnschrift, Segoe UI, sans-serif';
  ctx.fillText(`Last ${fmtPrice(last.close)}`, pad.left, 16);
}

async function refreshPredictions() {
  const rows = await fetchJson('/api/predictions/recent?limit=120');
  const tbody = document.getElementById('prediction_rows');
  tbody.innerHTML = rows.map(r => {
    const resultClass = r.result === 'CORRECT' ? 'correct' : r.result === 'WRONG' ? 'wrong' : r.result === 'NEUTRAL' ? 'neutral-badge' : 'pending';
    return `<tr onclick="selectSymbol('${escapeHtml(r.symbol)}')">
      <td>${escapeHtml(fmtTime(r.prediction_time))}</td>
      <td>${escapeHtml(r.symbol)}</td>
      <td>${badge(signalSide(r.prediction), signalClass(r.prediction))}</td>
      <td>${r.confidence}%</td>
      <td>${r.score_total}</td>
      <td>${r.horizon_minutes}m</td>
      <td>${badge(r.status, r.status === 'PENDING' ? 'pending' : 'correct')}</td>
      <td>${badge(r.actual_direction || '-', (r.actual_direction?.startsWith('SL_') || r.actual_direction?.startsWith('NS_')) ? 'wrong' : r.actual_direction?.startsWith('TP_') ? 'correct' : 'pending')}</td>
      <td>${badge(r.result || '-', resultClass)}</td>
      <td>${r.actual_return_percent ?? '-'}</td>
      <td>${escapeHtml(r.reason_summary || '-')}</td>
    </tr>`;
  }).join('');
  document.getElementById('last_refresh').textContent = 'Last refresh: ' + new Date().toLocaleTimeString();
}

async function refreshAll() {
  await refreshStats();
  await refreshPositions();
  await refreshSymbols();
  await Promise.all([refreshChart(), refreshPredictions()]);
}

document.getElementById('symbol_select').addEventListener('change', event => {
  selectSymbol(event.target.value);
});

window.addEventListener('resize', () => {
  drawChart(document.getElementById('price_chart'), state.candles, state.predictions);
});

refreshAll();
setInterval(refreshAll, 5000);
