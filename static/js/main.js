// K2 KPI Board - Frontend
// 공정 탭 → 실시간 KPI 카드 / 공통 KPI 스코어카드 → 선택 시 같은 페이지 하단 상세 분석

// ─── State ────────────────────────────────
const state = {
    process: 'all',
    kpi: null,
    ws: null,
    processes: [],
    cards: null,
    custom: null,
    detail: null,
    editing: null, // 수정 중인 공통 KPI id (null = 신규)
    editMode: false, // 레이아웃 편집 모드
    layout: null, // 실시간 KPI 배치 · 기본값 (/api/layout)
    drag: null, // 드래그 중인 대상 {type, id}
    editingCore: null,
    editingCat: null,
};

const BADGE = { ok: '달성', warn: '주의', bad: '미달', none: '목표 없음' };
const NS = 'http://www.w3.org/2000/svg';

// ─── Theme ────────────────────────────────
function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function palette() {
    return {
        navy: cssVar('--navy'), navy2: cssVar('--navy-2'), navy3: cssVar('--navy-3'), navy4: cssVar('--navy-4'),
        red: cssVar('--red'), amber: cssVar('--amber'), grid: cssVar('--grid'), axis: cssVar('--text3'),
        text: cssVar('--text'), text2: cssVar('--text2'), track: cssVar('--track'), surface: cssVar('--surface'),
        tip: cssVar('--tip-bg'), accentRgb: cssVar('--accent-rgb'), redRgb: cssVar('--red-rgb'),
        series: [cssVar('--navy'), cssVar('--navy-2'), cssVar('--navy-3'), cssVar('--navy-4'), cssVar('--line-strong')],
    };
}

const SUN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
const MOON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';

function currentTheme() {
    return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
}

function setTheme(t) {
    document.documentElement.setAttribute('data-theme', t);
    try { localStorage.setItem('kpi-theme', t); } catch (e) { /* 저장 불가 환경 무시 */ }
    $('themeBtn').innerHTML = t === 'dark' ? SUN : MOON;
    renderAll();
}

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
    if (hi === lo) { hi = lo + 1; lo -= 1; }
    const raw = (hi - lo) / ticks;
    const mag = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw);
    return { lo: Math.floor(lo / step) * step, hi: Math.ceil(hi / step) * step, step };
}

function digitsFor(v) {
    const a = Math.abs(v);
    return a >= 100 ? 0 : a >= 10 ? 1 : 2;
}

function unitSuffix(u) {
    if (!u) return '';
    return u === '%' ? '%' : ` ${u}`;
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
async function api(path, opts) {
    const res = await fetch(path, opts);
    if (!res.ok) {
        let msg = `${res.status}`;
        try { msg = (await res.json()).detail || msg; } catch (e) { /* 본문 없음 */ }
        throw new Error(msg);
    }
    return res.json();
}

const sendJSON = (path, method, body) => api(path, {
    method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});

function allKpiIds() {
    const ids = (state.cards?.cards || []).map((c) => c.id);
    (state.custom?.categories || []).forEach((c) => c.cards.forEach((k) => ids.push(k.id)));
    return ids;
}

async function loadAll({ scroll = false } = {}) {
    const [procs, cards, custom, lay] = await Promise.all([
        api('/api/processes'), api(`/api/kpis?process=${state.process}`), api(`/api/custom-kpis?process=${state.process}`),
        api('/api/layout'),
    ]);
    state.layout = lay;
    state.processes = procs;
    state.cards = cards;
    state.custom = custom;
    if (!state.kpi || !allKpiIds().includes(state.kpi)) {
        const shown = cards.cards.filter((c) => !c.hidden);
        state.kpi = (shown.find((c) => c.status === 'bad') || shown[0]).id;
    }
    renderTabs();
    renderCards();
    renderCats();
    await loadDetail({ scroll });
}

async function loadDetail({ scroll = false } = {}) {
    writeHash();
    subscribe();
    state.detail = await api(`/api/kpis/${encodeURIComponent(state.kpi)}?process=${state.process}`);
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
    renderCats();
    loadDetail({ scroll: true });
}

function renderAll() {
    renderCards();
    renderCats();
    renderDetail();
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
        if (msg.type !== 'kpi_update' || msg.cards.process !== state.process) return;
        state.processes = msg.processes;
        state.cards = msg.cards;
        state.custom = msg.custom;
        state.layout = msg.layout;
        renderTabs();
        if (!state.drag) {
            renderCards();
            renderCats();
        }
        if (msg.detail && msg.detail.kpi.id === state.kpi && msg.detail.process === state.process) {
            state.detail = msg.detail;
            // 값 입력 중에는 상세 영역을 다시 그리지 않음 (입력 내용 보호)
            if (!document.activeElement?.closest('.matrix')) renderDetail();
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
        return `<button class="proc-tab ${p.id === state.process ? 'on' : ''}" data-proc="${p.id}">${esc(p.name)}${cnt}</button>`;
    }).join('');
}

// ─── 실시간 KPI 카드 ────────────────────────────────
function sparkPath(data, w, h) {
    const mn = Math.min(...data), mx = Math.max(...data);
    const pts = data.map((v, i) => [i / Math.max(1, data.length - 1) * w, h - 3 - (v - mn) / (mx - mn || 1) * (h - 6)]);
    return { d: pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' '), last: pts[pts.length - 1] };
}

function sparkWide(data, color) {
    if (data.length < 2) return '<svg class="spark"></svg>';
    const { d } = sparkPath(data, 100, 38);
    return `<svg class="spark" viewBox="0 0 100 38" preserveAspectRatio="none" aria-hidden="true">`
        + `<path d="${d} L100 38 L0 38Z" fill="${color}" opacity=".08"/>`
        + `<path d="${d}" fill="none" stroke="${color}" stroke-width="1.7" vector-effect="non-scaling-stroke"/></svg>`;
}

function sparkSmall(data, color) {
    if (data.length < 2) return '<svg></svg>';
    const { d, last } = sparkPath(data, 96, 30);
    return `<svg viewBox="0 0 96 30" aria-hidden="true"><path d="${d}" fill="none" stroke="${color}" stroke-width="1.6"/>`
        + `<circle cx="${last[0]}" cy="${last[1]}" r="2.5" fill="${color}"/></svg>`;
}

