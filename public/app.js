let payload = null;
let latestRows = [];
let allComputedRows = [];

const FACE_VALUE_PER_LOT = 1000;
const BOARD_LOT_SHARES = 100;
const MATRIX_PRICES = [105, 110, 115, 120, 125, 130];
const MATRIX_DROPS = [0, 1, 2, 3, 5, 8];

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

function addDays(iso, days) {
  const d = parseDate(iso);
  if (!d) return iso;
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

function daysBetween(a, b) {
  if (!a || !b) return null;
  const da = parseDate(a);
  const db = parseDate(b);
  if (!da || !db) return null;
  return Math.round((da - db) / 86400000);
}

function fmtNum(v, digits = 2) {
  if (v === null || v === undefined || v === '' || Number.isNaN(Number(v))) return '-';
  return Number(v).toLocaleString('zh-CN', { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

function fmtCompact(v, digits = 1) {
  if (v === null || v === undefined || v === '' || Number.isNaN(Number(v))) return '-';
  const n = Number(v);
  if (Math.abs(n) >= 100000000) return `${fmtNum(n / 100000000, digits)}亿`;
  if (Math.abs(n) >= 10000) return `${fmtNum(n / 10000, digits)}万`;
  return fmtNum(n, digits);
}

function fmtInt(v) {
  if (v === null || v === undefined || v === '' || Number.isNaN(Number(v))) return '-';
  return Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 });
}

function esc(s) {
  return String(s ?? '').replace(/[&<>'"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[c]));
}

function clamp(n, min, max) {
  return Math.max(min, Math.min(max, n));
}

function ceilToBoardLot(shares) {
  if (!shares || shares <= 0) return 0;
  return Math.ceil(shares / BOARD_LOT_SHARES) * BOARD_LOT_SHARES;
}

function badge(status) {
  let cls = 'old';
  if (status === '可抢权') cls = 'active';
  else if (status === '等待股权登记') cls = 'wait';
  else if (status === '未来待发行') cls = 'future';
  else if (status === '已过登记日' || status === '已申购待上市') cls = 'late';
  return `<span class="badge ${cls}">${esc(status || '-')}</span>`;
}

function gradeHtml(grade) {
  const cls = String(grade || 'D').toLowerCase();
  return `<span class="grade grade-${cls}">${esc(grade || 'D')}</span>`;
}

function riskHtml(risk) {
  const key = risk === '低' ? 'low' : (risk === '中' ? 'mid' : 'high');
  return `<span class="risk-tag risk-${key}">${esc(risk || '高')}</span>`;
}

function ratingScore(rating) {
  const table = {
    'AAA': 10,
    'AA+': 8,
    'AA': 6,
    'AA-': 4,
    'A+': 2,
    'A': 0,
    'A-': -3,
    'BBB+': -6,
    'BBB': -8,
  };
  return table[String(rating || '').toUpperCase()] ?? 0;
}

function getParams() {
  return {
    days: Math.max(1, Number($('days').value || 45)),
    targetLots: Math.max(1, Number($('targetLots').value || 1)),
    expectedPrice: Math.max(0, Number($('expectedPrice').value || 0)),
    stockDropPct: Math.max(0, Number($('stockDropPct').value || 0)),
    minSafety: Number($('minSafety').value || 0),
    maxCost: Math.max(0, Number($('maxCost').value || 0)),
    stageFilter: $('stageFilter').value,
    sortBy: $('sortBy').value,
    keyword: String($('keyword').value || '').trim().toLowerCase(),
  };
}

function statusOf(r, today) {
  const record = r.record_date;
  const subscribe = r.subscribe_date;
  const listing = r.listing_date;
  const allot = Number(r.allot_per_share || 0);

  if (listing && today > listing) return '已上市/历史';
  if (subscribe && today > subscribe) return '已申购待上市';
  if (record && today > record) return '已过登记日';
  if (record && today === record) return '可抢权';
  if (record && today < record) return '等待股权登记';
  if (subscribe || allot > 0 || r.bond_code) return '未来待发行';
  return '资料不全';
}

function riskReasons(r, m) {
  const reasons = [];
  const safety = Number(m.safety_cushion_pct || 0);
  const stockChange = Number(r.stock_change_pct || 0);
  const change60d = Number(r.stock_change_60d_pct || 0);
  const cv = Number(r.convert_value || 0);
  const issueSize = Number(r.issue_size_yi || 0);
  const rating = String(r.rating || '').toUpperCase();

  if (!r.record_date) reasons.push('暂未拿到明确股权登记日，需以公告为准。');
  if (!r.allot_per_share) reasons.push('每股获配额缺失，买入股数无法可靠计算。');
  if (!r.stock_price) reasons.push('正股行情缺失，安全垫和资金占用可能为空。');
  if (safety < 1) reasons.push('安全垫偏低，正股小幅回撤就可能覆盖转债收益。');
  if (stockChange >= 5) reasons.push('正股当日涨幅较大，抢权买入容易遇到回落。');
  if (change60d >= 35) reasons.push('正股近 60 日涨幅较高，注意阶段性拥挤风险。');
  if (cv && cv < 95) reasons.push('转股价值偏低，转债上市溢价依赖情绪更强。');
  if (issueSize >= 20) reasons.push('发行规模较大，上市弹性可能弱于小规模转债。');
  if (rating && !['AAA', 'AA+', 'AA', 'AA-'].includes(rating)) reasons.push('信用评级一般，估值可能受折价影响。');
  if (!reasons.length) reasons.push('基础指标暂未触发明显风险，但仍需核对公告和正股走势。');
  return reasons;
}

function gradeAndRisk(r, m) {
  let score = 50;
  const safety = Number(m.safety_cushion_pct || 0);
  const netYield = Number(m.net_yield_after_drop_pct || 0);
  const cv = Number(r.convert_value || 0);
  const stockChange = Number(r.stock_change_pct || 0);
  const change60d = Number(r.stock_change_60d_pct || 0);
  const issueSize = Number(r.issue_size_yi || 0);
  const cost = Number(m.stock_cost || 0);
  const daysToRecord = m.days_to_record;

  score += clamp(safety * 7, -20, 35);
  score += clamp(netYield * 6, -18, 22);
  if (cv) score += clamp((cv - 100) * 0.6, -10, 15);
  score += ratingScore(r.rating);

  if (issueSize) {
    if (issueSize <= 5) score += 6;
    else if (issueSize >= 25) score -= 6;
    else if (issueSize >= 15) score -= 3;
  }

  if (cost) {
    if (cost <= 8000) score += 4;
    else if (cost >= 30000) score -= 4;
  }

  if (stockChange >= 5) score -= 8;
  else if (stockChange <= -3) score -= 3;

  if (change60d >= 40) score -= 8;
  else if (change60d >= 25) score -= 4;

  if (daysToRecord !== null) {
    if (daysToRecord === 0) score += 4;
    else if (daysToRecord <= 3 && daysToRecord > 0) score += 2;
    else if (daysToRecord > 20) score -= 3;
  }

  if (!r.record_date || !r.allot_per_share || !r.stock_price) score -= 15;

  score = clamp(score, 0, 100);
  const grade = score >= 78 ? 'A' : (score >= 64 ? 'B' : (score >= 50 ? 'C' : 'D'));

  const reasons = riskReasons(r, m);
  let risk = '低';
  if (reasons.length >= 4 || safety < 0.8 || !r.record_date || !r.allot_per_share) risk = '高';
  else if (reasons.length >= 2 || safety < 2) risk = '中';

  return { score: Number(score.toFixed(2)), grade, risk, reasons };
}

function computeMetrics(r, params) {
  const allot = Number(r.allot_per_share || 0);
  const stockPrice = Number(r.stock_price || 0);
  const expPrice = params.expectedPrice > 0 ? params.expectedPrice : Number(r.expected_price || 0);
  const lots = Number(params.targetLots || 1);
  const needShares = allot > 0 ? ceilToBoardLot(lots * FACE_VALUE_PER_LOT / allot) : 0;
  const allocatedFace = needShares * allot;
  const allocatedLots = allocatedFace > 0 ? allocatedFace / FACE_VALUE_PER_LOT : 0;
  const stockCost = needShares > 0 && stockPrice > 0 ? needShares * stockPrice : 0;
  const expectedBondProfit = expPrice > 0 ? lots * FACE_VALUE_PER_LOT * (expPrice - 100) / 100 : 0;
  const safety = stockCost > 0 ? expectedBondProfit / stockCost * 100 : 0;
  const stockLoss = stockCost * Number(params.stockDropPct || 0) / 100;
  const netProfit = expectedBondProfit - stockLoss;
  const netYield = stockCost > 0 ? netProfit / stockCost * 100 : 0;
  const today = todayCnString();
  const basisDate = r.record_date || r.subscribe_date || '';
  const daysToRecord = r.record_date ? daysBetween(r.record_date, today) : null;

  const base = {
    target_lots: lots,
    basis_date: basisDate,
    days_to_record: daysToRecord,
    need_shares: needShares,
    allocated_face_est: Number(allocatedFace.toFixed(2)),
    allocated_lots_est: Number(allocatedLots.toFixed(2)),
    stock_cost: Number(stockCost.toFixed(2)),
    expected_price_used: expPrice > 0 ? Number(expPrice.toFixed(2)) : null,
    expected_bond_profit: Number(expectedBondProfit.toFixed(2)),
    safety_cushion_pct: Number(safety.toFixed(2)),
    assumed_stock_drop_pct: Number(params.stockDropPct || 0),
    stock_loss: Number(stockLoss.toFixed(2)),
    net_profit_after_drop: Number(netProfit.toFixed(2)),
    net_yield_after_drop_pct: Number(netYield.toFixed(2)),
  };

  const gr = gradeAndRisk(r, base);
  return { ...base, ...gr };
}

function computeAllRows() {
  if (!payload) return [];
  const params = getParams();
  const today = todayCnString();

  return (payload.rows || []).map(r => {
    const status = statusOf(r, today);
    const metrics = computeMetrics(r, params);
    return { ...r, ...metrics, status };
  });
}

function matchKeyword(r, kw) {
  if (!kw) return true;
  const hay = [r.bond_code, r.bond_name, r.stock_code, r.stock_name, r.allot_code, r.purchase_code, r.rating]
    .map(x => String(x || '').toLowerCase())
    .join(' ');
  return hay.includes(kw);
}

function inStage(r, params, today, end) {
  const basis = r.record_date || r.subscribe_date || '';
  const d = r.record_date ? r.days_to_record : null;

  if (params.stageFilter === 'all') return !basis || (addDays(today, -30) <= basis && basis <= end);
  if (params.stageFilter === 'today') return d === 0;
  if (params.stageFilter === 'threeDays') return d !== null && d >= 0 && d <= 3;
  if (params.stageFilter === 'future') return r.status === '未来待发行' || !r.record_date || !r.allot_per_share;
  if (params.stageFilter === 'history') return ['已过登记日', '已申购待上市', '已上市/历史'].includes(r.status);

  return ['可抢权', '等待股权登记', '未来待发行'].includes(r.status)
    && (!basis || (today <= basis && basis <= end));
}

function sortRows(rows, sortBy) {
  const copy = [...rows];
  copy.sort((a, b) => {
    if (sortBy === 'score') return (b.score || 0) - (a.score || 0);
    if (sortBy === 'safety') return (b.safety_cushion_pct || 0) - (a.safety_cushion_pct || 0);
    if (sortBy === 'cost') return (a.stock_cost || Number.MAX_SAFE_INTEGER) - (b.stock_cost || Number.MAX_SAFE_INTEGER);
    if (sortBy === 'profit') return (b.net_profit_after_drop || 0) - (a.net_profit_after_drop || 0);

    const ad = a.days_to_record === null ? 9999 : a.days_to_record;
    const bd = b.days_to_record === null ? 9999 : b.days_to_record;
    if (ad !== bd) return ad - bd;
    return (b.score || 0) - (a.score || 0);
  });
  return copy;
}

function computeRows() {
  const params = getParams();
  const today = todayCnString();
  const end = addDays(today, params.days);
  allComputedRows = computeAllRows();

  const rows = allComputedRows.filter(r => {
    if (!matchKeyword(r, params.keyword)) return false;
    if (!inStage(r, params, today, end)) return false;
    if (params.minSafety !== null && Number(r.safety_cushion_pct || 0) < params.minSafety && r.allot_per_share) return false;
    if (params.maxCost > 0 && Number(r.stock_cost || 0) > params.maxCost) return false;
    return true;
  });

  return sortRows(rows, params.sortBy);
}

function calcForLots(r, lots, price = null, dropPct = null) {
  const allot = Number(r.allot_per_share || 0);
  const stockPrice = Number(r.stock_price || 0);
  const expectedPrice = Number(price ?? r.expected_price_used ?? r.expected_price ?? 0);
  const drop = Number(dropPct ?? getParams().stockDropPct ?? 0);
  const needShares = allot > 0 ? ceilToBoardLot(lots * FACE_VALUE_PER_LOT / allot) : 0;
  const stockCost = needShares > 0 && stockPrice > 0 ? needShares * stockPrice : 0;
  const bondProfit = expectedPrice > 0 ? lots * FACE_VALUE_PER_LOT * (expectedPrice - 100) / 100 : 0;
  const stockLoss = stockCost * drop / 100;
  const net = bondProfit - stockLoss;
  const safety = stockCost > 0 ? bondProfit / stockCost * 100 : 0;
  return { lots, needShares, stockCost, bondProfit, stockLoss, net, safety };
}

function rowHtml(r) {
  const stockChange = r.stock_change_pct === null || r.stock_change_pct === undefined || r.stock_change_pct === '' ? '' : ` / ${Number(r.stock_change_pct).toFixed(2)}%`;
  const safetyCls = Number(r.safety_cushion_pct || 0) >= 2 ? 'green' : (Number(r.safety_cushion_pct || 0) >= 1 ? 'orange' : 'red');
  const netCls = Number(r.net_profit_after_drop || 0) >= 0 ? 'green' : 'red';
  const riskText = (r.reasons || []).slice(0, 2).join('；');

  return `
    <tr>
      <td>${gradeHtml(r.grade)}<div class="name-sub">${fmtNum(r.score, 1)}分</div></td>
      <td>${badge(r.status)}</td>
      <td><div class="name-main">${esc(r.bond_name || '-')}</div><div class="name-sub">${esc(r.bond_code || '')}</div></td>
      <td><div class="name-main">${esc(r.stock_name || '-')}</div><div class="name-sub">${esc(r.stock_code || '')} / ¥${fmtNum(r.stock_price, 2)}${esc(stockChange)}</div></td>
      <td><b>${esc(r.record_date || '-')}</b><div class="name-sub">${r.days_to_record === null ? '-' : `${r.days_to_record} 天`}</div></td>
      <td>${esc(r.subscribe_date || '-')}</td>
      <td>${esc(r.allot_code || '-')}</td>
      <td>${fmtNum(r.allot_per_share, 4)}</td>
      <td><b>${fmtInt(r.need_shares)}</b><div class="name-sub">约 ${fmtNum(r.allocated_lots_est, 2)} 手</div></td>
      <td>¥${fmtNum(r.stock_cost, 2)}</td>
      <td>¥${fmtNum(r.expected_price_used, 2)}<div class="name-sub">转股价值 ${fmtNum(r.convert_value, 2)}</div></td>
      <td class="green">¥${fmtNum(r.expected_bond_profit, 2)}</td>
      <td class="${safetyCls}">${fmtNum(r.safety_cushion_pct, 2)}%</td>
      <td class="${netCls}">¥${fmtNum(r.net_profit_after_drop, 2)}<div class="name-sub">${fmtNum(r.net_yield_after_drop_pct, 2)}%</div></td>
      <td>${riskHtml(r.risk)}<div class="name-sub" title="${esc(riskText)}">${esc(riskText || '-')}</div></td>
      <td><button class="small" data-action="detail" data-code="${esc(r.bond_code)}">详情</button></td>
    </tr>`;
}

function miniItemHtml(r, mode = 'date') {
  const mainValue = mode === 'safety'
    ? `${fmtNum(r.safety_cushion_pct, 2)}%`
    : (mode === 'cost' ? `¥${fmtCompact(r.stock_cost, 1)}` : `${r.days_to_record === null ? '-' : r.days_to_record + '天'}`);
  return `
    <div class="mini-item" data-action="detail" data-code="${esc(r.bond_code)}">
      <div>
        <div class="mini-title">${esc(r.bond_name || '-')} ${gradeHtml(r.grade)}</div>
        <div class="mini-sub">${esc(r.stock_name || r.stock_code || '-')}｜登记 ${esc(r.record_date || '-')}｜买 ${fmtInt(r.need_shares)} 股｜安全垫 ${fmtNum(r.safety_cushion_pct, 2)}%</div>
      </div>
      <div class="mini-value">${mainValue}</div>
    </div>`;
}

function renderMiniPanels() {
  const near = sortRows(allComputedRows.filter(r => r.days_to_record !== null && r.days_to_record >= 0 && r.days_to_record <= 7), 'date').slice(0, 6);
  const ranked = sortRows(allComputedRows.filter(r => r.stock_cost > 0 && r.safety_cushion_pct !== 0), 'safety').slice(0, 6);
  const future = allComputedRows.filter(r => r.status === '未来待发行' || !r.record_date || !r.allot_per_share).slice(0, 6);

  $('focusList').innerHTML = near.length ? near.map(r => miniItemHtml(r, 'date')).join('') : '<div class="empty">暂无未来 7 天内登记机会</div>';
  $('rankList').innerHTML = ranked.length ? ranked.map(r => miniItemHtml(r, 'safety')).join('') : '<div class="empty">暂无可计算安全垫的数据</div>';
  $('futureList').innerHTML = future.length ? future.map(r => miniItemHtml(r, 'cost')).join('') : '<div class="empty">暂无未来待发行或资料不全数据</div>';
}

function renderStats() {
  const today = todayCnString();
  const tomorrow = addDays(today, 1);
  const rows = allComputedRows;
  const todayRows = rows.filter(r => r.record_date === today);
  const tomorrowRows = rows.filter(r => r.record_date === tomorrow);
  const threeRows = rows.filter(r => r.days_to_record !== null && r.days_to_record >= 0 && r.days_to_record <= 3);
  const gradeA = rows.filter(r => r.grade === 'A' && ['可抢权', '等待股权登记'].includes(r.status));
  const safetyRows = rows.filter(r => r.stock_cost > 0).sort((a, b) => (b.safety_cushion_pct || 0) - (a.safety_cushion_pct || 0));
  const costRows = rows.filter(r => r.stock_cost > 0 && ['可抢权', '等待股权登记'].includes(r.status)).sort((a, b) => a.stock_cost - b.stock_cost);
  const best = safetyRows[0];
  const low = costRows[0];

  $('statToday').textContent = todayRows.length;
  $('statTomorrow').textContent = tomorrowRows.length;
  $('statThreeDays').textContent = threeRows.length;
  $('statGradeA').textContent = gradeA.length;
  $('statBestSafety').textContent = best ? `${fmtNum(best.safety_cushion_pct, 2)}%` : '-';
  $('statBestName').textContent = best ? `${best.bond_name || best.bond_code}` : '-';
  $('statLowestCost').textContent = low ? `¥${fmtCompact(low.stock_cost, 1)}` : '-';
  $('statLowestName').textContent = low ? `${low.bond_name || low.bond_code}` : '-';
}

function render() {
  latestRows = computeRows();
  const tbody = document.querySelector('#bondTable tbody');
  const today = todayCnString();

  renderStats();
  renderMiniPanels();

  $('summary').textContent = `今天 ${today}，当前筛选 ${latestRows.length} 条；源数据 ${payload?.rows?.length || 0} 条`;
  $('lastRefresh').textContent = payload?.build_time ? `数据生成：${payload.build_time}` : '暂无生成时间';

  if (payload?.errors?.length) {
    $('errorBox').textContent = payload.errors.join('\n');
    $('errorBox').classList.remove('hidden');
  } else {
    $('errorBox').classList.add('hidden');
  }

  if (payload?.warnings?.length) {
    $('warningBox').textContent = payload.warnings.join('\n');
    $('warningBox').classList.remove('hidden');
  } else {
    $('warningBox').classList.add('hidden');
  }

  tbody.innerHTML = latestRows.length
    ? latestRows.map(rowHtml).join('')
    : `<tr><td colspan="16" style="text-align:center;color:#667085;padding:36px;">暂无符合条件的数据。可以扩大未来天数、降低安全垫，或切换到“全部可见数据”。</td></tr>`;
}

async function loadData() {
  $('summary').textContent = '加载中...';
  $('errorBox').classList.add('hidden');
  $('warningBox').classList.add('hidden');

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
    '等级','评分','阶段','风险','转债代码','转债名称','正股代码','正股简称','股权登记日','距离登记日','申购日','配售码','每股获配额','买入股数','约配售手数','买股金额','转股价值','信用评级','预估上市价','转债收益','安全垫%','假设正股回撤%','综合收益','综合收益率%','风险提示','备注'
  ];
  const lines = [headers.join(',')];

  for (const r of latestRows) {
    const arr = [
      r.grade, r.score, r.status, r.risk, r.bond_code, r.bond_name, r.stock_code, r.stock_name,
      r.record_date, r.days_to_record, r.subscribe_date, r.allot_code, r.allot_per_share,
      r.need_shares, r.allocated_lots_est, r.stock_cost, r.convert_value, r.rating,
      r.expected_price_used, r.expected_bond_profit, r.safety_cushion_pct, r.assumed_stock_drop_pct,
      r.net_profit_after_drop, r.net_yield_after_drop_pct, (r.reasons || []).join('；'), r.remark
    ].map(v => `"${String(v ?? '').replace(/"/g, '""')}"`);
    lines.push(arr.join(','));
  }

  const blob = new Blob(['\ufeff' + lines.join('\n')], { type: 'text/csv;charset=utf-8;' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `可转债抢权配售V3_${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

function detailCards(r) {
  const cards = [
    ['综合等级', `${r.grade} / ${fmtNum(r.score, 1)}分`],
    ['风险等级', r.risk],
    ['股权登记日', r.record_date || '-'],
    ['申购日', r.subscribe_date || '-'],
    ['正股价格', `¥${fmtNum(r.stock_price, 2)}`],
    ['每股获配额', fmtNum(r.allot_per_share, 4)],
    ['转股价值', fmtNum(r.convert_value, 2)],
    ['发行规模', r.issue_size_yi ? `${fmtNum(r.issue_size_yi, 2)}亿` : '-'],
  ];
  return `<div class="detail-grid">${cards.map(([k, v]) => `<div class="detail-card"><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`).join('')}</div>`;
}

function allotCalcTable(r) {
  const lotsList = [1, 2, 5, 10];
  const rows = lotsList.map(lots => {
    const c = calcForLots(r, lots);
    const cls = c.net >= 0 ? 'green' : 'red';
    return `<tr>
      <td>${lots} 手</td>
      <td>${fmtInt(c.needShares)} 股</td>
      <td>¥${fmtNum(c.stockCost, 2)}</td>
      <td>¥${fmtNum(c.bondProfit, 2)}</td>
      <td>${fmtNum(c.safety, 2)}%</td>
      <td class="${cls}">¥${fmtNum(c.net, 2)}</td>
    </tr>`;
  }).join('');

  return `
    <h3 class="section-title">配售手数测算</h3>
    <table class="calc-table">
      <thead><tr><th>目标</th><th>约需买入</th><th>买股金额</th><th>转债收益</th><th>安全垫</th><th>回撤后综合收益</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function pnlMatrix(r) {
  const lots = getParams().targetLots;
  const rows = MATRIX_DROPS.map(drop => {
    const tds = MATRIX_PRICES.map(price => {
      const c = calcForLots(r, lots, price, drop);
      const cls = c.net >= 0 ? 'green' : 'red';
      return `<td class="${cls}">¥${fmtNum(c.net, 0)}</td>`;
    }).join('');
    return `<tr><td>正股跌 ${drop}%</td>${tds}</tr>`;
  }).join('');

  return `
    <h3 class="section-title">盈亏矩阵：${lots} 手配售目标</h3>
    <table class="matrix-table">
      <thead><tr><th>情景</th>${MATRIX_PRICES.map(p => `<th>转债 ${p}</th>`).join('')}</tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function riskSection(r) {
  const items = (r.reasons || []).map(x => `<div class="risk-item">${esc(x)}</div>`).join('');
  return `<h3 class="section-title">风险提示</h3><div class="risk-list">${items}</div>`;
}

function showDetail(code) {
  const row = allComputedRows.find(r => String(r.bond_code) === String(code)) || latestRows.find(r => String(r.bond_code) === String(code));
  if (!row) return;

  $('detailTitle').textContent = `${row.bond_name || '-'}（${row.bond_code || '-'}）`;
  $('detailSub').textContent = `${row.stock_name || '-'} ${row.stock_code || ''}｜${row.status}｜配售码 ${row.allot_code || '-'}`;
  $('detailBody').innerHTML = detailCards(row) + allotCalcTable(row) + pnlMatrix(row) + riskSection(row);

  const dialog = $('detailDialog');
  if (typeof dialog.showModal === 'function') dialog.showModal();
  else dialog.setAttribute('open', 'open');
}

function closeDetail() {
  const dialog = $('detailDialog');
  if (typeof dialog.close === 'function') dialog.close();
  else dialog.removeAttribute('open');
}

$('queryBtn').addEventListener('click', render);
$('reloadBtn').addEventListener('click', loadData);
$('exportBtn').addEventListener('click', exportCsv);
$('closeDetailBtn').addEventListener('click', closeDetail);

['days', 'targetLots', 'expectedPrice', 'stockDropPct', 'minSafety', 'maxCost', 'stageFilter', 'sortBy', 'keyword'].forEach(id => {
  const el = $(id);
  const evt = id === 'keyword' ? 'input' : 'change';
  el.addEventListener(evt, render);
});

document.body.addEventListener('click', (e) => {
  const target = e.target.closest('[data-action="detail"]');
  if (!target) return;
  showDetail(target.getAttribute('data-code'));
});

$('detailDialog').addEventListener('click', (e) => {
  if (e.target === $('detailDialog')) closeDetail();
});

loadData();
