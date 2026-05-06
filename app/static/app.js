async function apiPost(url) {
  const box = document.getElementById('last_action');
  box.textContent = 'Memproses ' + url + ' ...';
  try {
    const res = await fetch(url, { method: 'POST' });
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
    box.textContent = JSON.stringify(data, null, 2);
    await refreshAll();
  } catch (err) {
    box.textContent = 'ERROR: ' + err;
  }
}

function badge(text, cls) {
  return `<span class="badge ${cls}">${text || '-'}</span>`;
}

async function refreshStats() {
  const res = await fetch('/api/stats/summary');
  const s = await res.json();
  document.getElementById('total_predictions').textContent = s.total_predictions;
  document.getElementById('pending').textContent = s.pending;
  document.getElementById('correct').textContent = s.correct;
  document.getElementById('wrong').textContent = s.wrong;
  document.getElementById('neutral').textContent = s.neutral;
  document.getElementById('directional_accuracy').textContent = s.directional_accuracy + '%';
  document.getElementById('sample_warning').classList.toggle('hidden', !s.sample_warning);
}

async function refreshPredictions() {
  const res = await fetch('/api/predictions/recent?limit=80');
  const rows = await res.json();
  const tbody = document.getElementById('prediction_rows');
  tbody.innerHTML = rows.map(r => {
    const predClass = r.prediction === 'UP' ? 'up' : r.prediction === 'DOWN' ? 'down' : 'neutral-badge';
    const resultClass = r.result === 'CORRECT' ? 'correct' : r.result === 'WRONG' ? 'wrong' : r.result === 'NEUTRAL' ? 'neutral-badge' : 'pending';
    return `<tr>
      <td>${r.prediction_time.replace('T', ' ')}</td>
      <td>${r.symbol}</td>
      <td>${badge(r.prediction, predClass)}</td>
      <td>${r.confidence}%</td>
      <td>${r.score_total}</td>
      <td>${r.horizon_minutes}m</td>
      <td>${badge(r.status, r.status === 'PENDING' ? 'pending' : 'correct')}</td>
      <td>${badge(r.result || '-', resultClass)}</td>
      <td>${r.actual_return_percent ?? '-'}</td>
      <td>${r.reason_summary || '-'}</td>
    </tr>`;
  }).join('');
  document.getElementById('last_refresh').textContent = 'Last refresh: ' + new Date().toLocaleTimeString();
}

async function refreshAll() {
  await Promise.all([refreshStats(), refreshPredictions()]);
}

refreshAll();
setInterval(refreshAll, 5000);