function renderCards() {
    const c = state.cards;
    if (!c) return;
    const P = palette();
    $('cardsTitle').textContent = `${c.label} · 실시간 KPI`;
    $('updated').textContent = kstTime(c.updated, true);
    $('summary').innerHTML = `<span><i style="background:var(--navy)"></i>달성 ${c.summary.ok}</span>`
        + `<span><i style="background:var(--amber)"></i>주의 ${c.summary.warn}</span>`
        + `<span><i style="background:var(--red)"></i>미달 ${c.summary.bad}</span>`;
    const edit = state.editMode;
    $('kpis').innerHTML = c.cards.filter((k) => edit || !k.hidden).map((k) => {
        const sel = k.id === state.kpi && !edit;
        const color = k.status === 'bad' ? P.red : P.navy;
        const sub = k.sub ? `<span class="kpi-sub">${esc(k.sub)}</span>` : '';
        const ctl = edit ? `<div class="edit-ctl">${ICON.grip}<span class="sp"></span>
              ${ib('left', 'core-move', k.id, '앞으로', 'data-dir="-1"')}${ib('right', 'core-move', k.id, '뒤로', 'data-dir="1"')}
              ${ib(k.hidden ? 'eyeOff' : 'eye', 'core-hide', k.id, k.hidden ? '다시 표시' : '숨기기')}${ib('pencil', 'core-edit', k.id, '이름 · 목표 수정')}</div>` : '';
        return `<div class="kpi ${sel ? 'sel' : ''} ${edit ? 'editing' : ''} ${k.hidden ? 'is-hidden' : ''}" data-kpi="${k.id}" data-drag="core"
            ${edit ? 'draggable="true"' : 'role="button" tabindex="0"'} aria-pressed="${sel}">
            ${ctl}
            <div class="kpi-top"><div><div class="kpi-name">${esc(k.name)}</div><div class="kpi-en">${esc(k.en)}</div></div>
              <span class="badge ${sel ? 'sel' : k.hidden ? 'none' : k.status}">${sel ? '선택' : k.hidden ? '숨김' : BADGE[k.status]}</span></div>
            <div class="kpi-val num ${k.text ? 'txt' : ''} ${k.status === 'bad' ? 'bad' : ''}">${esc(k.display)}<small>${esc(k.unit)}</small>${sub}</div>
            <div class="kpi-delta ${k.delta_class}">${esc(k.delta)}</div>
            ${sparkWide(k.spark, color)}
            <div class="kpi-foot"><div class="row"><span>목표 <b>${esc(k.target_text)}</b></span><b class="num">${k.pct}%</b></div>
              <div class="bar"><i class="${k.status}" style="width:${k.pct}%"></i></div></div>
        </div>`;
    }).join('');
}

// ─── 공통 KPI 스코어카드 ────────────────────────────────
function renderCats() {
    const cu = state.custom;
    if (!cu) return;
    const P = palette();
    $('catList').innerHTML = cu.categories.map((c) => `<option value="${esc(c.name)}">`).join('');
    const edit = state.editMode;
    $('cats').innerHTML = cu.categories.map((cat, ci) => {
        const s = cat.summary;
        const rows = cat.cards.filter((k) => edit || !k.hidden).map((k) => {
            const sel = k.id === state.kpi && !edit;
            const color = k.status === 'bad' ? P.red : P.navy;
            const ctl = edit ? `<span class="row-ctl">${ib('up', 'row-move', k.id, '위로', 'data-dir="-1"')}${ib('down', 'row-move', k.id, '아래로', 'data-dir="1"')}
                ${ib(k.hidden ? 'eyeOff' : 'eye', 'row-hide', k.id, k.hidden ? '다시 표시' : '숨기기')}${ib('pencil', 'row-edit', k.id, '내용 수정')}</span>` : '';
            return `<div class="ck-row ${sel ? 'sel' : ''} ${edit ? 'editing' : ''} ${k.hidden ? 'is-hidden' : ''}" data-kpi="${esc(k.id)}" data-drag="row"
                ${edit ? 'draggable="true"' : 'role="button" tabindex="0"'}>
                ${edit ? ICON.grip : ''}<span class="dot ${k.status === 'ok' ? '' : k.status}" title="${BADGE[k.status]}"></span>
                <span class="ck-name">${esc(k.name)}${k.demo ? '<span class="demo-tag">예시</span>' : ''}<small>${esc(k.en || '')}${k.period ? ` · ${k.period}` : ''}</small></span>
                <span class="ck-val num ${k.status === 'bad' ? 'bad' : ''}">${esc(k.display)}<small>${k.value == null ? '' : esc(k.unit)}</small></span>
                ${sparkSmall(k.spark, color)}
                <span class="ck-meta">목표 ${esc(k.target_text)}${k.status !== 'none' ? ` · ${k.pct}%` : ''}
                  <span class="d ${k.delta_class}">${esc(k.delta)}</span></span>${ctl}
            </div>`;
        }).join('');
        const headCtl = edit ? `<span class="cat-ctl">${ib('up', 'cat-move', cat.id, '앞으로', 'data-dir="-1"')}${ib('down', 'cat-move', cat.id, '뒤로', 'data-dir="1"')}
              ${ib('pencil', 'cat-edit', cat.id, '카테고리 이름 수정')}${cat.cards.length ? '' : ib('trash', 'cat-delete', cat.id, '빈 카테고리 삭제')}</span>` : '';
        const empty = edit ? 'KPI를 여기로 끌어다 놓거나 + 추가로 만드세요.' : '등록된 KPI가 없습니다. + 추가로 항목을 만드세요.';
        return `<div class="cat ${edit ? 'editing' : ''}" data-cat="${esc(cat.id)}" data-index="${ci}">
            <div class="cat-head" ${edit ? 'draggable="true" data-drag="cat"' : ''}>${edit ? ICON.grip : ''}<div><h3>${esc(cat.name)}</h3>${cat.en ? `<div class="en">${esc(cat.en)}</div>` : ''}</div>
              <div class="counts"><span><b class="up">${s.ok}</b> 달성</span><span><b style="color:var(--amber)">${s.warn}</b> 주의</span><span><b class="down">${s.bad}</b> 미달</span></div>
              ${headCtl}<button class="btn sm" data-action="add" data-cat="${esc(cat.name)}">+ 추가</button></div>
            ${rows || `<div class="cat-empty">${empty}</div>`}
        </div>`;
    }).join('');
}

// ─── Detail ────────────────────────────────
function renderDetail() {
    const d = state.detail;
    if (!d) return;
    $('detail').hidden = false;
    if (d.kind === 'custom') renderCustomDetail(d);
    else renderCoreDetail(d);
}

function detailHead(d, crumb, titleTail, chips, extraBtns = '') {
    const k = d.kpi;
    return `<div class="detail-head">
      <div>
        <div class="crumb">${esc(crumb)}</div>
        <div class="detail-title">${esc(d.meta.name)} <span class="v num ${k.status}">${esc(k.display)}${esc(k.text || k.value == null ? '' : unitSuffix(k.unit))}</span><span class="t">${titleTail}</span></div>
      </div>
      <div class="spacer"></div>
      <div class="conn">${chips}</div>
      ${extraBtns}
      <button class="btn" data-action="copy">링크 복사</button>
      <button class="btn primary" data-action="csv">Excel(CSV) 내보내기</button>
    </div>`;
}

