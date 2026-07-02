const STAGE_ORDER = [
  '今日股权登记', '今日申购/配债', '股权登记临近', '已确定发行', '已注册等待发行',
  '发行成功待上市', '已上市', '转股期', '审核通过', '已回复问询', '已问询', '交易所受理',
  '股东大会通过', '董事会预案', '摘牌结束', '未知'
];

const CORE_STAGES = new Set(['今日股权登记', '今日申购/配债', '股权登记临近', '已确定发行', '已注册等待发行']);

let allItems = [];
let summary = {};

function esc(v) {
  return String(v ?? '').replace(/[&<>'"]/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[s]));
}

function fmt(v, fallback = '-') {
  return v === null || v === undefined || v === '' ? fallback : esc(v);
}

function fmtNum(v, digits = 2) {
  if (v === null || v === undefined || v === '' || Number.isNaN(Number(v))) return '-';
  return Number(v).toFixed(digits).replace(/\.00$/, '');
}

function riskClass(risk) {
  return ({'最高':'max','高':'high','中高':'midHigh','中':'mid','低':'low'}[risk] || 'low');
}

function stageBadge(item) {
  const risk = item.risk_level || '低';
  return `<span class="badge ${riskClass(risk)}">${fmt(item.stage)} · ${fmt(risk)}</span>`;
}

function issueAmount(item) {
  const a = item.actual_issue_amount_billion ?? item.issue_amount_billion ?? item.planned_issue_amount_billion;
  if (a === null || a === undefined || a === '') return '-';
  return `${fmtNum(a)} 亿`;
}

function subscribeText(item) {
  const d1 = item.priority_subscribe_date || item.online_subscribe_date;
  const code = item.online_subscribe_code ? `<div class="small">申购代码：${fmt(item.online_subscribe_code)}</div>` : '';
  const pay = item.priority_payment_date ? `<div class="small">缴款：${fmt(item.priority_payment_date)}</div>` : '';
  return `${fmt(d1)}${code}${pay}`;
}

function listingText(item) {
  const list = item.listing_date ? `<div>上市：${fmt(item.listing_date)}</div>` : '';
  const conv = item.convert_start_date ? `<div class="small">转股起：${fmt(item.convert_start_date)}</div>` : '';
  return list || conv ? `${list}${conv}` : '-';
}

function latestAnn(item) {
  const title = item.latest_title || '';
  const date = item.latest_announcement_date || '';
  if (!title) return '-';
  const url = item.latest_url || '';
  const body = `${date ? `<div class="small">${fmt(date)}</div>` : ''}<div>${fmt(title)}</div>`;
  return url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${body}</a>` : body;
}

function renderStats() {
  const stages = summary.stages || {};
  const high = (summary.risks?.['最高'] || 0) + (summary.risks?.['高'] || 0);
  const html = [
    ['全部项目', summary.total ?? allItems.length],
    ['抢权核心池', summary.core_count ?? allItems.filter(x => CORE_STAGES.has(x.stage)).length],
    ['最高/高提醒', high],
    ['已注册等待发行', stages['已注册等待发行'] || 0],
    ['已确定发行', stages['已确定发行'] || 0],
  ].map(([label, num]) => `<div class="stat"><div class="num">${fmt(num)}</div><div class="label">${fmt(label)}</div></div>`).join('');
  document.getElementById('stats').innerHTML = html;
}

function renderNotice() {
  const el = document.getElementById('sourceNotice');
  const warnings = summary.source_warnings || [];
  const note = summary.source_note || '';
  if (!warnings.length && !note) return;
  el.style.display = 'block';
  el.innerHTML = `${note ? `<div>${fmt(note)}</div>` : ''}${warnings.map(w => `<div>⚠ ${fmt(w)}</div>`).join('')}`;
}

function renderCore() {
  const core = allItems.filter(x => CORE_STAGES.has(x.stage)).slice(0, 12);
  const el = document.getElementById('coreCards');
  if (!core.length) {
    el.innerHTML = '<div class="empty">暂无抢权核心提醒。</div>';
    return;
  }
  el.innerHTML = core.map(item => {
    const name = item.stock_name || item.bond_name || item.stock_code || item.bond_code || '未知项目';
    return `<article class="card">
      <h3>${fmt(name)} ${item.stock_code ? `<span class="code">${fmt(item.stock_code)}</span>` : ''}</h3>
      <div class="sub">${stageBadge(item)}</div>
      <div class="line"><span>转债</span><strong>${fmt(item.bond_name)} ${item.bond_code ? `<span class="code">${fmt(item.bond_code)}</span>` : ''}</strong></div>
      <div class="line"><span>股权登记日</span><strong>${fmt(item.record_date)}</strong></div>
      <div class="line"><span>申购/配债日</span><strong>${fmt(item.priority_subscribe_date || item.online_subscribe_date)}</strong></div>
      <div class="line"><span>发行规模</span><strong>${issueAmount(item)}</strong></div>
      <p class="small">${fmt(item.next_action)}</p>
    </article>`;
  }).join('');
}

function renderFilters() {
  const stageFilter = document.getElementById('stageFilter');
  const stages = Array.from(new Set(allItems.map(x => x.stage).filter(Boolean)))
    .sort((a,b) => STAGE_ORDER.indexOf(a) - STAGE_ORDER.indexOf(b));
  stageFilter.innerHTML = '<option value="">全部状态</option>' + stages.map(s => `<option value="${esc(s)}">${fmt(s)}</option>`).join('');
}

function filteredItems() {
  const q = document.getElementById('searchInput').value.trim().toLowerCase();
  const stage = document.getElementById('stageFilter').value;
  const risk = document.getElementById('riskFilter').value;
  return allItems.filter(item => {
    if (stage && item.stage !== stage) return false;
    if (risk && item.risk_level !== risk) return false;
    if (!q) return true;
    const hay = [item.stock_code, item.stock_name, item.bond_code, item.bond_name, item.latest_title, item.online_subscribe_code]
      .map(v => String(v ?? '').toLowerCase()).join(' ');
    return hay.includes(q);
  });
}

function renderTable() {
  const items = filteredItems();
  const tbody = document.getElementById('tbody');
  if (!items.length) {
    tbody.innerHTML = `<tr><td colspan="9" class="empty">没有匹配结果。</td></tr>`;
    return;
  }
  tbody.innerHTML = items.map(item => `<tr>
    <td><div class="stage">${stageBadge(item)}<span class="small">${fmt(item.latest_announcement_date)}</span></div></td>
    <td><strong>${fmt(item.stock_name)}</strong><div class="code">${fmt(item.stock_code)}</div></td>
    <td><strong>${fmt(item.bond_name)}</strong><div class="code">${fmt(item.bond_code)}</div>${item.rating ? `<div class="small">评级：${fmt(item.rating)}</div>` : ''}</td>
    <td class="nowrap">${issueAmount(item)}</td>
    <td class="nowrap">${fmt(item.record_date)}${Number.isInteger(item.days_to_record) ? `<div class="small">${item.days_to_record >= 0 ? '还有 ' + item.days_to_record + ' 天' : '已过 ' + Math.abs(item.days_to_record) + ' 天'}</div>` : ''}</td>
    <td class="nowrap">${subscribeText(item)}</td>
    <td class="nowrap">${listingText(item)}</td>
    <td>${fmt(item.next_action)}</td>
    <td>${latestAnn(item)}</td>
  </tr>`).join('');
}

async function load() {
  const res = await fetch('./data/convertibles.json?v=' + Date.now());
  if (!res.ok) throw new Error('数据文件加载失败');
  const data = await res.json();
  summary = data;
  allItems = data.items || [];
  document.getElementById('meta').textContent = `更新：${data.generated_at || '-'} ｜ 今日：${data.today || '-'} ｜ 项目数：${data.total ?? allItems.length}`;
  renderStats();
  renderNotice();
  renderCore();
  renderFilters();
  renderTable();
}

document.getElementById('searchInput').addEventListener('input', renderTable);
document.getElementById('stageFilter').addEventListener('change', renderTable);
document.getElementById('riskFilter').addEventListener('change', renderTable);
document.getElementById('resetBtn').addEventListener('click', () => {
  document.getElementById('searchInput').value = '';
  document.getElementById('stageFilter').value = '';
  document.getElementById('riskFilter').value = '';
  renderTable();
});
document.getElementById('refreshBtn').addEventListener('click', () => location.reload());

load().catch(err => {
  document.getElementById('meta').textContent = err.message;
  document.getElementById('tbody').innerHTML = `<tr><td colspan="9" class="empty">${esc(err.message)}。请先运行 GitHub Actions 或本地执行 scripts/update_data.py 生成数据。</td></tr>`;
});
