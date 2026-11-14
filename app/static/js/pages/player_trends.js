// プレイヤーページの推移グラフタブ。
// 記録（player_logs。1日1行・日付は UTC）は全期間ぶんがフラグメントに埋め込まれて届く。期間の切り替えはここで行い、サーバーには取りに行かない。
// カード・期間の箱の見た目とグラフの共通の部品は、バトル履歴（battles.css・battle_stats.js の BattleStatsKit）と同じものを使う。
(function () {
    'use strict';

    const DAY = 86400000;
    const PERIOD_KEY = 'bi_trends_period';   // 選んでいる期間。別のプレイヤーを開いても引き継ぐ
    const GAP_DAYS = 7;                      // 記録の無い日がこれ以上続いたら、線をつなげない
    const QUICK = ['30', '60', '90', '180', '365', 'all'];
    const QUICK_URL = { 30: '30d', 60: '60d', 90: '90d', 180: '180d', 365: '1y', all: 'all' };
    // 系列の色（順番を固定して使う。ライト・ダークの両方で見分けられる組み合わせ）
    const COLORS = {
        light: { blue: '#5b7fd6', amber: '#eda100', green: '#1baf7a', violet: '#6250d6', pink: '#e87ba4', red: '#e34948', sky: '#3aaede' },
        dark: { blue: '#7797e6', amber: '#f0b429', green: '#2dbb74', violet: '#9484f2', pink: '#ee8fb3', red: '#ec6a62', sky: '#58c4ee' },
    };

    const TEXT = {
        ja: {
            period: '期間', quick: { 30: '30日', 60: '60日', 90: '90日', 180: '180日', 365: '1年', all: '全期間' },
            pick: '指定', cal: { thisMonth: '今月', lastMonth: '先月', thisYear: '今年', lastYear: '去年' }, dateTo: '〜', seasonPick: 'ガチバトルシーズン',
            shown: (n) => `<b>${n}</b>日ぶんの記録`,
            recorded: (from, to, n) => `記録のある期間: ${from}〜${to}（${n}日ぶん）。日付の区切りは日本時間の9時です。`,
            emptyRange: 'この期間の記録はありません', noData: 'この期間に表示できるデータはありません',
            seasonLines: '縦の線: ガチバトルシーズンの始まり', rankedCurrent: '現在ポイント', rankedBest: '最高ポイント',
            accessoryNote: 'ハイパーチャージとバフィーの記録は、2026年3月からです。', hours: '時間',
        },
        en: {
            period: 'Period', quick: { 30: '30d', 60: '60d', 90: '90d', 180: '180d', 365: '1y', all: 'All time' },
            pick: 'Range', cal: { thisMonth: 'This month', lastMonth: 'Last month', thisYear: 'This year', lastYear: 'Last year' }, dateTo: 'to', seasonPick: 'Ranked season',
            shown: (n) => `<b>${n}</b> ${n === 1 ? 'day' : 'days'} of records`,
            recorded: (from, to, n) => `Records: ${from} – ${to} (${n} ${n === 1 ? 'day' : 'days'}). Days are counted in UTC.`,
            emptyRange: 'No records in this period', noData: 'No data to show for this period',
            seasonLines: 'Vertical lines: Ranked season start', rankedCurrent: 'Current points', rankedBest: 'Highest points',
            accessoryNote: 'Hypercharge and Buffie records start in March 2026.', hours: 'h',
        },
    };

    // グラフの定義。slot は広告で区切られた段（0〜2）。series の k はサーバーから届く系列の名前
    // dec: 小数の桁、zero: 縦軸を0から、int: 目盛りを整数だけに、max: 縦軸の上限
    // anchor はカードの id。プロフィールタブの各項目から #trophies-trend のように飛んでくるので、変えない
    const CHARTS = [
        { key: 'trophies', anchor: 'trophies-trend', slot: 0, wide: true, title: ['総合トロフィー推移', 'Total Trophies Trend'], series: [['highest_trophies', '最多トロフィー', 'Highest Trophies', 'blue'], ['current_trophies', '現在トロフィー', 'Current Trophies', 'amber']] },
        { key: 'ranked', anchor: 'ranked-score-trend', slot: 0, wide: true, ranked: true, title: ['ガチバトルポイント推移', 'Ranked Points Trend'] },
        { key: 'avgTrophies', anchor: 'average-trophies-trend', slot: 0, dec: 2, title: ['平均トロフィー推移', 'Avg. Trophies Trend'], series: [['average_highest_trophies', '平均最多トロフィー', 'Avg. Highest Trophies', 'blue'], ['average_current_trophies', '平均現在トロフィー', 'Avg. Current Trophies', 'amber']] },
        { key: 'prestige', anchor: 'total-prestige-level-trend', slot: 0, int: true, title: ['合計トップランカーレベル推移', 'Total Prestige Level Trend'], series: [['total_prestige_level', '合計トップランカーレベル', 'Total Prestige Level', 'green']] },
        { key: 'brawlers', anchor: 'brawler-stats-trend', slot: 0, int: true, title: ['キャラクター関連数値推移', 'Brawler Stats Trend'], series: [['unlocked_brawlers', 'アンロック済み', 'Unlocked Brawlers', 'blue'], ['max_power_brawlers', 'パワー11到達数', 'Max Power Brawlers', 'pink'], ['prestige_1_brawlers', 'トップランカー1到達数', 'Prestige 1 Reached', 'violet'], ['prestige_2_brawlers', 'トップランカー2到達数', 'Prestige 2 Reached', 'red'], ['prestige_3_brawlers', 'トップランカー3到達数', 'Prestige 3 Reached', 'amber']] },
        { key: 'avgPower', anchor: 'average-power-level-trend', slot: 0, dec: 2, max: 11, title: ['平均パワーレベル推移', 'Avg. Power Level Trend'], series: [['average_power', '平均パワーレベル', 'Average Power Level', 'pink']] },
        { key: 'accessories', anchor: 'accessories-trend', slot: 1, int: true, zero: true, note: 'accessoryNote', title: ['合計アクセサリー数推移', 'Total Accessories Trend'], series: [['gadgets', '合計ガジェット数', 'Total Gadgets', 'green'], ['star_powers', '合計スターパワー数', 'Total Star Powers', 'amber'], ['gears', '合計ギア数', 'Total Gears', 'blue'], ['hyper_charges', '合計ハイパーチャージ数', 'Total Hypercharges', 'violet'], ['buffies', '合計バフィー数', 'Total Buffies', 'pink']] },
        { key: 'avgAccessories', anchor: 'average-accessories-trend', slot: 1, dec: 2, zero: true, note: 'accessoryNote', title: ['平均アクセサリー数推移', 'Avg. Accessories Trend'], series: [['average_gadgets', '平均ガジェット数', 'Avg. Gadgets', 'green'], ['average_star_powers', '平均スターパワー数', 'Avg. Star Powers', 'amber'], ['average_gears', '平均ギア数', 'Avg. Gears', 'blue'], ['average_hyper_charges', '平均ハイパーチャージ数', 'Avg. Hypercharges', 'violet'], ['average_buffies', '平均バフィー数', 'Avg. Buffies', 'pink']] },
        { key: 'playTime', anchor: 'play-time-trend', slot: 1, round: true, unit: 'hours', title: ['推定プレイ時間推移', 'Est. Play Time Trend'], series: [['play_time', '推定プレイ時間', 'Est. Play Time', 'blue']] },
        { key: 'victories', anchor: 'victories-trend', slot: 1, int: true, zero: true, title: ['勝利数推移', 'Victories Trend'], series: [['team_victories', '3対3勝利数', '3v3 Victories', 'blue'], ['solo_victories', 'ソロ勝利数', 'Solo Victories', 'green'], ['duo_victories', 'デュオ勝利数', 'Duo Victories', 'sky']] },
        { key: 'skins', anchor: 'owned-skin-count-trend', slot: 1, int: true, zero: true, title: ['所持スキン数推移', 'Owned Skins Trend'], series: [['owned_skin_count', '所持スキン数', 'Owned Skins', 'violet']] },
        { key: 'record', anchor: 'record-points-trend', slot: 1, int: true, title: ['記録ポイント推移', 'Record Points Trend'], series: [['record_points', '記録ポイント', 'Record Points', 'green']] },
        { key: 'fame', anchor: 'fame-points-trend', slot: 2, int: true, title: ['累計名声ポイント推移', 'Total Fame Points Trend'], series: [['fame_points', '累計名声ポイント', 'Total Fame Points', 'sky']] },
        { key: 'winstreak', anchor: 'max-winstreak-trend', slot: 2, int: true, title: ['最大連勝数推移', 'Max Win Streak Trend'], series: [['max_winstreak', '最大連勝数', 'Max Win Streak', 'red']] },
        { key: 'mastery', anchor: 'total-mastery-trend', slot: 2, int: true, title: ['合計旧マスタリーポイント推移', 'Total Old Mastery Points Trend'], series: [['total_mastery', '合計マスタリーポイント', 'Total Mastery Points', 'amber']] },
        { key: 'avgMastery', anchor: 'average-mastery-trend', slot: 2, round: true, title: ['平均旧マスタリーポイント推移', 'Avg. Old Mastery Points Trend'], series: [['average_mastery', '平均マスタリーポイント', 'Average Mastery Points', 'amber']] },
    ];

    const toMs = (iso) => { const [y, m, d] = iso.split('-').map(Number); return Date.UTC(y, m - 1, d); };
    const toIso = (ms) => new Date(ms).toISOString().slice(0, 10);
    const isIso = (s) => /^\d{4}-\d{2}-\d{2}$/.test(s || '') && !isNaN(toMs(s));
    const slash = (iso) => { const [y, m, d] = iso.split('-').map(Number); return `${y}/${m}/${d}`; };

    window.initPlayerTrends = function (root) {
        const Kit = window.BattleStatsKit;
        const dataEl = root && root.querySelector('#playerTrendsData');
        const filtersEl = root && root.querySelector('#playerTrendsFilters');
        if (!Kit || !dataEl || !filtersEl || root.dataset.ptrReady) return;
        root.dataset.ptrReady = '1';
        let D;
        try { D = JSON.parse(dataEl.textContent); } catch (e) { return; }
        const { esc, nf, readJson, writeJson } = Kit;
        const lang = root.dataset.lang === 'ja' ? 'ja' : 'en';
        const li = lang === 'ja' ? 0 : 1;
        const T = TEXT[lang];
        const X = D.dates.map(toMs);
        const oldest = D.dates[0], latest = D.dates[D.dates.length - 1], today = D.today >= latest ? D.today : latest;
        const totalDays = Math.round((toMs(today) - toMs(oldest)) / DAY) + 1;
        const slots = [0, 1, 2].map((n) => root.querySelector(`[data-ptr-slot="${n}"]`));
        const { rankOf, rankName } = Kit.rankTools(D.rankMin, Kit.LEAGUES[lang]);
        const hidden = {};      // グラフごとの、隠している系列（この表示の間だけ）
        let charts = {};        // key → Chart

        // ---------- 期間 ----------
        // P は {q: '30'|…|'all'} か {from, to}（YYYY-MM-DD）
        function fromUrl() {
            const q = new URLSearchParams(location.search), from = q.get('from'), to = q.get('to'), period = q.get('period');
            if (isIso(from) || isIso(to)) return normalize({ from: isIso(from) ? from : oldest, to: isIso(to) ? to : today });
            if (!period) return null;
            if (period === 'all') return { q: 'all' };
            const m = /^(\d{1,4})([dy])$/.exec(period);
            if (!m) return null;
            const days = Number(m[1]) * (m[2] === 'y' ? 365 : 1);
            if (QUICK.includes(String(days))) return { q: String(days) };
            return days > 0 ? { from: toIso(toMs(today) - (days - 1) * DAY), to: today } : null;
        }
        function normalize(p) {
            if (!p) return null;
            if (p.q) return QUICK.includes(String(p.q)) ? { q: String(p.q) } : null;
            if (!isIso(p.from) || !isIso(p.to)) return null;
            return p.from <= p.to ? { from: p.from, to: p.to } : { from: p.to, to: p.from };
        }
        const urlPeriod = fromUrl();
        let P = urlPeriod || normalize(readJson(PERIOD_KEY, null)) || { q: totalDays < 30 ? 'all' : '30' };
        function range() {
            if (P.q === 'all') return [oldest, today];
            if (P.q) return [toIso(toMs(today) - (Number(P.q) - 1) * DAY), today];
            return [P.from, P.to];
        }
        function setPeriod(next) {
            const p = normalize(next);
            if (!p) return;
            P = p;
            writeJson(PERIOD_KEY, P);
            const url = new URL(location.href);
            ['period', 'from', 'to'].forEach((k) => url.searchParams.delete(k));
            if (P.q) url.searchParams.set('period', QUICK_URL[P.q]);
            else { url.searchParams.set('from', P.from); url.searchParams.set('to', P.to); }
            try { history.replaceState(history.state, '', url); } catch (e) { /* URL を書き換えられなくても表示は変わる */ }
            render();
        }
        // 今月・先月・今年・去年（UTC の今日から数える）
        function calendarRanges() {
            const t = new Date(toMs(today)), y = t.getUTCFullYear(), m = t.getUTCMonth();
            const iso = (yy, mm, dd) => toIso(Date.UTC(yy, mm, dd));
            const all = { thisMonth: [iso(y, m, 1), today], lastMonth: [iso(y, m - 1, 1), iso(y, m, 0)], thisYear: [iso(y, 0, 1), today], lastYear: [iso(y - 1, 0, 1), iso(y - 1, 11, 31)] };
            // 記録より前に終わる区切りは出さない
            return Object.entries(all).filter(([, [, to]]) => to >= oldest);
        }

        function renderFilters(shownDays) {
            const [from, to] = range();
            const quick = QUICK.filter((q) => q === 'all' || Number(q) <= totalDays || P.q === q)
                .map((q) => `<button type="button" class="bx-chip${P.q === q ? ' is-on' : ''}" data-q="${q}">${T.quick[q]}</button>`).join('');
            const cal = calendarRanges().map(([name, [a, b]]) => `<button type="button" class="bx-chip${!P.q && P.from === a && P.to === b ? ' is-on' : ''}" data-from="${a}" data-to="${b}">${T.cal[name]}</button>`).join('');
            const season = D.seasons.find((x) => !P.q && x[2] === P.from && x[3] === P.to);
            const seasonSelect = D.seasons.length ? `<select class="bxf-select" data-season aria-label="${T.seasonPick}"><option value="">${T.seasonPick}</option>${D.seasons.map((x) => `<option value="${x[0]}"${season && season[0] === x[0] ? ' selected' : ''}>${esc(x[1])}</option>`).join('')}</select>` : '';
            const dateInput = (key, value) => `<input type="date" class="bxf-date" data-date="${key}" value="${value}" min="${oldest}" max="${today}">`;
            // 描き直しても、入力中の欄のフォーカスが外れないようにする
            const active = document.activeElement && filtersEl.contains(document.activeElement) ? document.activeElement.dataset.date : null;
            filtersEl.innerHTML = `
                <div class="bxf-row"><span class="bxf-label">${T.period}</span>${quick}<span class="bxf-count">${T.shown(nf(shownDays))}</span></div>
                <div class="bxf-row"><span class="bxf-label">${T.pick}</span>${cal}<span class="bxf-dates">${dateInput('from', from)}<span>${T.dateTo}</span>${dateInput('to', to)}</span>${seasonSelect}</div>
                <p class="bxf-note">${T.recorded(slash(oldest), slash(latest), nf(X.length))}</p>`;
            if (active) { const el = filtersEl.querySelector(`[data-date="${active}"]`); if (el) el.focus(); }
        }
        filtersEl.addEventListener('click', (e) => {
            const chip = e.target.closest('.bx-chip');
            if (!chip) return;
            if (chip.dataset.q) setPeriod({ q: chip.dataset.q });
            else if (chip.dataset.from) setPeriod({ from: chip.dataset.from, to: chip.dataset.to });
        });
        filtersEl.addEventListener('change', (e) => {
            const t = e.target;
            if (t.dataset.date) {
                const [from, to] = range();
                const value = isIso(t.value) ? t.value : (t.dataset.date === 'from' ? oldest : today);
                setTimeout(() => setPeriod(t.dataset.date === 'from' ? { from: value, to } : { from, to: value }), 0);
            } else if ('season' in t.dataset) {
                const season = D.seasons.find((x) => String(x[0]) === t.value);
                if (season) setPeriod({ from: season[2], to: season[3] });
            }
        });

        // ---------- グラフ ----------
        const palette = () => COLORS[document.documentElement.classList.contains('dark') ? 'dark' : 'light'];
        const closedSet = () => new Set(readJson(Kit.CLOSED_KEY, []));
        const fmt = (def, v) => (def.dec ? v.toLocaleString('en-US', { minimumFractionDigits: def.dec, maximumFractionDigits: def.dec }) : nf(v)) + (def.unit ? T[def.unit] : '');
        const fmtDelta = (def, v) => {
            const shown = def.dec ? Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: def.dec, maximumFractionDigits: def.dec }) : nf(Math.abs(def.round ? Math.round(v) : v));
            return (v > 0 ? '+' : v < 0 ? '−' : '±') + shown;
        };
        const deltaHtml = (def, first, last) => {
            const v = def.round ? Math.round(last) - Math.round(first) : last - first;
            return `<em class="ptr-delta${v > 0 ? ' is-up' : v < 0 ? ' is-down' : ''}">${fmtDelta(def, v)}</em>`;
        };
        const dateTitle = (x) => slash(toIso(x));
        // 横軸の目盛り。日 → 週 → 月の初め、と幅に合わせて粗くする
        function xTicks(axis, x0, x1) {
            const room = Math.max(2, Math.floor(axis.width / 62)), days = Math.round((x1 - x0) / DAY);
            const out = [];
            const step = [1, 2, 7, 14].find((s) => days / s <= room);
            if (step) { for (let x = x1; x >= x0; x -= step * DAY) out.unshift(x); } else {
                const months = [];
                const d = new Date(x0);
                for (let t = Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1); t <= x1; t = Date.UTC(new Date(t).getUTCFullYear(), new Date(t).getUTCMonth() + 1, 1)) months.push(t);
                const every = Math.ceil(months.length / room);
                months.forEach((t, i) => { if ((months.length - 1 - i) % every === 0) out.push(t); });
            }
            axis.ticks = out.map((value) => ({ value }));
        }
        const xLabel = (v, index, list) => {
            const d = new Date(v), prev = index > 0 ? new Date(list[index - 1].value) : null;
            return (!prev || prev.getUTCFullYear() !== d.getUTCFullYear() ? `${d.getUTCFullYear()}/` : '') + `${d.getUTCMonth() + 1}/${d.getUTCDate()}`;
        };
        function card(def, cap, body) {
            const id = 'tr-' + def.key, closed = closedSet().has(id);
            return `<section class="bx-card${def.wide ? ' bx-span2' : ''}${closed ? ' is-closed' : ''}" id="${def.anchor}" data-card="${id}" data-key="${def.key}"><div class="bx-card__head">
                <button type="button" class="bx-card__toggle" data-act="fold" aria-expanded="${!closed}"><h3 class="bx-card__title">${def.title[li]}</h3><span class="bx-chev"></span></button>
                ${cap ? `<span class="bx-card__cap">${cap}</span>` : ''}</div><div class="bx-card__fold"><div>${body}</div></div></section>`;
        }
        const keyChip = (name, color, label, extra, off, dashed) => `<button type="button" class="ptr-key${off ? ' is-off' : ''}" data-series="${name}" aria-pressed="${!off}"><i class="${dashed ? 'is-dashed' : ''}" style="color:${color}"></i>${label}${extra}</button>`;

        // 1つのグラフのカード。期間の中にデータが無ければ null（一度も記録の無い系列だけのグラフは、そもそも出さない）
        function buildCard(def, i0, i1, after) {
            const th = Kit.themeOf(root), colors = palette();
            const xs = X.slice(i0, i1 + 1), x0 = xs[0], x1 = xs[xs.length - 1];
            // 前後の記録が離れていて、線にならない点（単独の点）は、点として描く
            const lonely = xs.map((x, i) => (i === 0 || x - xs[i - 1] >= GAP_DAYS * DAY) && (i === xs.length - 1 || xs[i + 1] - x >= GAP_DAYS * DAY));
            const pointRadius = (ctx) => (lonely[ctx.dataIndex] ? 2.5 : 0);
            const off = hidden[def.key] || (hidden[def.key] = new Set());

            if (def.ranked) {
                const cur = D.series.ranked_current_score, best = D.series.ranked_highest_score;
                const pts = [], bestPts = [];
                for (let i = i0; i <= i1; i++) {
                    if (cur[i] != null) pts.push({ x: X[i], y: cur[i] });
                    if (best[i] != null) bestPts.push({ x: X[i], y: best[i] });
                }
                if (pts.length < 2) return pts.length || cur.some((v) => v != null) ? card(def, '', `<div class="ptr-nodata">${T.noData}</div>`) : null;
                const showBest = bestPts.length >= 2 && !off.has('best');
                const rx0 = pts[0].x, rx1 = pts[pts.length - 1].x;
                const seasons = D.seasons.map((x) => toMs(x[2])).filter((x) => x > rx0 && x < rx1).sort((a, b) => a - b);
                after.push(() => {
                    const cfg = Kit.rankedTrendConfig({
                        th, pts, byRank: false, rankMin: D.rankMin, leagues: Kit.LEAGUES[lang], seasons, utc: true,
                        tipTitle: dateTitle,
                        tipLabel: (y, ds) => `${ds.label}: ${nf(y)}（${rankName(Math.max(1, rankOf(y)))}）`,
                        extra: showBest ? [{ label: T.rankedBest, data: bestPts.filter((p) => p.x >= rx0 && p.x <= rx1), borderColor: th.mut, borderWidth: 1.5, borderDash: [5, 4], pointRadius: 0, pointHoverRadius: 3, tension: 0 }] : [],
                    });
                    cfg.data.datasets[0].label = T.rankedCurrent;
                    mount(def.key, cfg);
                });
                const first = pts[0].y, last = pts[pts.length - 1].y;
                const legend = bestPts.length >= 2 ? `<div class="ptr-legend">${keyChip('current', th.ink, T.rankedCurrent, '', false)}${keyChip('best', th.mut, T.rankedBest, ` <b>${nf(bestPts[bestPts.length - 1].y)}</b>`, !showBest, true)}</div>` : '';
                return card(def, `${nf(first)} → <b>${nf(last)}</b> ${deltaHtml(def, first, last)}`, `${legend}<div class="bx-chart bx-chart--tall"><canvas data-ptr-chart="${def.key}"></canvas></div>${seasons.length ? `<div class="bx-heatlegend">${T.seasonLines}</div>` : ''}`);
            }

            // 記録が一度も無い系列（古いプレイヤーにしか無いものなど）は出さない
            const series = def.series.filter(([k]) => D.series[k] && D.series[k].some((v) => v != null));
            if (!series.length) return null;
            const stats = series.map(([k]) => {
                const col = D.series[k];
                let first = null, last = null;
                for (let i = i0; i <= i1; i++) if (col[i] != null) { if (first == null) first = col[i]; last = col[i]; }
                return { first, last };
            });
            if (stats.every((s) => s.last == null)) return card(def, '', `<div class="ptr-nodata">${T.noData}</div>`);
            const multi = series.length > 1;
            after.push(() => {
                const o = Kit.baseOptions(th);
                o.scales.x.type = 'linear';
                o.scales.x.min = x0 === x1 ? x0 - DAY : x0; o.scales.x.max = x0 === x1 ? x1 + DAY : x1;
                o.scales.x.afterBuildTicks = (axis) => xTicks(axis, x0, x1);
                o.scales.x.ticks.autoSkip = false; o.scales.x.ticks.maxTicksLimit = 40; o.scales.x.ticks.callback = xLabel;
                // 縦の範囲。値が動いていないときも、線が枠に張り付かないように上下を空ける
                const values = [];
                series.forEach(([k]) => { for (let i = i0; i <= i1; i++) if (D.series[k][i] != null) values.push(D.series[k][i]); });
                const lo = Math.min(...values), hi = Math.max(...values);
                const pad = hi > lo ? (hi - lo) * 0.06 : Math.max(def.dec ? 0.5 : 1, Math.abs(hi) * 0.05);
                o.scales.y.suggestedMin = def.zero ? 0 : Math.max(0, lo - pad);
                if (def.max != null && hi + pad >= def.max) o.scales.y.max = def.max; else o.scales.y.suggestedMax = hi + pad;
                o.scales.y.ticks.callback = (v) => (def.dec ? v.toLocaleString('en-US', { maximumFractionDigits: 2 }) : nf(v));
                if (!def.dec) o.scales.y.ticks.precision = 0;
                o.plugins.tooltip.displayColors = multi; o.plugins.tooltip.boxWidth = 8; o.plugins.tooltip.boxHeight = 8; o.plugins.tooltip.boxPadding = 4;
                o.plugins.tooltip.filter = (it) => it.parsed.y != null;
                o.plugins.tooltip.callbacks = {
                    title: (it) => (it.length ? dateTitle(it[0].parsed.x) : ''),
                    label: (it) => `${multi ? it.dataset.label + ': ' : ''}${fmt(def, def.round ? Math.round(it.parsed.y) : it.parsed.y)}`,
                };
                const datasets = series.map(([k, ja, en, color]) => ({
                    label: li ? en : ja, data: xs.map((x, i) => ({ x, y: D.series[k][i0 + i] })), hidden: off.has(k),
                    borderColor: colors[color], backgroundColor: colors[color], pointBackgroundColor: colors[color], pointBorderWidth: 0,
                    borderWidth: 2, pointRadius, pointHoverRadius: 4, tension: 0, spanGaps: GAP_DAYS * DAY - 1,
                }));
                mount(def.key, { type: 'line', data: { datasets }, options: o });
            });
            let cap = '', legend = '';
            if (multi) {
                legend = `<div class="ptr-legend">${series.map(([k, ja, en, color], n) => keyChip(k, colors[color], li ? en : ja, stats[n].last == null ? '' : ` <b>${fmt(def, def.round ? Math.round(stats[n].last) : stats[n].last)}</b>${deltaHtml(def, stats[n].first, stats[n].last)}`, off.has(k))).join('')}</div>`;
            } else {
                const { first, last } = stats[0], show = (v) => fmt(def, def.round ? Math.round(v) : v);
                cap = `${show(first)} → <b>${show(last)}</b> ${deltaHtml(def, first, last)}`;
            }
            return card(def, cap, `${legend}<div class="bx-chart"><canvas data-ptr-chart="${def.key}"></canvas></div>${def.note ? `<p class="ptr-note">${T[def.note]}</p>` : ''}`);
        }
        function mount(key, config) {
            const el = root.querySelector(`canvas[data-ptr-chart="${key}"]`);
            if (!el || !window.Chart) return;
            Chart.defaults.font.family = Kit.CHART_FONT;
            if (charts[key]) charts[key].destroy();
            charts[key] = new Chart(el, config);
        }

        function render() {
            const [from, to] = range(), a = toMs(from), b = toMs(to);
            let i0 = X.findIndex((x) => x >= a), i1 = -1;
            for (let i = X.length - 1; i >= 0; i--) if (X[i] <= b) { i1 = i; break; }
            const has = i0 !== -1 && i1 >= i0;
            renderFilters(has ? i1 - i0 + 1 : 0);
            Object.values(charts).forEach((chart) => chart.destroy());
            charts = {};
            if (!has) {
                slots.forEach((slot, n) => { slot.innerHTML = n ? '' : `<section class="bx-card bx-span2"><div class="ptr-nodata">${T.emptyRange}</div></section>`; });
                return;
            }
            const after = [];
            slots.forEach((slot, n) => {
                slot.innerHTML = CHARTS.filter((def) => def.slot === n).map((def) => buildCard(def, i0, i1, after) || '').join('');
                // 半分の幅のカードが奇数個だと右が空くので、最後の1つを横いっぱいにする
                const halves = slot.querySelectorAll('.bx-card:not(.bx-span2)');
                if (halves.length % 2) halves[halves.length - 1].classList.add('bx-span2');
            });
            after.forEach((fn) => fn());
        }

        root.addEventListener('click', (e) => {
            const fold = e.target.closest('[data-act="fold"]');
            if (fold) {
                const el = fold.closest('.bx-card'), closed = closedSet();
                const isClosed = el.classList.toggle('is-closed');
                fold.setAttribute('aria-expanded', String(!isClosed));
                if (isClosed) closed.add(el.dataset.card); else closed.delete(el.dataset.card);
                writeJson(Kit.CLOSED_KEY, [...closed]);
                return;
            }
            const key = e.target.closest('.ptr-key');
            if (!key) return;
            const cardEl = key.closest('.bx-card'), chartKey = cardEl.dataset.key, name = key.dataset.series;
            const off = hidden[chartKey] || (hidden[chartKey] = new Set());
            if (chartKey === 'ranked') {
                // 最高ポイントの線は縦の範囲を変えるので、描き直す（現在ポイントは消せない）
                if (name !== 'best') return;
                if (off.has(name)) off.delete(name); else off.add(name);
                render();
                return;
            }
            if (off.has(name)) off.delete(name); else off.add(name);
            key.classList.toggle('is-off', off.has(name));
            key.setAttribute('aria-pressed', String(!off.has(name)));
            const chart = charts[chartKey];
            if (chart) {
                const def = CHARTS.find((c) => c.key === chartKey);
                chart.data.datasets.forEach((ds) => { const s = def.series.find((x) => x[li ? 2 : 1] === ds.label); if (s) ds.hidden = off.has(s[0]); });
                chart.update();
            }
        });

        render();
        // プロフィールタブなどから #◯◯-trend 付きで開かれたときは、そのカードまで動かす（閉じていたら開く）
        const target = location.hash.length > 1 ? document.getElementById(location.hash.slice(1)) : null;
        if (target && root.contains(target)) {
            target.classList.remove('is-closed');
            requestAnimationFrame(() => target.scrollIntoView({ block: 'start' }));
        }
    };
})();