function renderCoreDetail(d) {
    const m = d.meta, k = d.kpi;
    const box = $('detail');
    const P = palette();
    const tgt = k.text ? `대기 WIP ${fmt(k.value)}wf · 기준 ${fmt(m.target)}wf` : `목표 ${esc(k.target_text)} · ${esc(k.delta)}`;
    const keepScroll = box.querySelector('.tbl-wrap')?.scrollLeft || 0;

    box.innerHTML = `
    ${detailHead(d, `${d.label} › 실시간 KPI › 상세 분석`, tgt, `연결 데이터 ${m.sources.map((s) => `<span class="chip"><i class="dot"></i>${esc(s)}</span>`).join('')}`)}
    <div class="mini">${d.stats.map(renderStat).join('')}</div>
    <div class="dgrid">
      <div class="panel c8">
        <h3>${esc(d.trend.title)} <span class="src-tag">${esc(m.sources[0])}</span></h3>
        <div class="sub">${esc(d.trend.sub)}</div>
        ${trendLegend(d.trend, d.kpi.id === 'bottleneck' ? '병목 기준' : '목표', P)}
        <svg class="chart" id="trendChart" height="240"></svg>
      </div>
      <div class="panel c4 last">
        <h3>${esc(d.breakdown.title)} <span class="src-tag">${esc(d.breakdown.src)}</span></h3>
        <div class="sub">${esc(d.breakdown.sub)}</div>
        ${renderBreakdown(d.breakdown, P)}
      </div>
      <div class="panel c5">
        <h3>${esc(d.ranking.title)} <span class="src-tag">${esc(d.ranking.src)}</span></h3>
        <div class="sub">${esc(d.ranking.sub)}</div>
        <svg class="chart" id="rankChart" height="${d.ranking.items.length * 28 + 6}"></svg>
      </div>
      <div class="panel c7 last">
        <h3>${esc(d.heatmap.title)} <span class="src-tag">${esc(d.ranking.src)}</span></h3>
        <div class="sub">${esc(d.heatmap.sub)}</div>
        <svg class="chart" id="heatChart" height="${d.heatmap.rows.length * 27 + 24}"></svg>
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
          ${renderInsights(d.insights)}
        </div>
      </div>
    </div>
    <div class="foot-note"><span>데이터 원천: ${m.sources.map(esc).join(' / ')}</span><span>${esc(m.formula)}</span><span>최종 동기화 ${kstTime(d.updated)}</span></div>`;

    drawTrend($('trendChart'), d.trend, m.unit === '%' ? m.digits : 0, P);
    drawRanking($('rankChart'), d.ranking, P);
    drawHeatmap($('heatChart'), d.heatmap, P);
    const tw = box.querySelector('.tbl-wrap');
    if (tw) tw.scrollLeft = keepScroll;
}

function renderCustomDetail(d) {
    const m = d.meta, k = d.kpi;
    const P = palette();
    const tail = `목표 ${esc(k.target_text)}${k.period ? ` · ${esc(k.period)} 기준` : ''} · ${esc(k.delta)}`;
    const chips = `<span class="chip">${esc(m.category_name)}</span><span class="chip"><i class="dot"></i>수기 입력</span>`
        + (m.demo ? '<span class="chip" style="color:var(--amber)">예시 데이터</span>' : '');
    const btns = `<button class="btn" data-action="edit">편집</button><button class="btn danger" data-action="delete">삭제</button>`;
    const mx = d.matrix;
    const dg = m.digits;
    const cell = (row, i) => {
        const v = row.values[i];
        const ph = row.id === 'all' && row.computed[i] != null ? `자동 ${fmt(row.computed[i], dg)}` : '';
        return `<td><input type="number" step="any" inputmode="decimal" data-proc="${row.id}" data-period="${mx.periods[i]}"
            value="${v == null ? '' : v}" placeholder="${esc(ph)}" aria-label="${esc(row.name)} ${mx.periods[i]}"></td>`;
    };
    const trendBox = d.trend
        ? `${trendLegend(d.trend, '목표', P)}<svg class="chart" id="trendChart" height="240"></svg>`
        : '<div class="empty">입력된 값이 없습니다. 아래 표에 값을 입력하세요.</div>';

    $('detail').innerHTML = `
    ${detailHead(d, `${d.label} › 공통 KPI › ${m.category_name}`, tail, chips, btns)}
    <div class="mini">${d.stats.map(renderStat).join('')}</div>
    <div class="dgrid">
      <div class="panel c8">
        <h3>${d.trend ? esc(d.trend.title) : '월별 추이'} <span class="src-tag">수기 입력</span></h3>
        <div class="sub">${d.trend ? esc(d.trend.sub) : ''}</div>
        ${trendBox}
      </div>
      <div class="panel c4 last">
        <h3>공정별 비교 <span class="src-tag">8대 공정</span></h3>
        <div class="sub">최근 입력값 · 행을 누르면 해당 공정으로 이동</div>
        ${renderCompare(d.compare, dg)}
      </div>
      <div class="panel c8 end">
        <h3>월별 입력 <span class="save-state" id="saveState">셀을 고치면 바로 저장되고 모든 사용자 화면에 반영됩니다</span></h3>
        <div class="sub">단위: ${esc(m.unit || '-')} · All 행을 비워두면 공정 값을 ${mx.agg === 'sum' ? '합계' : '평균'}해 자동 계산 · 값을 지우면 삭제</div>
        <div class="tbl-wrap"><table class="matrix"><thead><tr><th>공정</th>${mx.periods.map((p) => `<th>${p}</th>`).join('')}</tr></thead>
          <tbody>${mx.rows.map((r) => `<tr class="${r.id === 'all' ? 'all-row' : ''}"><td>${esc(r.name)}</td>${mx.periods.map((_, i) => cell(r, i)).join('')}</tr>`).join('')}</tbody></table></div>
      </div>
      <div class="c4 stack">
        <div class="panel last">
          <h3>분석 요약 <span class="src-tag">자동 분석</span></h3>
          <div class="sub">입력값 기반 규칙 분석</div>
          ${renderInsights(d.insights)}
        </div>
        <div class="panel last end">
          <h3>정의</h3>
          <dl class="desc">
            <dt>설명</dt><dd>${esc(m.description || '-')}</dd>
            <dt>방향 · 집계</dt><dd>${m.lower ? '낮을수록 좋음' : '높을수록 좋음'} · All = 공정 ${m.agg === 'sum' ? '합계' : '평균'}</dd>
            <dt>담당</dt><dd>${esc(m.owner || '-')}</dd>
          </dl>
        </div>
      </div>
    </div>
    <div class="foot-note"><span>데이터 원천: 수기 입력 (공통 KPI)</span><span>KPI ID: ${esc(m.id)}</span><span>갱신 ${kstTime(d.updated)}</span></div>`;

    if (d.trend) drawTrend($('trendChart'), d.trend, dg, P);
}

