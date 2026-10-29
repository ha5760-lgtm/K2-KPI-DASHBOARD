// K2 KPI Board - Frontend
// 공정 탭 → KPI 카드 → 카드 선택 시 같은 페이지 하단에 연결 데이터 상세 분석

// ─── State ────────────────────────────────
const state = {
    process: 'all',
    kpi: null,
    ws: null,
    processes: [],
    cards: null,
    detail: null,
};

const COLORS = {
    navy: '#1428A0', navy2: '#3B5BC0', navy3: '#8A9AD6', navy4: '#C9D1EE', navyBg: '#EEF1FA',
    red: '#D63232', amber: '#B7791F', grid: '#EEF0F4', axis: '#8B92A1', text: '#111318', text2: '#4B5160',
    track: '#F4F5F8',
};
const SERIES = [COLORS.navy, COLORS.navy2, COLORS.navy3, COLORS.navy4, '#DDE1EA', '#B8BECB', '#9AA3B5', '#6E7FB8'];
const BADGE = { ok: '달성', warn: '주의', bad: '미달' };
const NS = 'http://www.w3.org/2000/svg';

// ─── Utils ────────────────────────────────
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function fmt(v, digits = 0) {
    if (v == null || Number.isNaN(v)) return '-';
    return Number(v).toLocaleString('ko-KR', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function el(tag, attrs, parent, text) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (text != null) e.textContent = text;
    if (parent) parent.appendChild(e);
    return e;
}

function niceScale(lo, hi, ticks = 5) {
    if (hi === lo) hi = lo + 1;
    const raw = (hi - lo) / ticks;
    const mag = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw);
    return { lo: Math.floor(lo / step) * step, hi: Math.ceil(hi / step) * step, step };
}

function digitsFor(v) {
    const a = Math.abs(v);
    return a >= 100 ? 0 : a >= 10 ? 1 : 2;
}

function showTip(html, x, y) {
    const t = $('tooltip');
    t.innerHTML = html;
    t.hidden = false;
    const r = t.getBoundingClientRect();
    let left = x + 14, top = y - r.height - 10;
    if (left + r.width > window.innerWidth - 8) left = x - r.width - 14;
    if (top < 8) top = y + 14;
    t.style.left = `${left}px`;
    t.style.top = `${top}px`;
}
// 서버 기준시(KST)로 표시 — 접속 PC 시간대와 무관하게 이벤트 시각과 일치
function kstTime(iso, withDate = false) {
    const p = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
        timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
    }).formatToParts(new Date(iso)).map((x) => [x.type, x.value]));
    const t = `${p.hour}:${p.minute}:${p.second}`;
    return withDate ? `${p.year}.${p.month}.${p.day} ${t}` : t;
}

const hideTip = () => { $('tooltip').hidden = true; };

// ─── URL hash (공유 링크) ────────────────────────────────
function readHash() {
    const p = new URLSearchParams(location.hash.slice(1));
    if (p.get('process')) state.process = p.get('process');
    if (p.get('kpi')) state.kpi = p.get('kpi');
}
function writeHash() {
    const p = new URLSearchParams({ process: state.process });
    if (state.kpi) p.set('kpi', state.kpi);
    history.replaceState(null, '', `#${p}`);
}

// ─── API ────────────────────────────────
async function api(path) {
    const res = await fetch(path);
    if (!res.ok) throw new Error(`${path} → ${res.status}`);
    return res.json();
}

async function loadAll({ scroll = false } = {}) {
    const [procs, cards] = await Promise.all([api('/api/processes'), api(`/api/kpis?process=${state.process}`)]);
    state.processes = procs;
    state.cards = cards;
    if (!state.kpi || !cards.cards.some((c) => c.id === state.kpi)) {
        state.kpi = (cards.cards.find((c) => c.status === 'bad') || cards.cards[0]).id;
    }
    renderTabs();
    renderCards();
    await loadDetail({ scroll });
}

