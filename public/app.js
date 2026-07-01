let payload = null;
let latestRows = [];

const FACE_VALUE_PER_LOT = 1000;
const BOARD_LOT_SHARES = 100;

const $ = (id) => document.getElementById(id);

function todayCnString() {
  const now = new Date();
  const utc = now.getTime() + now.getTimezoneOffset() * 60000;
  const cn = new Date(utc + 8 * 3600000);
  return cn.toISOString().slice(0, 10);
}

function parseDate(s) {
  if (!s) return null;
  const d = new Date(`${s}T00:00:00+08:00`);
  return Number.isNaN(d.getTime()) ? null : d;
}

function daysBetween(a, b) {
  return Math.round((parseDate(a) - parseDate(b)) / 86400000);
}

function addDays(iso, days) {
  const d = parseDate(iso);
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

function fmtNum(v, digits = 2) {
  if (v === null || v === undefined || v === '' || Number.isNaN(Number(v))) return '-';
  return Number(v).toLocaleString('zh-CN', { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

function fmtInt(v) {
  if (v === null || v === undefined || v === '' || Number.isNaN(Number(v))) return '-';
  return Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 });
}

function esc(s) {
  return String(s ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
}

function badge(status) {
  let cls = 'old';
  if (status === '可抢权') cls = 'active';
  else if (status === '等待股权登记') cls = 'wait';
  else if (status === '已过登记日') cls = 'late';
  return `<span class="badge ${cls}">${esc(status || '-')}</span>`;
}

function ratingBonus(rating) {
  const table = {
    'AAA': 5.0,
    'AA+': 3.5,
    'AA': 2.2,
    'AA-': 0.8,
    'A+': -0.8,
    'A': -1.8,
    'A-': -3.0,
    'BBB+': -5.0,
    'BBB': -7.0,
  };
  return table[String(rating || '').toUpperCase()] || 0;
}

function ceilToBoardLot(shares) {
  if (!shares || shares <= 0) return 0;
  return Math.ceil(shares / BOARD_LOT_SHARES) * BOARD_LOT_SHARES;
}

function getParams() {
  return {
    days: Math.max(1, Number($('days').value || 45)),
    targetLots: Math.max(1, Number($('targetLots').value || 1)),
    expectedPrice: Math.max(0, Number($('expectedPrice').value || 0)),
    stockDropPct: Math.max(0, Number($('stockDropPct').value || 0)),
    onlyActive: $('onlyActive').checked,
  };
}

function statusOf(r, today) {
  const record = r.record_date;
  const subscribe = r.subscribe_date;
  const listing = r.listing_date;

  if (record && today > record && (!subscribe || today <= subscribe)) return '已过登记日';
  if (subscribe && today > subscribe && (!listing || today <= listing)) return '已申购待上市';
  if (listing && today > listing) return '已上市/历史';
  if (record && today < record) return '等待股权登记';
  return '可抢权';
}

function computeRows() {
  if (!payload) return [];
  const p = getParams();
  const today = todayCnString();
  const end = addDays(today, p.days);
  const rows = [];

  for (const r of payload.rows || []) {
    const basisDate = r.record_date || r.subscribe_date;
    if (!basisDate) continue;

    if (p.onlyActive) {
      if (!(today <= basisDate && basisDate <= end)) continue;
    } else {
      const start = addDays(today, -10);
      if (!(start <= basisDate && basisDate <= end)) continue;
    }

    const status = statusOf(r, today);
    if (p.onlyActive && !['可抢权', '等待股权登记'].includes(status)) continue;

    const allot = Number(r.allot_per_share || 0);
    const stockPrice = Number(r.stock_price || 0);
    const expPrice = p.expectedPrice > 0 ? p.expectedPrice : Number(r.expected_price || 0);
    const needShares = allot > 0 ? ceilToBoardLot(p.targetLots * FACE_VALUE_PER_LOT / allot) : 0;
    const allocatedFace = needShares * allot;
    const stockCost = needShares > 0 && stockPrice > 0 ? needShares * stockPrice : 0;
    const expectedProfit = expPrice > 0 ? p.targetLots * FACE_VALUE_PER_LOT * (expPrice - 100) / 100 : 0;
    const safety = stockCost > 0 ? expectedProfit / stockCost * 100 : 0;
    const netProfit = expectedProfit - stockCost * p.stockDropPct / 100;
    const netYield = stockCost > 0 ? netProfit / stockCost * 100 : 0;
    const daysToRecord = daysBetween(basisDate, today);

    let score = safety * 10;
    const cv = Number(r.convert_value || 0);
    score += Math.max(0, cv - 100) * 0.10;
    score += ratingBonus(r.rating) * 0.35;
    if (daysToRecord >= 0) score -= daysToRecord * 0.08;

    rows.push({
      ...r,
      status,
      target_lots: p.targetLots,
      need_shares: needShares,
      allocated_face_est: Number(allocatedFace.toFixed(2)),
      stock_cost: Number(stockCost.toFixed(2)),
      expected_price_used: expPrice > 0 ? Number(expPrice.toFixed(2)) : null,
      expected_bond_profit: Number(expectedProfit.toFixed(2)),
      safety_cushion_pct: Number(safety.toFixed(2)),
      assumed_stock_drop_pct: p.stockDropPct,
      net_profit_after_drop: Number(netProfit.toFixed(2)),
      net_yield_after_drop_pct: Number(netYield.toFixed(2)),
      days_to_record: daysToRecord,
      score: Number(score.toFixed(2)),
    });
  }

  rows.sort((a, b) => {
    const d = (a.days_to_record ?? 9999) - (b.days_to_record ?? 9999);
    if (d !== 0) return d;
    return (b.score || 0) - (a.score || 0);
  });
  return rows;
}

function rowHtml(r) {
  const stockChange = r.stock_change_pct === null || r.stock_change_pct === undefined || r.stock_change_pct === '' ? '' : ` / ${Number(r.stock_change_pct).toFixed(2)}%`;
  const safetyCls = Number(r.safety_cushion_pct || 0) >= 2 ? 'green' : (Number(r.safety_cushion_pct || 0) >= 1 ? 'orange' : 'red');
  const netCls = Number(r.net_profit_after_drop || 0) >= 0 ? 'green' : 'red';
  return `
    <tr>
      <td>${badge(r.status)}</td>
      <td><div class="name-main">${esc(r.bond_name || '-')}</div><div class="name-sub">${esc(r.bond_code || '')}</div></td>
      <td><div class="name-main">${esc(r.stock_name || '-')}</div><div class="name-sub">${esc(r.stock_code || '')} / ¥${fmtNum(r.stock_price, 2)}${esc(stockChange)}</div></td>
      <td>${esc(r.record_date || '-')}</td>
      <td>${esc(r.subscribe_date || '-')}</td>
      <td>${esc(r.allot_code || '-')}</td>
      <td>${fmtNum(r.allot_per_share, 4)}</td>
      <td><b>${fmtInt(r.need_shares)}</b></td>
      <td>¥${fmtNum(r.stock_cost, 2)}</td>
      <td>${fmtNum(r.convert_value, 2)}</td>
      <td>${esc(r.rating || '-')}</td>
      <td>¥${fmtNum(r.expected_price_used, 2)}</td>
      <td class="green">¥${fmtNum(r.expected_bond_profit, 2)}</td>
      <td class="${safetyCls}">${fmtNum(r.safety_cushion_pct, 2)}%</td>
      <td class="${netCls}">¥${fmtNum(r.net_profit_after_drop, 2)} / ${fmtNum(r.net_yield_after_drop_pct, 2)}%</td>
      <td>${fmtNum(r.score, 2)}</td>
      <td>${esc(r.remark || '')}</td>
    </tr>`;
}

function render() {
  latestRows = computeRows();
  const tbody = document.querySelector('#bondTable tbody');
  const today = todayCnString();
  $('summary').textContent = `今天 ${today}，共 ${latestRows.length} 条结果；源数据 ${payload?.rows?.length || 0} 条`;
  $('lastRefresh').textContent = payload?.build_time ? `数据生成：${payload.build_time}` : '暂无生成时间';

  if (payload?.errors?.length) {
    $('errorBox').textContent = payload.errors.join('\n');
    $('errorBox').classList.remove('hidden');
  } else {
    $('errorBox').classList.add('hidden');
  }

  tbody.innerHTML = latestRows.length
    ? latestRows.map(rowHtml).join('')
    : `<tr><td colspan="17" style="text-align:center;color:#667085;padding:36px;">暂无未过股权登记日的近期可转债。可以取消“只看未过登记日”，或扩大未来天数。</td></tr>`;
}

async function loadData() {
  $('summary').textContent = '加载中...';
  $('errorBox').classList.add('hidden');
  try {
    const res = await fetch(`./data/bonds_latest.json?t=${Date.now()}`, { cache: 'no-store' });
    if (!res.ok) throw new Error(`读取 data/bonds_latest.json 失败：HTTP ${res.status}`);
    payload = await res.json();
    render();
  } catch (e) {
    $('summary').textContent = '加载失败';
    $('errorBox').textContent = String(e.message || e);
    $('errorBox').classList.remove('hidden');
  }
}

function exportCsv() {
  const headers = [
    '状态','转债代码','转债名称','正股代码','正股简称','股权登记日','申购日','配售码','每股获配额','买入股数','正股金额','转股价值','信用评级','预估上市价','转债收益','安全垫%','综合收益','综合收益率%','排序分','备注'
  ];
  const lines = [headers.join(',')];
  for (const r of latestRows) {
    const arr = [
      r.status, r.bond_code, r.bond_name, r.stock_code, r.stock_name, r.record_date, r.subscribe_date, r.allot_code,
      r.allot_per_share, r.need_shares, r.stock_cost, r.convert_value, r.rating, r.expected_price_used,
      r.expected_bond_profit, r.safety_cushion_pct, r.net_profit_after_drop, r.net_yield_after_drop_pct, r.score, r.remark
    ].map(v => `"${String(v ?? '').replace(/"/g, '""')}"`);
    lines.push(arr.join(','));
  }
  const blob = new Blob(['\ufeff' + lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `可转债抢权配售_${new Date().toISOString().slice(0,10)}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

$('queryBtn').addEventListener('click', render);
$('reloadBtn').addEventListener('click', loadData);
$('exportBtn').addEventListener('click', exportCsv);
['days','targetLots','expectedPrice','stockDropPct','onlyActive'].forEach(id => {
  $(id).addEventListener('change', render);
});

loadData();