function trendLegend(t, targetLabel, P) {
    const tg = t.target == null ? '' : `<span><i style="background:${P.red}"></i>${targetLabel} ${fmt(t.target, digitsFor(t.target))}${t.unit === '%' ? '%' : ''}</span>`;
    return `<div class="legend"><span><i style="background:${P.navy}"></i>${esc(t.series_label)}</span>
        ${t.prev ? `<span><i style="background:${P.navy4}"></i>${esc(t.prev_label)}</span>` : ''}${tg}</div>`;
}

function renderInsights(list) {
    return `<div class="insight">${list.map((i) => `<div class="ins ${i.level}"><b>${esc(i.title)}</b><p>${esc(i.text)}</p></div>`).join('')}</div>`;
}

function renderStat(s) {
    const delta = s.delta ? `<em class="${s.tone === 'bad' ? 'bad' : s.tone === 'good' ? 'good' : ''}">${esc(s.delta)}</em>` : '';
    const note = s.note ? `<em>${esc(s.note)}</em>` : '';
    return `<div><span>${esc(s.label)}</span><b class="num ${s.mono ? 'mono' : ''} ${s.tone === 'alert' ? 'alert' : ''}">${esc(s.value)}<small>${esc(s.unit)}</small></b>${delta}${note}</div>`;
}

function renderBreakdown(b, P) {
    const items = b.items || [];
    const tot = items.reduce((s, x) => s + x.value, 0) || 1;
    const max = b.max || Math.max(...items.map((x) => x.value), 1);
    const color = (i) => P.series[i % P.series.length];
    const stack = b.share
        ? `<div class="stackbar">${items.map((x, i) => `<i style="flex:${x.value || 0.0001};background:${color(i)}" title="${esc(x.name)}"></i>`).join('')}</div>`
        : '';
    const digits = items.some((x) => !Number.isInteger(x.value)) ? 1 : 0;
    const top = b.share ? Math.max(...items.map((y) => y.value), 1) : max;
    return stack + items.map((x, i) => `
        <div class="bd-row">
          <span class="sw" style="background:${color(i)}"></span>
          <span class="nm">${esc(x.name)}</span>
          <span class="sh">${b.share ? `${(x.value / tot * 100).toFixed(0)}%` : ''}</span>
          <span class="vl num">${fmt(x.value, digits)}${esc(b.unit)}</span>
          <span class="tr"><i style="width:${(x.value / top * 100).toFixed(1)}%;background:${i >= 3 ? P.navy3 : color(i)}"></i></span>
        </div>`).join('');
}