async function loadDetail({ scroll = false } = {}) {
    writeHash();
    subscribe();
    state.detail = await api(`/api/kpis/${state.kpi}?process=${state.process}`);
    renderDetail();
    if (scroll) $('detail').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function selectProcess(id) {
    if (id === state.process) return;
    state.process = id;
    loadAll();
}

function selectKpi(id) {
    if (id === state.kpi) {
        $('detail').scrollIntoView({ behavior: 'smooth', block: 'start' });
        return;
    }
    state.kpi = id;
    renderCards();
    loadDetail({ scroll: true });
}

// ─── WebSocket ────────────────────────────────
function connectWS() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${proto}://${location.host}/ws`);
    state.ws = ws;
    ws.onopen = () => {
        $('live').classList.add('on');
        $('live').querySelector('b').textContent = 'LIVE';
        subscribe();
    };
    ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        if (msg.type !== 'kpi_update') return;
        if (msg.cards.process !== state.process) return;
        state.processes = msg.processes;
        state.cards = msg.cards;
        renderTabs();
        renderCards();
        if (msg.detail && msg.detail.kpi.id === state.kpi && msg.detail.process === state.process) {
            state.detail = msg.detail;
            renderDetail();
        }
    };
    ws.onclose = () => {
        $('live').classList.remove('on');
        $('live').querySelector('b').textContent = '재연결 중';
        setTimeout(connectWS, 3000);
    };
}

function subscribe() {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
        state.ws.send(JSON.stringify({ type: 'subscribe', process: state.process, kpi: state.kpi }));
    }
}

// ─── Header / Tabs ────────────────────────────────
async function loadSources() {
    try {
        const src = await api('/api/sources');
        const demo = src.some((s) => s.mode === 'simulated');
        $('sources').innerHTML = src.map((s) =>
            `<span class="src"><i class="dot ${s.status === 'ok' ? '' : s.status}"></i>${esc(s.name)}</span>`).join('')
            + (demo ? '<span class="src demo" title="실데이터 연동 전 시뮬레이션 데이터">DEMO 데이터</span>' : '');
    } catch (e) { /* 헤더 표시용 — 실패해도 무시 */ }
}

function renderTabs() {
    $('procTabs').innerHTML = state.processes.map((p) => {
        let cnt = '';
        if (p.bad) cnt = `<span class="cnt" title="미달 KPI">${p.bad}</span>`;
        else if (p.warn) cnt = `<span class="cnt warn" title="주의 KPI">${p.warn}</span>`;
        return `<button class="proc-tab ${p.id === state.process ? 'on' : ''}" data-proc="${p.id}">
            ${esc(p.name)}<span class="en">${esc(p.en)}</span>${cnt}</button>`;
    }).join('');
}

// ─── KPI cards ────────────────────────────────
function sparkSVG(data, color) {
    const w = 96, h = 34;
    const mn = Math.min(...data), mx = Math.max(...data);
    const pts = data.map((v, i) => [i / (data.length - 1) * w, h - 3 - (v - mn) / (mx - mn || 1) * (h - 6)]);
    const d = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ');
    const last = pts[pts.length - 1];
    return `<svg width="${w}" height="${h}" aria-hidden="true"><path d="${d} L${w} ${h} L0 ${h}Z" fill="${color}" opacity=".08"/>`
        + `<path d="${d}" fill="none" stroke="${color}" stroke-width="1.6"/><circle cx="${last[0]}" cy="${last[1]}" r="2.6" fill="${color}"/></svg>`;
}