function renderCompare(c, digits) {
    const items = c.items;
    const vals = items.map((x) => (x.value == null ? null : c.by_pct ? x.value / x.target * 100 : x.value));
    const known = vals.filter((v) => v != null);
    const scaleMax = Math.max(...known, c.target || 0, 0) * 1.08 || 1;
    const tgtPos = c.by_pct ? 100 / scaleMax * 100 : c.target ? c.target / scaleMax * 100 : null;
    return items.map((x, i) => {
        const w = vals[i] == null ? 0 : vals[i] / scaleMax * 100;
        let val;
        if (vals[i] == null) val = '-';
        else if (c.by_pct) val = `${vals[i].toFixed(1)}%`;
        else val = (digits != null ? fmt(x.value, digits) : x.display) + (c.unit === '%' ? '%' : '');
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
function drawTrend(svg, t, vd, P) {
    const W = svg.clientWidth || 800, H = 240, L = 54, R = 14, T = 16, B = 26;
    const hasTarget = t.target != null;
    const all = [...t.today, ...(t.prev || []), ...(hasTarget ? [t.target] : [])];
    let lo = Math.min(...all), hi = Math.max(...all);
    const span = hi - lo || Math.abs(hi) * 0.2 || 1;
    lo = lo < span * 0.6 ? 0 : lo - span * 0.25;
    hi += span * 0.12;
    if (t.unit === '%' && hi > 100 && Math.max(...t.today) <= 100) hi = 100;
    const sc = niceScale(lo, hi);
    const n = t.today.length;
    const x = (i) => L + (n === 1 ? 0.5 : i / (n - 1)) * (W - L - R);
    const y = (v) => T + (1 - (v - sc.lo) / (sc.hi - sc.lo)) * (H - T - B);
    const dg = sc.step < 1 ? (sc.step < 0.1 ? 2 : 1) : 0;
    const hourly = t.labels[0] && t.labels[0].includes(':');

    for (let v = sc.lo; v <= sc.hi + 1e-9; v += sc.step) {
        el('line', { x1: L, x2: W - R, y1: y(v), y2: y(v), stroke: P.grid }, svg);
        el('text', { x: L - 8, y: y(v) + 4, 'text-anchor': 'end', 'font-size': 11, fill: P.axis }, svg, fmt(v, dg));
    }
    const every = n > 16 ? 3 : n > 8 ? 2 : 1;
    t.labels.forEach((lb, i) => {
        if (i % every === 0) el('text', { x: x(i), y: H - 6, 'text-anchor': 'middle', 'font-size': 11, fill: P.axis }, svg, lb);
    });
    if (hourly) {
        t.labels.forEach((lb, i) => {
            if (['06:00', '14:00', '22:00'].includes(lb)) {
                const x0 = x(Math.max(0, i - 1)), x1 = x(Math.min(n - 1, i + 1));
                el('rect', { x: x0, y: T, width: x1 - x0, height: H - T - B, fill: P.navy, opacity: 0.05 }, svg);
                el('text', { x: x(i), y: T + 10, 'text-anchor': 'middle', 'font-size': 10.5, fill: P.axis }, svg, '교대');
            }
        });
    }
    if (hasTarget) {
        el('line', { x1: L, x2: W - R, y1: y(t.target), y2: y(t.target), stroke: P.red, 'stroke-dasharray': '4 3', 'stroke-width': 1.2 }, svg);
    }
    const path = (a) => a.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(' ');
    if (t.prev) el('path', { d: path(t.prev), fill: 'none', stroke: P.navy4, 'stroke-width': 1.7 }, svg);
    el('path', { d: `${path(t.today)} L${x(n - 1)} ${y(sc.lo)} L${x(0)} ${y(sc.lo)}Z`, fill: P.navy, opacity: 0.07 }, svg);
    el('path', { d: path(t.today), fill: 'none', stroke: P.navy, 'stroke-width': 2.3 }, svg);
    const gaps = new Set(t.gaps || []);
    t.today.forEach((v, i) => {
        const breach = hasTarget && (t.lower ? v > t.target : v < t.target);
        if (gaps.has(i)) el('circle', { cx: x(i), cy: y(v), r: 3, fill: P.surface, stroke: P.axis, 'stroke-dasharray': '2 1.5' }, svg);
        else if (breach) el('circle', { cx: x(i), cy: y(v), r: 2.8, fill: P.surface, stroke: P.red, 'stroke-width': 1.5 }, svg);
        else if (!hourly) el('circle', { cx: x(i), cy: y(v), r: 2.6, fill: P.navy }, svg);
    });
    const lv = t.today[n - 1];
    const label = fmt(lv, vd) + (t.unit === '%' ? '%' : '');
    el('circle', { cx: x(n - 1), cy: y(lv), r: 4.5, fill: P.navy }, svg);
    const bw = label.length * 7.2 + 14;
    const by = y(lv) - 32 < T ? y(lv) + 10 : y(lv) - 32;
    el('rect', { x: x(n - 1) - bw - 2, y: by, width: bw, height: 22, rx: 3, fill: P.tip }, svg);
    el('text', { x: x(n - 1) - bw / 2 - 2, y: by + 15, 'text-anchor': 'middle', 'font-size': 12, fill: '#fff', 'font-weight': 700 }, svg, label);

    const guide = el('line', { y1: T, y2: H - B, stroke: P.axis, 'stroke-dasharray': '2 2', visibility: 'hidden' }, svg);
    const overlay = el('rect', { x: L, y: T, width: W - L - R, height: H - T - B, fill: 'transparent' }, svg);
    overlay.addEventListener('mousemove', (e) => {
        const r = svg.getBoundingClientRect();
        const i = n === 1 ? 0 : Math.round((e.clientX - r.left - L) / (W - L - R) * (n - 1));
        if (i < 0 || i >= n) return;
        guide.setAttribute('x1', x(i)); guide.setAttribute('x2', x(i)); guide.setAttribute('visibility', 'visible');
        const u = unitSuffix(t.unit);
        let html = `<b>${esc(t.labels[i])}</b>${i === n - 1 && hourly ? ' <span class="muted">(진행 중)</span>' : ''}`
            + `${gaps.has(i) ? ' <span class="muted">(미입력 · 직전 값)</span>' : ''}<br>현재 ${fmt(t.today[i], vd)}${u}`;
        if (t.prev) html += `<br><span class="muted">${esc(t.prev_label)} ${fmt(t.prev[i], vd)}${u}</span>`;
        showTip(html, e.clientX, e.clientY);
    });
    overlay.addEventListener('mouseleave', () => { guide.setAttribute('visibility', 'hidden'); hideTip(); });
}

function drawRanking(svg, rk, P) {
    const W = svg.clientWidth || 500, L = rk.items.some((x) => x.proc) ? 128 : 70, R = 52, rowH = 28;
    const max = rk.max || Math.max(...rk.items.map((x) => x.value), 1);
    rk.items.forEach((d, i) => {
        const yy = 3 + i * rowH, w = Math.max(1, (W - L - R) * d.value / max);
        el('text', { x: 0, y: yy + 14, 'font-size': 12.5, fill: P.text, 'font-weight': 700, class: 'eqlabel' }, svg, d.eq);
        if (d.proc) el('text', { x: 64, y: yy + 14, 'font-size': 11.5, fill: P.axis }, svg, d.proc);
        el('rect', { x: L, y: yy + 3, width: W - L - R, height: 16, fill: P.track, rx: 2 }, svg);
        el('rect', { x: L, y: yy + 3, width: w, height: 16, fill: d.alert ? P.red : i < 5 ? P.navy : P.navy3, rx: 2 }, svg);
        el('text', { x: L + w + 6, y: yy + 15.5, 'font-size': 12, fill: P.text2, 'font-weight': 600 }, svg, fmt(d.value, rk.digits));
    });
}

function drawHeatmap(svg, hm, P) {
    const W = svg.clientWidth || 700, L = 70, T = 2, B = 22;
    const rows = hm.rows.length, cols = hm.cols.length;
    const H = rows * 27 + 24;
    const cw = (W - L) / cols, ch = (H - T - B) / Math.max(rows, 1);
    const span = (hm.max - hm.min) || 1;
    hm.rows.forEach((eq, r) => {
        el('text', { x: 0, y: T + r * ch + ch / 2 + 4, 'font-size': 12, fill: P.text, 'font-weight': 600, class: 'eqlabel' }, svg, eq);
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
            if (alert) fill = `rgba(${P.redRgb},${0.45 + a * 0.5})`;
            else if (a < 0.04 && hm.mode !== 'volume') fill = P.track;
            else fill = `rgba(${P.accentRgb},${0.1 + a * 0.8})`;
            const rect = el('rect', { x: L + c * cw + 1, y: T + r * ch + 1, width: Math.max(1, cw - 2), height: ch - 2, fill, rx: 2 }, svg);
            el('title', {}, rect, `${eq} · ${hm.cols[c]} · ${fmt(v, digitsFor(v))}${hm.unit}`);
        });
    });
    const every = cols > 16 ? 3 : 1;
    hm.cols.forEach((lb, c) => {
        if (c % every === 0) el('text', { x: L + c * cw + cw / 2, y: H - 6, 'text-anchor': 'middle', 'font-size': 11, fill: P.axis }, svg, lb);
    });
}

// ─── 공통 KPI 편집 ────────────────────────────────
function openModal(kpiMeta, category) {
    const f = $('kpiForm');
    f.reset();
    $('formErr').hidden = true;
    state.editing = kpiMeta ? kpiMeta.id : null;
    $('modalTitle').textContent = kpiMeta ? 'KPI 편집' : 'KPI 추가';
    if (kpiMeta) {
        f.category.value = kpiMeta.category_name;
        f.name.value = kpiMeta.name;
        f.en.value = kpiMeta.en || '';
        f.unit.value = kpiMeta.unit || '';
        f.target.value = kpiMeta.target ?? '';
        f.lower.value = String(kpiMeta.lower);
        f.agg.value = kpiMeta.agg;
        f.digits.value = String(kpiMeta.digits);
        f.owner.value = kpiMeta.owner || '';
        f.description.value = kpiMeta.description || '';
    } else if (category) {
        f.category.value = category;
    }
    $('modal').hidden = false;
    (category && !kpiMeta ? f.name : f.category).focus();
}

function closeModal() {
    $('modal').hidden = true;
    state.editing = null;
}

function closeModals() {
    closeModal();
    $('coreModal').hidden = true;
    $('catModal').hidden = true;
    state.editingCore = null;
    state.editingCat = null;
}

async function submitModal(e) {
    e.preventDefault();
    const f = e.target;
    const body = {
        category: f.category.value.trim(),
        name: f.name.value.trim(),
        en: f.en.value.trim(),
        unit: f.unit.value.trim(),
        target: f.target.value === '' ? null : Number(f.target.value),
        lower: f.lower.value === 'true',
        agg: f.agg.value,
        digits: Number(f.digits.value),
        owner: f.owner.value.trim(),
        description: f.description.value.trim(),
    };
    try {
        if (state.editing) {
            await sendJSON(`/api/custom-kpis/${encodeURIComponent(state.editing)}`, 'PUT', body);
        } else {
            const res = await sendJSON('/api/custom-kpis', 'POST', body);
            state.kpi = res.id;
        }
        closeModal();
        await loadAll({ scroll: true });
    } catch (err) {
        $('formErr').textContent = `저장 실패: ${err.message}`;
        $('formErr').hidden = false;
    }
}

async function deleteKpi() {
    const m = state.detail?.meta;
    if (!m || !confirm(`'${m.name}' KPI와 입력된 값을 모두 삭제할까요? 되돌릴 수 없습니다.`)) return;
    await api(`/api/custom-kpis/${encodeURIComponent(m.id)}`, { method: 'DELETE' });
    state.kpi = null;
    await loadAll();
}

async function saveCell(input) {
    const raw = input.value.trim();
    const value = raw === '' ? null : Number(raw);
    if (raw !== '' && !Number.isFinite(value)) {
        input.classList.add('err');
        return;
    }
    const s = $('saveState');
    try {
        await sendJSON(`/api/custom-kpis/${encodeURIComponent(state.kpi)}/values`, 'PUT',
            { process: input.dataset.proc, period: input.dataset.period, value });
        input.classList.remove('err');
        input.classList.add('saved');
        if (s) s.textContent = `저장됨 · ${input.dataset.proc === 'all' ? 'All' : input.closest('tr').firstElementChild.textContent} ${input.dataset.period}`;
        const [custom, detail] = await Promise.all([
            api(`/api/custom-kpis?process=${state.process}`), api(`/api/kpis/${encodeURIComponent(state.kpi)}?process=${state.process}`),
        ]);
        state.custom = custom;
        state.detail = detail;
        renderCats();
        if (!document.activeElement?.closest('.matrix')) renderDetail();
    } catch (err) {
        input.classList.add('err');
        if (s) s.textContent = `저장 실패: ${err.message}`;
    }
}

// ─── 레이아웃 편집 (순서 · 카테고리 · 표시 · 이름/목표) ────────────────────────────────
const ICON = {
    grip: '<svg class="grip" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="9" cy="6" r="1.6"/><circle cx="15" cy="6" r="1.6"/><circle cx="9" cy="12" r="1.6"/><circle cx="15" cy="12" r="1.6"/><circle cx="9" cy="18" r="1.6"/><circle cx="15" cy="18" r="1.6"/></svg>',
    left: '<path d="M15 6l-6 6 6 6"/>',
    right: '<path d="M9 6l6 6-6 6"/>',
    up: '<path d="M6 15l6-6 6 6"/>',
    down: '<path d="M6 9l6 6 6-6"/>',
    eye: '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    eyeOff: '<path d="M3 3l18 18M10.6 5.1A10 10 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-3.2 4.1M6.6 6.6A17 17 0 0 0 2 12s3.6 7 10 7a9.7 9.7 0 0 0 5.4-1.6M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
    pencil: '<path d="M4 20h4L19 9l-4-4L4 16v4zM14 6l4 4"/>',
    trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
};

function ib(icon, action, id, title, extra = '') {
    return `<button class="ib" type="button" data-action="${action}" data-id="${esc(id)}" ${extra} title="${esc(title)}" aria-label="${esc(title)}">`
        + `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICON[icon]}</svg></button>`;
}

function setEditMode(on) {
    state.editMode = on;
    document.body.classList.toggle('edit-mode', on);
    $('editBar').hidden = !on;
    const b = $('editBtn');
    b.textContent = on ? '편집 완료' : '레이아웃 편집';
    b.classList.toggle('primary', on);
    renderCards();
    renderCats();
}

function flash(msg, bad = false) {
    const t = $('toast');
    t.textContent = msg;
    t.classList.toggle('bad', bad);
    t.hidden = false;
    clearTimeout(flash.timer);
    flash.timer = setTimeout(() => { t.hidden = true; }, 2200);
}

async function guarded(fn, okMsg) {
    try {
        await fn();
        if (okMsg) flash(okMsg);
    } catch (err) {
        flash(`저장 실패: ${err.message}`, true);
    }
    await refreshLists();
}

async function refreshLists() {
    const [procs, cards, custom, lay] = await Promise.all([
        api('/api/processes'), api(`/api/kpis?process=${state.process}`), api(`/api/custom-kpis?process=${state.process}`), api('/api/layout'),
    ]);
    Object.assign(state, { processes: procs, cards, custom, layout: lay });
    renderTabs();
    renderCards();
    renderCats();
    if (state.detail && allKpiIds().includes(state.kpi)) {
        state.detail = await api(`/api/kpis/${encodeURIComponent(state.kpi)}?process=${state.process}`);
        renderDetail();
    }
}

// 실시간 KPI 순서 · 숨김
function coreOrder() { return state.cards.cards.map((c) => c.id); }
function coreHidden() { return state.cards.cards.filter((c) => c.hidden).map((c) => c.id); }

function saveCoreOrder(order, msg = '순서를 저장했습니다') {
    // 화면에 먼저 반영 후 저장
    const by = Object.fromEntries(state.cards.cards.map((c) => [c.id, c]));
    state.cards.cards = order.map((i) => by[i]);
    renderCards();
    return guarded(() => sendJSON('/api/layout/core', 'PUT', { order }), msg);
}

function moveCore(id, dir) {
    const order = coreOrder();
    const i = order.indexOf(id), j = i + dir;
    if (j < 0 || j >= order.length) return;
    [order[i], order[j]] = [order[j], order[i]];
    saveCoreOrder(order);
}

function toggleCoreHidden(id) {
    const hidden = new Set(coreHidden());
    if (hidden.has(id)) hidden.delete(id); else hidden.add(id);
    guarded(() => sendJSON('/api/layout/core', 'PUT', { hidden: [...hidden] }), hidden.has(id) ? '숨겼습니다 (편집 모드에서만 보임)' : '다시 표시합니다');
}

async function resetLayout() {
    if (!confirm('실시간 KPI의 순서 · 숨김 · 이름 · 목표를 모두 기본값으로 되돌릴까요?\n(공통 KPI는 그대로 유지됩니다)')) return;
    guarded(() => api('/api/layout/reset', { method: 'POST' }), '실시간 KPI를 기본 배치로 되돌렸습니다');
}

// 실시간 KPI 이름 · 목표
function openCoreModal(id) {
    const card = state.cards.cards.find((c) => c.id === id);
    const def = state.layout.defaults[id];
    const ov = state.layout.overrides[id] || {};
    const editable = state.layout.editable_target.includes(id);
    const f = $('coreForm');
    state.editingCore = id;
    $('coreTitle').textContent = `실시간 KPI 수정 · ${def.name}`;
    f.name.value = card.name;
    f.en.value = card.en;
    f.target.value = editable ? (ov.target ?? def.target) : '';
    f.target.disabled = !editable;
    f.name.placeholder = def.name;
    $('coreTargetHint').textContent = editable
        ? `기본 ${fmt(def.target, def.target % 1 ? 1 : 0)}${card.unit === '%' ? '%' : ` ${card.unit || 'wf'}`} · 판정(달성/주의/미달)에 바로 반영`
        : '공정별 일 계획값을 사용하므로 변경할 수 없습니다';
    $('coreErr').hidden = true;
    $('coreModal').hidden = false;
    f.name.focus();
}

async function submitCoreModal(e) {
    e.preventDefault();
    const f = e.target;
    const body = { name: f.name.value.trim(), en: f.en.value.trim() };
    if (!f.target.disabled) body.target = f.target.value === '' ? null : Number(f.target.value);
    try {
        await sendJSON(`/api/layout/core/${state.editingCore}`, 'PUT', body);
        closeModals();
        flash('실시간 KPI 내용을 저장했습니다');
        await refreshLists();
    } catch (err) {
        $('coreErr').textContent = `저장 실패: ${err.message}`;
        $('coreErr').hidden = false;
    }
}

async function resetCoreKpi() {
    const id = state.editingCore;
    await sendJSON(`/api/layout/core/${id}`, 'PUT', { reset: true });
    closeModals();
    flash('기본값으로 되돌렸습니다');
    await refreshLists();
}

// 공통 KPI 배치
function arrangement() {
    return state.custom.categories.map((c) => ({ id: c.id, kpis: c.cards.map((k) => k.id) }));
}

function saveArrangement(arr, msg = '배치를 저장했습니다') {
    // 화면에 먼저 반영
    const cards = Object.fromEntries(state.custom.categories.flatMap((c) => c.cards.map((k) => [k.id, k])));
    const cats = Object.fromEntries(state.custom.categories.map((c) => [c.id, c]));
    state.custom.categories = arr.map((a) => ({ ...cats[a.id], cards: a.kpis.map((i) => ({ ...cards[i], category: a.id })) }));
    renderCats();
    return guarded(() => sendJSON('/api/custom-layout', 'PUT', { categories: arr }), msg);
}

function moveRow(id, dir) {
    const arr = arrangement();
    const ci = arr.findIndex((c) => c.kpis.includes(id));
    const list = arr[ci].kpis, i = list.indexOf(id), j = i + dir;
    if (j >= 0 && j < list.length) {
        [list[i], list[j]] = [list[j], list[i]];
    } else {
        // 카테고리 끝에서 더 이동하면 이웃 카테고리로 넘어감
        const nci = ci + dir;
        if (nci < 0 || nci >= arr.length) return;
        list.splice(i, 1);
        if (dir > 0) arr[nci].kpis.unshift(id); else arr[nci].kpis.push(id);
    }
    saveArrangement(arr);
}

function moveCat(id, dir) {
    const arr = arrangement();
    const i = arr.findIndex((c) => c.id === id), j = i + dir;
    if (j < 0 || j >= arr.length) return;
    [arr[i], arr[j]] = [arr[j], arr[i]];
    saveArrangement(arr);
}

function toggleRowHidden(id) {
    const card = state.custom.categories.flatMap((c) => c.cards).find((k) => k.id === id);
    guarded(() => sendJSON(`/api/custom-kpis/${encodeURIComponent(id)}`, 'PUT', { hidden: !card.hidden }),
        card.hidden ? '다시 표시합니다' : '숨겼습니다 (편집 모드에서만 보임)');
}

async function editRow(id) {
    const d = await api(`/api/kpis/${encodeURIComponent(id)}?process=${state.process}`);
    openModal(d.meta);
}

// 카테고리
function openCatModal(id) {
    const f = $('catForm');
    f.reset();
    state.editingCat = id;
    const cat = id ? state.custom.categories.find((c) => c.id === id) : null;
    $('catTitle').textContent = cat ? '카테고리 수정' : '카테고리 추가';
    if (cat) { f.name.value = cat.name; f.en.value = cat.en || ''; }
    $('catErr').hidden = true;
    $('catModal').hidden = false;
    f.name.focus();
}

async function submitCatModal(e) {
    e.preventDefault();
    const f = e.target;
    const body = { name: f.name.value.trim(), en: f.en.value.trim() };
    try {
        if (state.editingCat) await sendJSON(`/api/custom-categories/${state.editingCat}`, 'PUT', body);
        else await sendJSON('/api/custom-categories', 'POST', body);
        closeModals();
        flash('카테고리를 저장했습니다');
        await refreshLists();
    } catch (err) {
        $('catErr').textContent = `저장 실패: ${err.message}`;
        $('catErr').hidden = false;
    }
}

async function deleteCat(id) {
    const cat = state.custom.categories.find((c) => c.id === id);
    if (!confirm(`'${cat.name}' 카테고리를 삭제할까요?`)) return;
    guarded(() => api(`/api/custom-categories/${id}`, { method: 'DELETE' }), '카테고리를 삭제했습니다');
}

// 드래그 앤 드롭
function clearDropMarks() {
    document.querySelectorAll('.drop-before, .drop-after, .drop-into').forEach((n) => n.classList.remove('drop-before', 'drop-after', 'drop-into'));
}

function dropSide(target, e, horizontal) {
    const r = target.getBoundingClientRect();
    return horizontal ? (e.clientX > r.left + r.width / 2 ? 'after' : 'before') : (e.clientY > r.top + r.height / 2 ? 'after' : 'before');
}

document.addEventListener('dragstart', (e) => {
    if (!state.editMode) return;
    const src = e.target.closest?.('[data-drag]');
    if (!src) return;
    const type = src.dataset.drag;
    const id = type === 'cat' ? src.closest('.cat').dataset.cat : src.dataset.kpi;
    state.drag = { type, id };
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', id);
    (type === 'cat' ? src.closest('.cat') : src).classList.add('dragging');
});

document.addEventListener('dragover', (e) => {
    const d = state.drag;
    if (!d) return;
    clearDropMarks();
    if (d.type === 'core') {
        const t = e.target.closest('.kpi[data-kpi]');
        if (!t || t.dataset.kpi === d.id) return;
        e.preventDefault();
        t.classList.add(`drop-${dropSide(t, e, true)}`);
    } else if (d.type === 'row') {
        const row = e.target.closest('.ck-row');
        if (row && row.dataset.kpi !== d.id) {
            e.preventDefault();
            row.classList.add(`drop-${dropSide(row, e, false)}`);
            return;
        }
        const cat = e.target.closest('.cat');
        if (cat && !row) { e.preventDefault(); cat.classList.add('drop-into'); }
    } else if (d.type === 'cat') {
        const cat = e.target.closest('.cat');
        if (!cat || cat.dataset.cat === d.id) return;
        e.preventDefault();
        cat.classList.add(`drop-${dropSide(cat, e, true)}`);
    }
});

document.addEventListener('drop', (e) => {
    const d = state.drag;
    if (!d) return;
    e.preventDefault();
    clearDropMarks();
    if (d.type === 'core') {
        const t = e.target.closest('.kpi[data-kpi]');
        if (t && t.dataset.kpi !== d.id) {
            const order = coreOrder().filter((i) => i !== d.id);
            let at = order.indexOf(t.dataset.kpi);
            if (dropSide(t, e, true) === 'after') at += 1;
            order.splice(at, 0, d.id);
            saveCoreOrder(order);
        }
    } else if (d.type === 'row') {
        const arr = arrangement();
        arr.forEach((c) => { c.kpis = c.kpis.filter((i) => i !== d.id); });
        const row = e.target.closest('.ck-row');
        const cat = e.target.closest('.cat');
        if (row && row.dataset.kpi !== d.id) {
            const c = arr.find((x) => x.kpis.includes(row.dataset.kpi));
            let at = c.kpis.indexOf(row.dataset.kpi);
            if (dropSide(row, e, false) === 'after') at += 1;
            c.kpis.splice(at, 0, d.id);
        } else if (cat) {
            arr.find((x) => x.id === cat.dataset.cat).kpis.push(d.id);
        } else {
            return endDrag();
        }
        const toCat = state.custom.categories.find((c) => c.id === arr.find((x) => x.kpis.includes(d.id)).id);
        saveArrangement(arr, `'${toCat.name}'에 배치했습니다`);
    } else if (d.type === 'cat') {
        const cat = e.target.closest('.cat');
        if (cat && cat.dataset.cat !== d.id) {
            const arr = arrangement();
            const moving = arr.find((c) => c.id === d.id);
            const rest = arr.filter((c) => c.id !== d.id);
            let at = rest.findIndex((c) => c.id === cat.dataset.cat);
            if (dropSide(cat, e, true) === 'after') at += 1;
            rest.splice(at, 0, moving);
            saveArrangement(rest, '카테고리 순서를 저장했습니다');
        }
    }
    endDrag();
});

function endDrag() {
    state.drag = null;
    clearDropMarks();
    document.querySelectorAll('.dragging').forEach((n) => n.classList.remove('dragging'));
}
document.addEventListener('dragend', endDrag);


// ─── Export ────────────────────────────────
function exportCSV() {
    const d = state.detail;
    if (!d) return;
    const q = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
    const lines = [];
    lines.push([q('공정'), q(d.label), q('KPI'), q(d.meta.name), q('값'), q(d.kpi.display), q('목표'), q(d.kpi.target_text), q('기준시각'), q(d.updated)].join(','));
    lines.push('');
    if (d.trend) {
        lines.push([q('구간'), q('값'), q('전일'), q('목표')].join(','));
        d.trend.labels.forEach((lb, i) => lines.push([q(lb), d.trend.today[i], d.trend.prev ? d.trend.prev[i] : '', d.trend.target ?? ''].join(',')));
        lines.push('');
    }
    if (d.kind === 'custom') {
        lines.push([q('공정'), ...d.matrix.periods.map(q)].join(','));
        d.matrix.rows.forEach((r) => lines.push([q(r.name), ...r.values.map((v) => (v == null ? '' : v))].join(',')));
    } else {
        lines.push([q('설비'), q(`${d.ranking.title} (${d.ranking.unit})`)].join(','));
        d.ranking.items.forEach((x) => lines.push([q(x.eq), x.value].join(',')));
        lines.push('');
        lines.push(d.events.columns.map(q).join(','));
        d.events.rows.forEach((r) => lines.push(r.cells.map((c) => q(c.t)).join(',')));
    }
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
    if (e.target.closest('.modal')) {
        if (e.target.closest('[data-action="close"]')) closeModals();
        else if (e.target.closest('[data-action="core-reset"]')) resetCoreKpi();
        return;
    }
    if (e.target.classList.contains('modal-bg')) { closeModals(); return; }
    const act = e.target.closest('[data-action]');
    if (act) {
        const a = act.dataset.action;
        const id = act.dataset.id;
        const dir = Number(act.dataset.dir);
        if (a === 'csv') exportCSV();
        else if (a === 'copy') copyLink(act);
        else if (a === 'theme') setTheme(currentTheme() === 'dark' ? 'light' : 'dark');
        else if (a === 'add') openModal(null, act.dataset.cat);
        else if (a === 'edit') openModal(state.detail.meta);
        else if (a === 'delete') deleteKpi();
        else if (a === 'edit-mode') setEditMode(!state.editMode);
        else if (a === 'layout-reset') resetLayout();
        else if (a === 'cat-add') openCatModal(null);
        else if (a === 'core-move') moveCore(id, dir);
        else if (a === 'core-hide') toggleCoreHidden(id);
        else if (a === 'core-edit') openCoreModal(id);
        else if (a === 'row-move') moveRow(id, dir);
        else if (a === 'row-hide') toggleRowHidden(id);
        else if (a === 'row-edit') editRow(id);
        else if (a === 'cat-move') moveCat(id, dir);
        else if (a === 'cat-edit') openCatModal(id);
        else if (a === 'cat-delete') deleteCat(id);
        return;
    }
    const tab = e.target.closest('[data-proc]');
    if (tab && !tab.matches('input')) { selectProcess(tab.dataset.proc); return; }
    const card = e.target.closest('[data-kpi]');
    if (card && !state.editMode) selectKpi(card.dataset.kpi);
});

document.addEventListener('change', (e) => {
    if (e.target.matches('.matrix input')) saveCell(e.target);
});
// 입력 중 미뤄둔 상세 갱신을 표 밖으로 나가는 즉시 반영
document.addEventListener('focusout', (e) => {
    if (!e.target.matches('.matrix input')) return;
    setTimeout(() => { if (!document.activeElement?.closest('.matrix')) renderDetail(); }, 0);
});
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeModals();
    if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('[data-kpi][role="button"]')) {
        e.preventDefault();
        selectKpi(e.target.dataset.kpi);
    }
    if (e.key === 'Enter' && e.target.matches('.matrix input')) e.target.blur();
});
$('kpiForm').addEventListener('submit', submitModal);
$('coreForm').addEventListener('submit', submitCoreModal);
$('catForm').addEventListener('submit', submitCatModal);

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
$('themeBtn').innerHTML = currentTheme() === 'dark' ? SUN : MOON;
readHash();
loadSources();
loadAll().catch((e) => console.error(e));
connectWS();