function renderCards() {
    const c = state.cards;
    if (!c) return;
    $('cardsTitle').textContent = `${c.label} 핵심 KPI`;
    $('updated').textContent = kstTime(c.updated, true);
    $('summary').innerHTML = `<span><i style="background:var(--navy)"></i>달성 ${c.summary.ok}</span>`
        + `<span><i style="background:var(--amber)"></i>주의 ${c.summary.warn}</span>`
        + `<span><i style="background:var(--red)"></i>미달 ${c.summary.bad}</span>`;
    $('kpis').innerHTML = c.cards.map((k) => {
        const color = k.status === 'bad' ? COLORS.red : COLORS.navy;
        const sub = k.sub ? `<span class="kpi-sub">${esc(k.sub)}</span>` : '';
        return `<button class="kpi ${k.id === state.kpi ? 'sel' : ''}" data-kpi="${k.id}" aria-pressed="${k.id === state.kpi}">
            <div class="kpi-top"><span class="kpi-name">${esc(k.name)}</span><span class="kpi-en">${esc(k.en)}</span><span class="badge ${k.status}">${BADGE[k.status]}</span></div>
            <div class="kpi-body">
              <div>
                <div class="kpi-val num ${k.text ? 'txt' : ''} ${k.status === 'bad' ? 'bad' : ''}">${esc(k.display)}<small>${esc(k.unit)}</small>${sub}</div>
                <div class="kpi-delta ${k.delta_class}">${esc(k.delta)}</div>
              </div>
              ${sparkSVG(k.spark, color)}
            </div>
            <div class="kpi-foot"><span>목표 <b>${esc(k.target_text)}</b></span><div class="bar"><i class="${k.status}" style="width:${k.pct}%"></i></div><b class="num">${k.pct}%</b></div>
        </button>`;
    }).join('');
}

// ─── Detail ────────────────────────────────
function renderDetail() {
    const d = state.detail;
    if (!d) return;
    const m = d.meta, k = d.kpi;
    const box = $('detail');
    box.hidden = false;
    const unit = k.text ? '' : k.unit;
    const tgt = k.text ? `대기 WIP ${fmt(k.value)}wf · 기준 ${fmt(m.target)}wf` : `목표 ${esc(k.target_text)} · ${esc(k.delta)}`;
    const keepScroll = box.querySelector('.tbl-wrap')?.scrollLeft || 0;

    box.innerHTML = `
    <div class="detail-head">
      <div>
        <div class="crumb">${esc(d.label)} › 핵심 KPI › 상세 분석</div>
        <div class="detail-title">${esc(m.name)} <span class="v num ${k.status}">${esc(k.display)}${esc(unit)}</span><span class="t">${tgt}</span></div>
      </div>
      <div class="spacer"></div>
      <div class="conn">연결 데이터 ${m.sources.map((s) => `<span class="chip"><i class="dot"></i>${esc(s)}</span>`).join('')}</div>
      <button class="btn" data-action="copy">링크 복사</button>
      <button class="btn primary" data-action="csv">Excel(CSV) 내보내기</button>
    </div>
    <div class="mini">${d.stats.map(renderStat).join('')}</div>
    <div class="dgrid">
      <div class="panel c8">
        <h3>${esc(d.trend.title)} <span class="src-tag">${esc(m.sources[0])}</span></h3>
        <div class="sub">${esc(d.trend.sub)}</div>
        <div class="legend">
          <span><i style="background:${COLORS.navy}"></i>${esc(d.trend.series_label)}</span>
          ${d.trend.prev ? `<span><i style="background:${COLORS.navy4}"></i>${esc(d.trend.prev_label)}</span>` : ''}
          <span><i style="background:${COLORS.red}"></i>${d.kpi.id === 'bottleneck' ? '병목 기준' : '목표'} ${fmt(d.trend.target, digitsFor(d.trend.target))}${esc(d.trend.unit === '%' ? '%' : '')}</span>
        </div>
        <svg class="chart" id="trendChart" height="230"></svg>
      </div>
      <div class="panel c4 last">
        <h3>${esc(d.breakdown.title)} <span class="src-tag">${esc(d.breakdown.src)}</span></h3>
        <div class="sub">${esc(d.breakdown.sub)}</div>
        ${renderBreakdown(d.breakdown)}
      </div>
      <div class="panel c5">
        <h3>${esc(d.ranking.title)} <span class="src-tag">${esc(d.ranking.src)}</span></h3>
        <div class="sub">${esc(d.ranking.sub)}</div>
        <svg class="chart" id="rankChart" height="${d.ranking.items.length * 26 + 6}"></svg>
      </div>
      <div class="panel c7 last">
        <h3>${esc(d.heatmap.title)} <span class="src-tag">${esc(d.ranking.src)}</span></h3>
        <div class="sub">${esc(d.heatmap.sub)}</div>
        <svg class="chart" id="heatChart" height="${d.heatmap.rows.length * 25 + 24}"></svg>
      </div>
      <div class="panel c8 end">
        <h3>${esc(d.events.title)} <span class="src-tag">${esc(d.events.src)}</span></h3>
        <div class="sub">${esc(d.events.sub)}</div>
        <div class="tbl-wrap">${renderTable(d.events)}</div>
      </div>
      <div class="c4 stack">
        <div class="panel">
          <h3>공정별 비교 <span class="src-tag">8대 공정</span></h3>
          <div class="sub">${d.compare.by_pct ? '일 목표 대비 달성률' : d.kpi.id === 'bottleneck' ? '공정별 최대 대기 WIP (wf)' : '공정별 현재값'} · 행을 누르면 해당 공정으로 이동</div>
          ${renderCompare(d.compare)}
        </div>
        <div class="panel last end">
          <h3>분석 요약 <span class="src-tag">자동 분석</span></h3>
          <div class="sub">연결 데이터 기반 규칙 분석</div>
          <div class="insight">${d.insights.map((i) => `<div class="ins ${i.level}"><b>${esc(i.title)}</b><p>${esc(i.text)}</p></div>`).join('')}</div>
        </div>
      </div>
    </div>
    <div class="foot-note"><span>데이터 원천: ${m.sources.map(esc).join(' / ')}</span><span>${esc(m.formula)}</span><span>최종 동기화 ${kstTime(d.updated)}</span></div>`;

    drawTrend($('trendChart'), d.trend, m);
    drawRanking($('rankChart'), d.ranking);
    drawHeatmap($('heatChart'), d.heatmap);
    const tw = box.querySelector('.tbl-wrap');
    if (tw) tw.scrollLeft = keepScroll;
}

function renderStat(s) {
    const delta = s.delta ? `<em class="${s.tone === 'bad' ? 'bad' : s.tone === 'good' ? 'good' : ''}">${esc(s.delta)}</em>` : '';
    const note = s.note ? `<em>${esc(s.note)}</em>` : '';
    return `<div><span>${esc(s.label)}</span><b class="num ${s.mono ? 'mono' : ''} ${s.tone === 'alert' ? 'alert' : ''}">${esc(s.value)}<small>${esc(s.unit)}</small></b>${delta}${note}</div>`;
}

function renderBreakdown(b) {
    const items = b.items || [];
    const tot = items.reduce((s, x) => s + x.value, 0) || 1;
    const max = b.max || Math.max(...items.map((x) => x.value), 1);
    const color = (i) => SERIES[i % SERIES.length];
    const stack = b.share
        ? `<div class="stackbar">${items.map((x, i) => `<i style="flex:${x.value || 0.0001};background:${color(i)}" title="${esc(x.name)}"></i>`).join('')}</div>`
        : '';
    const digits = items.some((x) => !Number.isInteger(x.value)) ? 1 : 0;
    return stack + items.map((x, i) => `
        <div class="bd-row">
          <span class="sw" style="background:${color(i)}"></span>
          <span class="nm">${esc(x.name)}</span>
          <span class="sh">${b.share ? `${(x.value / tot * 100).toFixed(0)}%` : ''}</span>
          <span class="vl num">${fmt(x.value, digits)}${esc(b.unit)}</span>
          <span class="tr"><i style="width:${(x.value / (b.share ? Math.max(...items.map((y) => y.value), 1) : max) * 100).toFixed(1)}%;background:${i >= 4 ? '#B8BECB' : color(i)}"></i></span>
        </div>`).join('');
}

function renderCompare(c) {
    const items = c.items;
    const vals = items.map((x) => (c.by_pct ? x.value / x.target * 100 : x.value));
    const scaleMax = Math.max(...vals, c.target || 0) * 1.08 || 1;
    const tgtPos = c.by_pct ? 100 / scaleMax * 100 : c.target ? c.target / scaleMax * 100 : null;
    return items.map((x, i) => {
        const w = vals[i] / scaleMax * 100;
        const val = c.by_pct ? `${vals[i].toFixed(1)}%` : x.display + (c.unit === '%' ? '%' : '');
        const title = x.label ? ` title="${esc(x.label)}"` : '';
        return `<button class="cmp-row ${x.id === state.process ? 'on' : ''}" data-proc="${x.id}"${title}>
            <span class="nm">${esc(x.name)}</span>
            <span class="tr"><i class="${x.status}" style="width:${w.toFixed(1)}%"></i>${tgtPos != null ? `<em style="left:${tgtPos.toFixed(1)}%"></em>` : ''}</span>
            <span class="vl num">${esc(val)}</span>
            <span class="dot ${x.status === 'ok' ? '' : x.status}"></span>
        </button>`;
    }).join('');
}

function renderTable(t) {
    if (!t.rows.length) return '<div class="empty">표시할 이벤트가 없습니다</div>';
    return `<table><thead><tr>${t.columns.map((c) => `<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${t.rows.map((r) =>
        `<tr class="${r.new ? 'new' : ''}">${r.cells.map((c) => {
            if (c.k === 'status') return `<td><span class="st ${c.s}">${esc(c.t)}</span></td>`;
            return `<td class="${c.k || ''}">${esc(c.t)}</td>`;
        }).join('')}</tr>`).join('')}</tbody></table>`;
}

// ─── Charts ────────────────────────────────
function drawTrend(svg, t, meta) {
    const W = svg.clientWidth || 800, H = 230, L = 52, R = 14, T = 14, B = 24;
    const all = [...t.today, ...(t.prev || []), t.target];
    let lo = Math.min(...all), hi = Math.max(...all);
    const span = hi - lo || 1;
    lo = lo < span * 0.6 ? 0 : lo - span * 0.25;
    hi += span * 0.12;
    if (t.unit === '%' && hi > 100 && Math.max(...t.today) <= 100) hi = 100;
    const sc = niceScale(lo, hi);
    const n = t.today.length;
    const x = (i) => L + i / (n - 1) * (W - L - R);
    const y = (v) => T + (1 - (v - sc.lo) / (sc.hi - sc.lo)) * (H - T - B);
    const dg = digitsFor(sc.step) === 2 && sc.step < 1 ? 1 : 0;

    for (let v = sc.lo; v <= sc.hi + 1e-9; v += sc.step) {
        el('line', { x1: L, x2: W - R, y1: y(v), y2: y(v), stroke: COLORS.grid }, svg);
        el('text', { x: L - 8, y: y(v) + 3.5, 'text-anchor': 'end', 'font-size': 10, fill: COLORS.axis }, svg, fmt(v, dg));
    }
    const every = n > 16 ? 3 : n > 8 ? 2 : 1;
    t.labels.forEach((lb, i) => {
        if (i % every === 0) el('text', { x: x(i), y: H - 6, 'text-anchor': 'middle', 'font-size': 10, fill: COLORS.axis }, svg, lb);
    });
    // 교대 시간대 음영 (시간 단위 추이에서만)
    if (t.labels[0] && t.labels[0].includes(':')) {
        t.labels.forEach((lb, i) => {
            if (['06:00', '14:00', '22:00'].includes(lb)) {
                const x0 = x(Math.max(0, i - 1)), x1 = x(Math.min(n - 1, i + 1));
                el('rect', { x: x0, y: T, width: x1 - x0, height: H - T - B, fill: COLORS.navy, opacity: 0.035 }, svg);
                el('text', { x: x(i), y: T + 10, 'text-anchor': 'middle', 'font-size': 9.5, fill: COLORS.axis }, svg, '교대');
            }
        });
    }
    el('line', { x1: L, x2: W - R, y1: y(t.target), y2: y(t.target), stroke: COLORS.red, 'stroke-dasharray': '4 3', 'stroke-width': 1.2 }, svg);
    const path = (a) => a.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(' ');
    if (t.prev) el('path', { d: path(t.prev), fill: 'none', stroke: COLORS.navy4, 'stroke-width': 1.6 }, svg);
    el('path', { d: `${path(t.today)} L${x(n - 1)} ${y(sc.lo)} L${x(0)} ${y(sc.lo)}Z`, fill: COLORS.navy, opacity: 0.06 }, svg);
    el('path', { d: path(t.today), fill: 'none', stroke: COLORS.navy, 'stroke-width': 2.2 }, svg);
    const breach = (v) => (t.lower ? v > t.target : v < t.target);
    t.today.forEach((v, i) => {
        if (breach(v)) el('circle', { cx: x(i), cy: y(v), r: 2.6, fill: '#fff', stroke: COLORS.red, 'stroke-width': 1.5 }, svg);
    });
    const lv = t.today[n - 1];
    const vd = meta.unit === '%' ? meta.digits : 0;
    const label = fmt(lv, vd) + (t.unit === '%' ? '%' : '');
    el('circle', { cx: x(n - 1), cy: y(lv), r: 4, fill: COLORS.navy }, svg);
    const bw = label.length * 6.6 + 14;
    const by = y(lv) - 30 < T ? y(lv) + 10 : y(lv) - 30;
    el('rect', { x: x(n - 1) - bw - 2, y: by, width: bw, height: 20, rx: 3, fill: COLORS.text }, svg);
    el('text', { x: x(n - 1) - bw / 2 - 2, y: by + 13.5, 'text-anchor': 'middle', 'font-size': 11, fill: '#fff', 'font-weight': 700 }, svg, label);

    // hover
    const guide = el('line', { y1: T, y2: H - B, stroke: COLORS.axis, 'stroke-dasharray': '2 2', visibility: 'hidden' }, svg);
    const overlay = el('rect', { x: L, y: T, width: W - L - R, height: H - T - B, fill: 'transparent' }, svg);
    overlay.addEventListener('mousemove', (e) => {
        const r = svg.getBoundingClientRect();
        const i = Math.round((e.clientX - r.left - L) / (W - L - R) * (n - 1));
        if (i < 0 || i >= n) return;
        guide.setAttribute('x1', x(i)); guide.setAttribute('x2', x(i)); guide.setAttribute('visibility', 'visible');
        const u = t.unit === '%' ? '%' : ` ${t.unit}`;
        const dd = meta.unit === '%' ? meta.digits : 0;
        let html = `<b>${esc(t.labels[i])}</b>${i === n - 1 && t.labels[0].includes(':') ? ' <span class="muted">(진행 중)</span>' : ''}<br>현재 ${fmt(t.today[i], dd)}${u}`;
        if (t.prev) html += `<br><span class="muted">${esc(t.prev_label)} ${fmt(t.prev[i], dd)}${u}</span>`;
        showTip(html, e.clientX, e.clientY);
    });
    overlay.addEventListener('mouseleave', () => { guide.setAttribute('visibility', 'hidden'); hideTip(); });
}

function drawRanking(svg, rk) {
    const W = svg.clientWidth || 500, L = rk.items.some((x) => x.proc) ? 112 : 64, R = 48, rowH = 26;
    const max = rk.max || Math.max(...rk.items.map((x) => x.value), 1);
    rk.items.forEach((d, i) => {
        const yy = 3 + i * rowH, w = Math.max(1, (W - L - R) * d.value / max);
        el('text', { x: 0, y: yy + 13, 'font-size': 11.5, fill: COLORS.text, 'font-weight': 700, class: 'eqlabel' }, svg, d.eq);
        if (d.proc) el('text', { x: 58, y: yy + 13, 'font-size': 10.5, fill: COLORS.axis }, svg, d.proc);
        el('rect', { x: L, y: yy + 3, width: W - L - R, height: 14, fill: COLORS.track, rx: 2 }, svg);
        el('rect', { x: L, y: yy + 3, width: w, height: 14, fill: d.alert ? COLORS.red : i < 5 ? COLORS.navy : COLORS.navy3, rx: 2 }, svg);
        el('text', { x: L + w + 6, y: yy + 14, 'font-size': 11, fill: COLORS.text2, 'font-weight': 600 }, svg, fmt(d.value, rk.digits));
    });
}

function drawHeatmap(svg, hm) {
    const W = svg.clientWidth || 700, L = 64, T = 2, B = 22;
    const rows = hm.rows.length, cols = hm.cols.length;
    const H = rows * 25 + 24;
    const cw = (W - L) / cols, ch = (H - T - B) / Math.max(rows, 1);
    const span = (hm.max - hm.min) || 1;
    hm.rows.forEach((eq, r) => {
        el('text', { x: 0, y: T + r * ch + ch / 2 + 4, 'font-size': 11, fill: COLORS.text, 'font-weight': 600, class: 'eqlabel' }, svg, eq);
        const rowAvg = hm.values[r].reduce((s, v) => s + v, 0) / cols;
        hm.values[r].forEach((v, c) => {
            let a = (v - hm.min) / span;
            if (hm.mode === 'low_bad') a = 1 - a;
            a = Math.max(0, Math.min(1, a));
            let alert;
            if (hm.mode === 'volume') alert = v < rowAvg * 0.3;
            else if (hm.mode === 'low_bad') alert = v < hm.alert;
            else alert = v >= hm.alert;
            let fill;
            if (alert) fill = `rgba(214,50,50,${0.45 + a * 0.5})`;
            else if (a < 0.04 && hm.mode !== 'volume') fill = COLORS.track;
            else fill = `rgba(20,40,160,${0.08 + a * 0.82})`;
            const rect = el('rect', { x: L + c * cw + 1, y: T + r * ch + 1, width: Math.max(1, cw - 2), height: ch - 2, fill, rx: 2 }, svg);
            el('title', {}, rect, `${eq} · ${hm.cols[c]} · ${fmt(v, digitsFor(v))}${hm.unit}`);
        });
    });
    const every = cols > 16 ? 3 : 1;
    hm.cols.forEach((lb, c) => {
        if (c % every === 0) el('text', { x: L + c * cw + cw / 2, y: H - 6, 'text-anchor': 'middle', 'font-size': 10, fill: COLORS.axis }, svg, lb);
    });
}

// ─── Export ────────────────────────────────
function exportCSV() {
    const d = state.detail;
    if (!d) return;
    const q = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
    const lines = [];
    lines.push([q('공정'), q(d.label), q('KPI'), q(d.meta.name), q('값'), q(d.kpi.display), q('목표'), q(d.kpi.target_text), q('기준시각'), q(d.updated)].join(','));
    lines.push('');
    lines.push([q('구간'), q('현재'), q('전일'), q('목표')].join(','));
    d.trend.labels.forEach((lb, i) => lines.push([q(lb), d.trend.today[i], d.trend.prev ? d.trend.prev[i] : '', d.trend.target].join(',')));
    lines.push('');
    lines.push([q('설비'), q(`${d.ranking.title} (${d.ranking.unit})`)].join(','));
    d.ranking.items.forEach((x) => lines.push([q(x.eq), x.value].join(',')));
    lines.push('');
    lines.push(d.events.columns.map(q).join(','));
    d.events.rows.forEach((r) => lines.push(r.cells.map((c) => q(c.t)).join(',')));
    const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `KPI_${d.process}_${d.kpi.id}_${new Date().toISOString().slice(0, 16).replace(/[-:T]/g, '')}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
}

async function copyLink(btn) {
    try {
        await navigator.clipboard.writeText(location.href);
        btn.textContent = '복사됨';
    } catch (e) {
        btn.textContent = '복사 실패';
    }
    setTimeout(() => { btn.textContent = '링크 복사'; }, 1500);
}

// ─── Events ────────────────────────────────
document.addEventListener('click', (e) => {
    const tab = e.target.closest('[data-proc]');
    if (tab) { selectProcess(tab.dataset.proc); return; }
    const card = e.target.closest('[data-kpi]');
    if (card) { selectKpi(card.dataset.kpi); return; }
    const act = e.target.closest('[data-action]');
    if (act?.dataset.action === 'csv') exportCSV();
    if (act?.dataset.action === 'copy') copyLink(act);
});

let resizeTimer;
window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(renderDetail, 150);
});

window.addEventListener('hashchange', () => {
    const before = `${state.process}/${state.kpi}`;
    readHash();
    if (`${state.process}/${state.kpi}` !== before) loadAll();
});

// ─── Init ────────────────────────────────
readHash();
loadSources();
loadAll().catch((e) => console.error(e));
connectWS();
