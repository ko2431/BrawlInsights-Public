// バトル履歴ページ: 「バトル」より上のグラフのカード（サマリー・日ごとの勝敗・プレイ時間帯・キャラ別・モード別など）
// フラグメントはサブタブの切り替えのたびに差し替わるので、差し込まれるたびに window.initBattleStats(root) を呼ぶ
(function () {
    'use strict';

    // 集計1つぶんの並び（app/services/battle_history.py の AGG_FIELDS と同じ順）
    const AGG = ['n', 'w', 'd', 'l', 'sp', 'sn', 'k', 'dth', 'dd', 'he', 'dt', 'dn', 'du', 'tc', 'g'];
    const CLOSED_KEY = 'bi_battles_blocks_closed';   // 折りたたんでいるカードの種類
    const PREF_KEY = 'bi_battles_block_views';       // 各カードで選んでいる表示（円グラフ・13週など）
    const CHART_FONT = "-apple-system, BlinkMacSystemFont, 'Hiragino Kaku Gothic ProN', 'Segoe UI', 'Yu Gothic UI', 'Meiryo', Roboto, 'Noto Sans JP', sans-serif";
    const FMT_COLORS = { '3v3': '#2a78d6', '5v5': '#9b5de5', solo: '#91e136', duo: '#4cc9f0', trio: '#5fd8b0', '2v2': '#f59e0b', '1v1': '#e5484d', '4v4': '#8d6e63', other: '#9a9a9a' };
    const FMT_DARK = new Set(['solo', 'duo', 'trio', '2v2']);  // 明るい色の上は濃い文字にする
    const RARITY = { 1: '#4dc2c8', 2: '#58cc54', 3: '#6196e0', 4: '#c75ceb', 5: '#e15555', 6: '#d2c448', 7: '#dfad66' };  // スキンのレア度の色（player.css の --sr-accent）と同じ
    const PALETTE = ['#5b7fd6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#6250d6', '#e34948', '#4cc9f0', '#8bc34a', '#b07cd8', '#d98e5f', '#5fd8b0'];
    const SL = '<i class="bx-sl">/</i>';
    // ガチバトルのランクの帯（3ランクずつ。最後はプロ）の色
    const LEAGUE_COLORS = ['#c8824a', '#9aa9bd', '#e3b01f', '#3fb6ea', '#a45cf0', '#e5484d', '#f08a24', '#2bb673'];
    const ROMAN = ['Ⅰ', 'Ⅱ', 'Ⅲ'];

    const TEXT = {
        ja: {
            dow: ['月', '火', '水', '木', '金', '土', '日'], dowLong: (d) => d + '曜',
            summary: 'サマリー', lastBattles: (n, unit) => `過去<b>${n}</b>${unit}のバトル`, unitBattle: '件', unitSet: 'セット',
            winRate: '勝率', w: '勝', d: '分', l: '敗', wdl: (w, d, l) => `${w}勝 ${d ? d + '分 ' : ''}${l}敗`,
            maxStreak: '最大連勝', loseStreak: '連敗', streakW: '連勝', streakL: '連敗', nowW: (n) => `現在${n}連勝中`, nowL: (n) => `現在${n}連敗中`,
            trophyChange: 'トロフィー増減', perBattle: '1戦あたり', mvpRate: '★ MVP率', times: '回',
            avgKD: `平均キル${SL}デス`, kills: 'キル', deaths: 'デス', killsUnit: 'キル', deathsUnit: 'デス', kdRatio: 'キルデス比', avgDamage: '平均与ダメージ', damageTaken: '被ダメージ',
            avgTime: '平均バトル時間', totalTime: '合計', hoursUnit: '時間', minutesUnit: '分',
            noteHead: 'キル / デス・与ダメージ・バトル時間は、', noteOld: '2026年11月アップデート以降の', noteMode: 'バトルロイヤルなど一部のモードを除く', noteTail: (n) => `バトル（${n}試合）が対象です。`, noteJoin: '、',
            daily: '日ごとの勝敗', weekly: '週ごとの勝敗', d14: '14日', d30: '30日', w13: '13週', keyWin: '勝利（上）', keyLose: '敗北（下）',
            hours: 'プレイ時間帯', tz: '時刻: ', tzJapan: '日本時間', tzDevice: 'この端末の時刻', hourMark: '時', battleCount: 'バトル数',
            heatCell: (dow, h, n, rate) => `${dow}曜 ${h}時台\n${n}戦・勝率 ${rate}`, few: '少', many: '多', rateHigh: '勝率 高', rateLow: '低（3戦未満は色なし）',
            mostActive: '最もプレイした時間帯は', mostActiveAt: (dow, h, n) => `<b>${dow}曜 ${h}時台</b>（${n}戦）`, bestRate: '勝率が高いのは', bestRateAt: (from, to, rate) => `<b>${from}〜${to}時</b>（${rate}%）`,
            brawlers: 'キャラ別の成績', brawlerCount: (n) => `${n}キャラ`, brawler: 'キャラ', uses: '使用回数', damageShort: '与ダメ', showAllBrawlers: (n) => `すべて表示（全${n}キャラ）`, showTop: '上位だけ表示',
            list: '一覧', pie: '円グラフ', other: 'その他', pieTip: (n, p) => `${n}回（${p}%）`,
            modes: 'モード別の成績', modesCap: 'モードを押してマップ別で閲覧', mode: 'モード', plays: 'プレイ回数', showAllModes: (n) => `すべて表示（全${n}モード）`,
            fmt: { solo: 'ソロ', duo: 'デュオ', trio: 'トリオ', other: 'その他' }, fmtVs: (n) => `${n}対${n}`, fmtTip: (name, n, unit, p) => `${name}: ${n}${unit}（${p}%）`,
            mates: '一緒にプレイしたプレイヤー', matesCap: '同じチームになった回数', matesCapFriendly: '味方・敵の合計', player: 'プレイヤー', count: '回数', rateWith: '一緒のときの勝率', showMoreMates: (n) => `もっと見る（${n}人まで）`,
            trophySum: 'トロフィー増減の推移', trophySumCap: (v) => `期間の合計 ${v}`, trophySumTip: (v) => `期間の初めから ${v}`, trophyAbs: 'トロフィーの推移', trophyUnit: 'トロフィー',
            trophyBy: 'キャラ別のトロフィー増減', trophyByCap: '増えた5キャラ・減った5キャラ', trophyByTop: '変動の大きい10キャラ', trophyByTip: (g, v) => `${g}戦で ${v}`,
            empty: 'バトルがありません',
            leagues: ['ブロンズ', 'シルバー', 'ゴールド', 'ダイヤモンド', 'エリート', 'レジェンド', 'マスター', 'プロ'],
            currentPoints: '現在のポイント', rankedScore: 'ポイントの推移', rankedRank: 'ランクの推移', pointsTip: (v, rank) => `${v} ポイント（${rank}）`,
            modesMaps: 'モード・マップ別の成績', picksCap: 'マップの右はよく使ったキャラ', graph: 'グラフ', byMode: 'モード別', byMap: 'マップ別',
            modeRates: 'モードごとの勝率', mapRates: 'マップごとの勝率（モード別）', radarTip: (rate, n) => `勝率 ${rate}%（${n}回）`, pickTip: (name, n) => `${name}: ${n}回`,
            radarNeedModes: 'レーダーを出すには、3つ以上のモードのバトルが必要です', radarNeedMaps: 'レーダーを出すには、同じモードで3つ以上のマップのバトルが必要です',
            bans: 'BAN', bansCap: (n) => `BANのある${n}セット`, banMine: '自分がBANしたキャラ', banOpp: '敵チームにBANされたキャラ', banTeam: '味方がBANしたキャラ', banTip: (name, n, p) => `${name}\n${n}回（${p}%）`,
        },
        en: {
            dow: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'], dowLong: (d) => d,
            summary: 'Summary', lastBattles: (n, unit) => `Last <b>${n}</b> ${unit}`, unitBattle: 'battles', unitSet: 'sets',
            winRate: 'Win rate', w: 'W', d: 'D', l: 'L', wdl: (w, d, l) => `${w}W ${d ? d + 'D ' : ''}${l}L`,
            maxStreak: 'Best win', loseStreak: 'loss streak', streakW: 'W', streakL: 'L', nowW: (n) => `On a ${n}-win streak`, nowL: (n) => `On a ${n}-loss streak`,
            trophyChange: 'Trophy change', perBattle: 'Per battle', mvpRate: '★ MVP rate', times: '',
            avgKD: `Avg. kills${SL}deaths`, kills: 'K', deaths: 'D', killsUnit: '', deathsUnit: '', kdRatio: 'K/D ratio', avgDamage: 'Avg. damage dealt', damageTaken: 'Taken',
            avgTime: 'Avg. battle time', totalTime: 'Total', hoursUnit: 'h', minutesUnit: 'm',
            noteHead: 'Kills / deaths, damage and battle time are based on ', noteOld: 'battles since the November 2026 update', noteMode: 'battles in modes that have stats (Showdown and a few other modes are not included)', noteBoth: 'battles since the November 2026 update in modes that have stats (Showdown and a few other modes are not included)', noteTail: (n) => `: ${n} battles.`, noteJoin: '',
            daily: 'Daily results', weekly: 'Weekly results', d14: '14d', d30: '30d', w13: '13w', keyWin: 'Wins (up)', keyLose: 'Losses (down)',
            hours: 'Play times', tz: 'Time zone: ', tzJapan: 'Japan Standard Time', tzDevice: 'this device', hourMark: 'h', battleCount: 'Battles',
            heatCell: (dow, h, n, rate) => `${dow} ${h}:00\n${n} battles, win rate ${rate}`, few: 'Fewer', many: 'More', rateHigh: 'Win rate: high', rateLow: 'low (no color under 3 battles)',
            mostActive: 'Most active:', mostActiveAt: (dow, h, n) => `<b>${dow} ${h}:00</b> (${n} battles)`, bestRate: 'Best win rate:', bestRateAt: (from, to, rate) => `<b>${from}:00–${to}:00</b> (${rate}%)`,
            brawlers: 'Brawlers', brawlerCount: (n) => `${n} brawlers`, brawler: 'Brawler', uses: 'Picks', damageShort: 'Damage', showAllBrawlers: (n) => `Show all ${n} brawlers`, showTop: 'Show top only',
            list: 'List', pie: 'Pie chart', other: 'Other', pieTip: (n, p) => `${n} (${p}%)`,
            modes: 'Game modes', modesCap: 'Tap a mode to see its maps', mode: 'Mode', plays: 'Battles', showAllModes: (n) => `Show all ${n} modes`,
            fmt: { solo: 'Solo', duo: 'Duo', trio: 'Trio', other: 'Other' }, fmtVs: (n) => `${n}v${n}`, fmtTip: (name, n, unit, p) => `${name}: ${n} (${p}%)`,
            mates: 'Played with', matesCap: 'Times on the same team', matesCapFriendly: 'Teammates and opponents', player: 'Player', count: 'Battles', rateWith: 'Win rate together', showMoreMates: (n) => `Show more (up to ${n})`,
            trophySum: 'Trophy change over time', trophySumCap: (v) => `Total ${v}`, trophySumTip: (v) => `${v} since the start`, trophyAbs: 'Trophies over time', trophyUnit: 'trophies',
            trophyBy: 'Trophy change by brawler', trophyByCap: 'Top 5 gains and losses', trophyByTop: '10 biggest changes', trophyByTip: (g, v) => `${v} in ${g} battles`,
            empty: 'No battles',
            leagues: ['Bronze', 'Silver', 'Gold', 'Diamond', 'Mythic', 'Legendary', 'Masters', 'Pro'],
            currentPoints: 'Current points', rankedScore: 'Points over time', rankedRank: 'Rank over time', pointsTip: (v, rank) => `${v} points (${rank})`,
            modesMaps: 'Modes and maps', picksCap: 'Most-picked brawlers are shown beside each map', graph: 'Chart', byMode: 'By mode', byMap: 'By map',
            modeRates: 'Win rate by mode', mapRates: 'Win rate by map, per mode', radarTip: (rate, n) => `Win rate ${rate}% (${n})`, pickTip: (name, n) => `${name}: ${n}`,
            radarNeedModes: 'The radar chart needs battles in at least 3 modes', radarNeedMaps: 'The radar chart needs battles on at least 3 maps of one mode',
            bans: 'Bans', bansCap: (n) => `${n} sets with bans`, banMine: 'Banned by you', banOpp: 'Banned by opponents', banTeam: 'Banned by teammates', banTip: (name, n, p) => `${name}\n${n} (${p}%)`,
        },
    };

    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const nf = (n) => Math.round(n).toLocaleString('en-US');
    const f1 = (x) => (x == null || isNaN(x) ? '—' : x.toFixed(1));
    const f2 = (x) => (x == null || isNaN(x) ? '—' : x.toFixed(2));
    const pc = (x) => (x == null || isNaN(x) ? '—' : x.toFixed(1) + '%');
    const signed = (n) => (n > 0 ? '+' : '') + nf(n);
    const dur = (s) => (s == null || isNaN(s) ? '—' : Math.floor(s / 60) + ':' + String(Math.round(s % 60)).padStart(2, '0'));
    const toAgg = (arr, offset) => { const a = {}; for (let i = 0; i < AGG.length; i++) a[AGG[i]] = arr[offset + i] || 0; return a; };
    const wr = (a) => (a.w + a.l ? a.w / (a.w + a.l) * 100 : null);
    const lighten = (hex) => '#' + [1, 3, 5].map((k) => Math.round(parseInt(hex.slice(k, k + 2), 16) * 0.55 + 255 * 0.45).toString(16).padStart(2, '0')).join('');

    function readJson(key, fallback) { try { const v = JSON.parse(localStorage.getItem(key) || 'null'); return v == null ? fallback : v; } catch (e) { return fallback; } }
    function writeJson(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* 記憶できなくても操作はできる */ } }

    // ---------- 吹き出し（ページに1つ） ----------
    let tipEl = null;
    function showTip(target, x, y) {
        if (!tipEl) { tipEl = document.createElement('div'); tipEl.className = 'bx-tip'; document.body.appendChild(tipEl); }
        tipEl.textContent = target.dataset.tip;
        tipEl.classList.add('is-on');
        const w = tipEl.offsetWidth, h = tipEl.offsetHeight;
        tipEl.style.left = Math.max(6, Math.min(x - w / 2, document.documentElement.clientWidth - w - 6)) + 'px';
        tipEl.style.top = (y - h - 12 < 6 ? y + 16 : y - h - 12) + 'px';
    }
    const hideTip = () => { if (tipEl) tipEl.classList.remove('is-on'); };
    const tipTarget = (e) => (e.target.closest ? e.target.closest('.bx [data-tip]') : null);
    document.addEventListener('pointermove', (e) => { const t = tipTarget(e); if (t) showTip(t, e.clientX, e.clientY); else hideTip(); });
    document.addEventListener('focusin', (e) => { const t = tipTarget(e); if (t) { const r = t.getBoundingClientRect(); showTip(t, r.left + r.width / 2, r.top); } });
    document.addEventListener('focusout', hideTip);
    window.addEventListener('scroll', hideTip, { passive: true });

    let current = null;  // いま表示しているカード（画面幅が変わったときの詰め直し用）
    window.addEventListener('resize', () => { if (current && document.body.contains(current.root)) current.fitWbars(); });

    window.initBattleStats = function (root) {
        const dataEl = root && root.querySelector('#battleStatsData');
        if (!dataEl || root.dataset.bxReady) return;
        root.dataset.bxReady = '1';
        let D;
        try { D = JSON.parse(dataEl.textContent); } catch (e) { return; }
        const lang = root.dataset.lang === 'ja' ? 'ja' : 'en';
        const T = TEXT[lang];
        const IMG = root.dataset.imgBase;
        const STATIC = IMG.replace(/\/images$/, '');
        const total = toAgg(D.total, 0);
        const unitWord = D.tab === 'powerleague' ? T.unitSet : T.unitBattle;
        const names = D.names;
        const charts = [];
        let after = [];

        // 選んでいる表示。別のプレイヤーを開いても引き継ぐ
        const S = Object.assign({ dailyRange: 30, heatMode: 'n', bsort: 'n', bview: 'list', msort: 'n', mview: 'list', matesort: 'n', mateview: 'list', mgraph: 'mode', banview: 'list' }, readJson(PREF_KEY, {}));
        const V = { bmore: false, mmore: false, matemore: false, mopen: new Set() };  // この表示の間だけの状態
        const savePrefs = () => writeJson(PREF_KEY, Object.assign(readJson(PREF_KEY, {}), S));

        const bname = (id) => (names.brawlers[id] ? names.brawlers[id][0] : String(id));
        const rcolor = (id) => RARITY[(names.brawlers[id] || [])[1]] || '#9a9a9a';
        const bimg = (id) => `${IMG}/brawler_borderless_portraits/${id}.png`;
        const mname = (id) => (id && names.modes[id] ? names.modes[id][0] : T.other);
        const mcolor = (id) => (id && names.modes[id] && names.modes[id][1]) || '#9a9a9a';
        const micon = (id) => (id && names.modes[id] && names.modes[id][2] ? STATIC + names.modes[id][2] : null);
        const mapname = (id) => names.maps[id] || String(id);
        const fmtName = (key) => (T.fmt[key] ? T.fmt[key] : T.fmtVs(key.charAt(0)));
        function short(n) {
            if (n == null || isNaN(n)) return '—';
            const a = Math.abs(n);
            if (a < 10000) return nf(n);
            if (lang === 'ja') { const m = n / 10000; return (a < 100000 ? m.toFixed(2) : a < 1000000 ? m.toFixed(1) : String(Math.round(m))) + '<small>万</small>'; }
            return a < 1000000 ? (n / 1000).toFixed(a < 100000 ? 1 : 0) + '<small>k</small>' : (n / 1000000).toFixed(2) + '<small>M</small>';
        }
        const shortText = (n) => short(n).replace(/<[^>]+>/g, '');
        // ガチバトルのランク。境目のポイントはサーバーから届く（Battle.ranked_rank_after と同じ）
        const RANK_MIN = (D.ranked && D.ranked.rankMin) || [];
        const rankOf = (score) => { let r = 0; for (let i = 1; i < RANK_MIN.length; i++) if (score >= RANK_MIN[i]) r = i; return r; };
        const leagueIndex = (rank) => Math.min(7, Math.floor((Math.max(1, rank) - 1) / 3));
        const rankName = (rank) => (rank >= 22 ? T.leagues[7] : `${T.leagues[leagueIndex(rank)]} ${ROMAN[(rank - 1) % 3]}`);
        const ymd = (t) => { const d = new Date(t * 1000); return `${d.getMonth() + 1}/${d.getDate()}`; };
        const ymdFull = (t) => { const d = new Date(t * 1000); return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()} ${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`; };

        // ---------- 共通の部品 ----------
        const tipAttr = (text) => ` data-tip="${esc(text)}" tabindex="0"`;
        const times = (n) => `${nf(n)}${T.times ? `<small>${T.times}</small>` : ''}`;
        function wrCell(a) {
            const v = wr(a);
            return `<span class="bx-wr"${tipAttr(T.wdl(a.w, a.d, a.l))}><span class="bx-split${v == null ? ' is-none' : ''}"><i style="width:${v == null ? 0 : v}%"></i></span><b>${pc(v)}</b></span>`;
        }
        const seg = (name, value, opts) => `<span class="bx-seg" data-seg="${name}">${opts.map(([v, l]) => `<button type="button" data-v="${v}"${String(v) === String(value) ? ' class="is-on"' : ''}>${l}</button>`).join('')}</span>`;
        const viewSeg = (name, value) => seg(name, value, [['list', T.list], ['pie', T.pie]]);
        const closedSet = () => new Set(readJson(CLOSED_KEY, []));
        // カード。見出しで折りたためる。開閉はカードの種類ごとに端末に記憶する
        function card(key, cls, title, cap, tools, body) {
            const closed = closedSet().has(key);
            return `<section class="bx-card ${cls}${closed ? ' is-closed' : ''}" data-card="${key}"><div class="bx-card__head">
                <button type="button" class="bx-card__toggle" data-act="fold" aria-expanded="${!closed}"><h3 class="bx-card__title">${title}</h3><span class="bx-chev"></span></button>
                ${cap ? `<span class="bx-card__cap">${cap}</span>` : ''}${tools ? `<div class="bx-card__tools">${tools}</div>` : ''}</div><div class="bx-card__fold"><div>${body}</div></div></section>`;
        }
        function chartTheme() {
            const cs = getComputedStyle(root);
            const v = (n) => cs.getPropertyValue(n).trim();
            return { card: v('--bx-card') || '#fff', mut: v('--color-777') || '#777', grid: v('--color-eee') || '#eee', win: v('--bx-win'), lose: v('--bx-lose'), accent: v('--bx-accent'), ink: v('--color-111') };
        }
        function baseOptions(th) {
            return {
                responsive: true, maintainAspectRatio: false, animation: { duration: 250 }, interaction: { mode: 'index', intersect: false },
                plugins: { legend: { display: false }, tooltip: { backgroundColor: 'rgba(28,28,28,.94)', titleColor: '#fff', bodyColor: '#fff', padding: 8, cornerRadius: 9, titleFont: { size: 12 }, bodyFont: { size: 12 }, displayColors: false } },
                scales: {
                    x: { grid: { display: false }, border: { color: th.grid }, ticks: { color: th.mut, font: { size: 10.5 }, maxRotation: 0, autoSkip: true, maxTicksLimit: 8 } },
                    y: { grid: { color: th.grid }, border: { display: false }, ticks: { color: th.mut, font: { size: 10.5 }, maxTicksLimit: 6 } },
                },
            };
        }
        function mountChart(id, build) {
            after.push(() => {
                const el = root.querySelector('#' + id);
                if (!el || !window.Chart) return;
                Chart.defaults.font.family = CHART_FONT;
                charts.push(new Chart(el, build(chartTheme())));
            });
        }
        // 円グラフ（一覧との切り替え用）。0.5% 未満は「その他」にまとめる。同じ色が続くときは薄い色と交互にする
        function pieBody(id, items, small) {
            const tot = items.reduce((s, x) => s + x.n, 0) || 1;
            const main = items.slice().sort((a, b) => b.n - a.n).filter((x) => x.n / tot >= 0.005).map((x, i) => ({ ...x, base: x.color || PALETTE[i % PALETTE.length] }));
            main.forEach((x, i) => { const prev = main[i - 1]; x.color = prev && prev.base === x.base && prev.color === prev.base ? lighten(x.base) : x.base; });
            const rest = tot - main.reduce((s, x) => s + x.n, 0);
            if (rest > 0) main.push({ label: T.other, n: rest, color: '#b9b9b9' });
            mountChart(id, (th) => ({
                type: 'pie', data: { labels: main.map((x) => x.label), datasets: [{ data: main.map((x) => x.n), backgroundColor: main.map((x) => x.color), borderColor: th.card, borderWidth: 1.5, hoverOffset: 6 }] },
                options: { responsive: true, maintainAspectRatio: false, animation: { duration: 250 }, layout: { padding: 6 }, plugins: { legend: { display: false }, tooltip: { ...baseOptions(th).plugins.tooltip, callbacks: { label: (it) => T.pieTip(nf(it.parsed), (it.parsed / tot * 100).toFixed(1)) } } } },
            }));
            const legend = main.slice(0, small ? 6 : 10).map((x) => `<li><i style="background:${x.color}"></i><span>${esc(x.label)}</span><b>${(x.n / tot * 100).toFixed(1)}%</b></li>`).join('');
            return `<div class="bx-pie${small ? ' bx-pie--s' : ''}"><div class="bx-pie__chart"><canvas id="${id}"></canvas></div><ul class="bx-pie__legend">${legend}</ul></div>`;
        }
        // 勝率の順。回数の少ないものは下に回す
        const byRate = (list, minN) => list.sort((a, b) => ((b.a.n >= minN) - (a.a.n >= minN)) || (wr(b.a) ?? -1) - (wr(a.a) ?? -1) || b.a.n - a.a.n);

        // ---------- サマリー ----------
        function statsNote() {
            if (!total.sn || total.sn >= total.g || (!D.note.old && !D.note.mode)) return '';
            const who = lang === 'ja'
                ? (D.note.old ? T.noteOld : '') + (D.note.old && D.note.mode ? T.noteJoin : '') + (D.note.mode ? T.noteMode : '')
                : D.note.old && D.note.mode ? T.noteBoth : D.note.old ? T.noteOld : T.noteMode;
            return `<p class="bx-note">${T.noteHead}${who}${T.noteTail(nf(total.sn))}</p>`;
        }
        // 勝率の帯。文字が入りきらない区間のぶんは、帯の右上に出す
        function fitWbars() {
            root.querySelectorAll('.bx-wbar').forEach((bar) => {
                if (!bar.clientWidth) return;  // まだ表示されていない（幅が決まってから詰め直す）
                const out = bar.previousElementSibling, outside = [];
                bar.querySelectorAll('i').forEach((segEl) => {
                    const label = segEl.firstElementChild;
                    segEl.classList.remove('is-empty');
                    if (label.scrollWidth + 10 > segEl.clientWidth) {
                        segEl.classList.add('is-empty');
                        outside.push(`<span class="bx-key" style="--c:var(--bx-${segEl.dataset.kind})"><span>${label.innerHTML}</span></span>`);
                    }
                });
                out.innerHTML = outside.join('');
            });
        }
        function cardSummary() {
            const a = total, st = D.streak, v = wr(a), tiles = [];
            const part = (cls, kind, n, unitText) => (n ? `<i class="${cls}" data-kind="${kind}" style="flex:${n}"><span><b>${nf(n)}</b>${unitText}</span></i>` : '');
            tiles.push(`<div class="bx-tile bx-tile--wr"><span class="bx-tile__label">${T.winRate}</span><div class="bx-wrline"><span class="bx-tile__value">${v == null ? '—' : v.toFixed(1)}<small>%</small></span></div>
                <div class="bx-wbar-out"></div><div class="bx-wbar">${part('w', 'win', a.w, T.w)}${part('d', 'draw', a.d, T.d)}${part('l', 'lose', a.l, T.l)}</div></div>`);
            after.push(fitWbars);
            tiles.push(`<div class="bx-tile"><span class="bx-tile__label"><span class="bx-nw"><img src="${IMG}/ui/win_streak.png" alt="">${T.maxStreak}</span>${SL}<span class="bx-nw"><img src="${IMG}/ui/win_streak_blue.png" alt="">${T.loseStreak}</span></span>
                <span class="bx-tile__value"><span class="bx-nw">${st.bw}<small>${T.streakW}</small></span> <span class="bx-nw">${SL}${st.bl}<small>${T.streakL}</small></span></span><span class="bx-tile__sub">${st.cw >= 2 ? T.nowW(st.cw) : st.cl >= 2 ? T.nowL(st.cl) : '&nbsp;'}</span></div>`);
            if (D.tab === 'trophy') {
                tiles.push(`<div class="bx-tile ${a.tc > 0 ? 'is-up' : a.tc < 0 ? 'is-down' : ''}"><span class="bx-tile__label"><img src="${IMG}/ui/trophy.png" alt="">${T.trophyChange}</span><span class="bx-tile__value">${signed(a.tc)}</span><span class="bx-tile__sub">${T.perBattle} ${(a.g ? a.tc / a.g : 0).toFixed(2)}</span></div>`);
            }
            if (D.ranked && D.ranked.current) {
                const score = D.ranked.current[1], rank = Math.max(1, rankOf(score));
                tiles.push(`<div class="bx-tile"><span class="bx-tile__label">${T.currentPoints}</span><span class="bx-tile__value bx-tile__value--icon"><img src="${IMG}/ranked_icons/${rank}.png" alt="">${nf(score)}</span><span class="bx-tile__sub">${rankName(rank)}</span></div>`);
            }
            tiles.push(`<div class="bx-tile"><span class="bx-tile__label">${T.mvpRate}</span><span class="bx-tile__value">${a.n ? (a.sp / a.n * 100).toFixed(1) : '—'}<small>%</small></span><span class="bx-tile__sub">${nf(a.sp)}${T.times}</span></div>`);
            if (a.sn) {
                tiles.push(`<div class="bx-tile"><span class="bx-tile__label">${T.avgKD}</span><span class="bx-tile__value"><span class="bx-nw">${f1(a.k / a.sn)}<small>${T.killsUnit}</small></span> <span class="bx-nw">${SL}${f1(a.dth / a.sn)}<small>${T.deathsUnit}</small></span></span><span class="bx-tile__sub">${T.kdRatio} ${a.dth ? f2(a.k / a.dth) : '—'}</span></div>`);
                tiles.push(`<div class="bx-tile"><span class="bx-tile__label">${T.avgDamage}</span><span class="bx-tile__value">${short(a.dd / a.sn)}</span><span class="bx-tile__sub">${T.damageTaken} ${shortText(a.dt / a.sn)}</span></div>`);
            }
            if (a.dn) tiles.push(`<div class="bx-tile"><span class="bx-tile__label">${T.avgTime}</span><span class="bx-tile__value">${dur(a.du / a.dn)}</span><span class="bx-tile__sub">${T.totalTime} ${a.du >= 3600 ? nf(a.du / 3600) + T.hoursUnit : nf(a.du / 60) + T.minutesUnit}</span></div>`);
            if (tiles.length % 2 === 0) tiles[tiles.length - 1] = tiles[tiles.length - 1].replace('class="bx-tile"', 'class="bx-tile bx-tile--wide"');
            return card('summary', '', T.summary, T.lastBattles(nf(a.n), unitWord), '', `<div class="bx-tiles">${tiles.join('')}</div>${statsNote()}`);
        }

        // ---------- 日ごとの勝敗 ----------
        function cardDaily() {
            const range = Number(S.dailyRange) || 30, weekly = range > 45;
            const dayOf = (date) => Math.floor(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()) / 86400000);
            const today = dayOf(new Date());
            const step = weekly ? 7 : 1, count = Math.ceil(range / step);
            const bins = Array.from({ length: count }, (_, i) => ({ day: today - (count - 1 - i) * step, w: 0, l: 0, d: 0 }));
            for (const [hour, w, d, l] of D.recent) {
                const idx = count - 1 - Math.floor((today - dayOf(new Date(hour * 3600000))) / step);
                if (idx < 0 || idx >= count) continue;
                bins[idx].w += w; bins[idx].d += d; bins[idx].l += l;
            }
            const label = (day) => { const d = new Date(day * 86400000); return `${d.getUTCMonth() + 1}/${d.getUTCDate()}`; };
            const dow = (day) => T.dow[(new Date(day * 86400000).getUTCDay() + 6) % 7];
            mountChart('bxDaily', (th) => {
                const o = baseOptions(th);
                o.scales.x.stacked = true; o.scales.y.stacked = true;
                o.scales.y.ticks.callback = (v) => Math.abs(v);
                o.scales.y.ticks.precision = 0;
                o.scales.y.grid.color = (c) => (c.tick.value === 0 ? th.mut : th.grid);
                o.plugins.tooltip.callbacks = {
                    title: (items) => { const b = bins[items[0].dataIndex]; return weekly ? `${label(b.day - 6)}–${label(b.day)}` : `${label(b.day)} (${dow(b.day)})`; },
                    label: () => null,
                    afterBody: (items) => { const b = bins[items[0].dataIndex]; return [T.wdl(b.w, b.d, b.l), `${T.winRate} ${b.w + b.l ? (b.w / (b.w + b.l) * 100).toFixed(1) + '%' : '—'}`]; },
                };
                // 緑と赤で1本の角丸の棒に見せる（境目には角を付けない）
                return { type: 'bar', data: { labels: bins.map((b) => label(b.day)), datasets: [
                    { data: bins.map((b) => b.w), backgroundColor: th.win, borderRadius: { topLeft: 4, topRight: 4 }, maxBarThickness: 16, borderSkipped: 'bottom' },
                    { data: bins.map((b) => -b.l), backgroundColor: th.lose, borderRadius: { bottomLeft: 4, bottomRight: 4 }, maxBarThickness: 16, borderSkipped: 'bottom' }] }, options: o };
            });
            const tools = seg('dailyRange', range, [[14, T.d14], [30, T.d30], [91, T.w13]]);
            return card('daily', '', weekly ? T.weekly : T.daily, '', tools, `<div><span class="bx-key" style="--c:var(--bx-win)">${T.keyWin}</span><span class="bx-key" style="--c:var(--bx-lose)">${T.keyLose}</span></div><div class="bx-chart"><canvas id="bxDaily"></canvas></div>`);
        }

        // ---------- プレイ時間帯 ----------
        function tzLabel() {
            try {
                const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
                if (tz === 'Asia/Tokyo') return T.tzJapan;
                const part = new Intl.DateTimeFormat(lang, { timeZoneName: 'long' }).formatToParts(new Date()).find((x) => x.type === 'timeZoneName');
                return part ? part.value : tz;
            } catch (e) { return T.tzDevice; }
        }
        function cardHours() {
            // サーバーからは UTC の 曜日×時 で届く。この端末の時刻にずらす
            const shift = Math.round(-new Date().getTimezoneOffset() / 60);
            const cells = Array.from({ length: 7 }, () => Array.from({ length: 24 }, () => ({ n: 0, w: 0, l: 0 })));
            let max = 0;
            for (let i = 0; i < 168; i++) {
                const n = D.hours[i * 3];
                if (!n) continue;
                const local = ((i + shift) % 168 + 168) % 168, c = cells[Math.floor(local / 24)][local % 24];
                c.n += n; c.w += D.hours[i * 3 + 1]; c.l += D.hours[i * 3 + 2];
                if (c.n > max) max = c.n;
            }
            let html = '<div class="bx-heat"><span></span>';
            for (let h = 0; h < 24; h++) html += `<span class="bx-heat__h">${h % 3 === 0 ? h + (h === 21 ? `<small>${T.hourMark}</small>` : '') : ''}</span>`;
            let best = null;
            for (let d = 0; d < 7; d++) {
                html += `<span class="bx-heat__d">${T.dow[d]}</span>`;
                for (let h = 0; h < 24; h++) {
                    const c = cells[d][h];
                    if (!c.n) { html += '<span class="bx-heat__c"></span>'; continue; }
                    if (!best || c.n > best.n) best = { n: c.n, d, h };
                    const rate = c.w + c.l ? c.w / (c.w + c.l) : null;
                    let bg;
                    if (S.heatMode === 'wr') {
                        if (rate == null || c.n < 3) bg = 'var(--bx-track)';
                        else { const t = Math.min(1, Math.abs(rate - 0.5) * 2.6); bg = `color-mix(in srgb, ${rate >= 0.5 ? 'var(--bx-win)' : 'var(--bx-lose)'} ${Math.round(12 + t * 88)}%, var(--bx-track))`; }
                    } else bg = `rgba(91,127,214,${(0.14 + 0.86 * c.n / max).toFixed(3)})`;
                    html += `<span class="bx-heat__c" style="background:${bg}"${tipAttr(T.heatCell(T.dow[d], h, c.n, rate == null ? '—' : (rate * 100).toFixed(1) + '%'))}></span>`;
                }
            }
            html += '</div>';
            // 時間帯ごとの勝率（3時間ずつ）
            const slots = Array.from({ length: 8 }, () => ({ n: 0, w: 0, l: 0 }));
            for (let d = 0; d < 7; d++) for (let h = 0; h < 24; h++) { const s = slots[Math.floor(h / 3)], c = cells[d][h]; s.n += c.n; s.w += c.w; s.l += c.l; }
            const totalN = slots.reduce((x, s) => x + s.n, 0);
            const good = slots.map((s, i) => ({ i, n: s.n, rate: s.w + s.l ? s.w / (s.w + s.l) * 100 : null })).filter((s) => s.rate != null && s.n >= Math.max(5, totalN * 0.04)).sort((a, b) => b.rate - a.rate)[0];
            const legend = S.heatMode === 'wr'
                ? `<div class="bx-heatlegend">${T.rateHigh}<i style="background:var(--bx-win)"></i><i style="background:color-mix(in srgb,var(--bx-win) 40%,var(--bx-track))"></i><i style="background:var(--bx-track)"></i><i style="background:color-mix(in srgb,var(--bx-lose) 40%,var(--bx-track))"></i><i style="background:var(--bx-lose)"></i>${T.rateLow}</div>`
                : `<div class="bx-heatlegend">${T.few}<i style="background:rgba(91,127,214,.14)"></i><i style="background:rgba(91,127,214,.4)"></i><i style="background:rgba(91,127,214,.7)"></i><i style="background:rgba(91,127,214,1)"></i>${T.many}</div>`;
            const facts = `<div class="bx-facts bx-facts--col">${best ? `<span>${T.mostActive} ${T.mostActiveAt(T.dow[best.d], best.h, nf(best.n))}</span>` : ''}${good ? `<span>${T.bestRate} ${T.bestRateAt(good.i * 3, good.i * 3 + 3, good.rate.toFixed(1))}</span>` : ''}</div>`;
            return card('hours', '', T.hours, `${T.tz}${esc(tzLabel())}`, seg('heatMode', S.heatMode, [['n', T.battleCount], ['wr', T.winRate]]), max ? html + legend + facts : `<div class="bx-empty">${T.empty}</div>`);
        }

        // ---------- ガチバトル: ポイント（無ければランク）の推移 ----------
        // 補助線の位置。範囲に入るランクの境目の数を見て、細かさを決める
        function rankTicks(lo, hi) {
            const inRange = (list) => list.filter((v) => v >= lo && v <= hi);
            const subs = inRange(RANK_MIN.slice(1)), leagues = inRange([1, 4, 7, 10, 13, 16, 19, 22].map((r) => RANK_MIN[r]));
            if (subs.length > 9 && leagues.length >= 2) return leagues;   // 広い範囲: 帯（ブロンズ・シルバー…）の境目だけ
            if (subs.length >= 3) return subs;                              // ふつう: ランクの境目すべて
            // 狭い範囲: 境目に、きりのいい値を足す
            const span = hi - lo, step = [25, 50, 100, 250, 500].find((s) => span / s <= 6) || 500;
            const out = new Set(subs);
            for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) if (!subs.some((s) => Math.abs(s - v) < step * 0.45)) out.add(v);
            return [...out].sort((a, b) => a - b);
        }
        function cardRankedTrend() {
            const rk = D.ranked;
            if (!rk || !rk.points || rk.points.length < 2) return '';
            const pts = rk.points.map(([t, y]) => ({ x: t * 1000, y })), byRank = rk.mode === 'rank';
            const ys = pts.map((p) => p.y), dataLo = Math.min(...ys), dataHi = Math.max(...ys);
            const pad = byRank ? 0.5 : Math.max(30, (dataHi - dataLo) * 0.08);
            const lo = byRank ? Math.max(0.5, dataLo - pad) : Math.max(0, Math.floor(dataLo - pad)), hi = byRank ? Math.min(22.5, dataHi + pad) : Math.ceil(dataHi + pad);
            // 背景にランクの帯の色を薄く敷き、右端にランクの名前を出す
            const bands = {
                id: 'rankBands',
                beforeDatasetsDraw(chart) {
                    const { ctx, chartArea: area, scales: { y } } = chart;
                    ctx.save();
                    ctx.beginPath(); ctx.rect(area.left, area.top, area.width, area.height); ctx.clip();
                    for (let rank = 1; rank <= 22; rank++) {
                        const from = byRank ? rank - 0.5 : RANK_MIN[rank], to = byRank ? rank + 0.5 : rank < 22 ? RANK_MIN[rank + 1] : Infinity;
                        if (to <= lo || from >= hi) continue;
                        const top = y.getPixelForValue(Math.min(to, hi)), bottom = y.getPixelForValue(Math.max(from, lo));
                        ctx.globalAlpha = 0.09 + ((rank - 1) % 3) * 0.035;
                        ctx.fillStyle = LEAGUE_COLORS[leagueIndex(rank)];
                        ctx.fillRect(area.left, top, area.width, bottom - top);
                        if (!byRank && bottom - top >= 18) { ctx.globalAlpha = 0.8; ctx.font = `600 11px ${CHART_FONT}`; ctx.textAlign = 'right'; ctx.textBaseline = 'middle'; ctx.fillText(rankName(rank), area.right - 6, (top + bottom) / 2); }
                    }
                    ctx.restore();
                },
            };
            const ticks = byRank ? null : rankTicks(lo, hi);
            mountChart('bxRanked', (th) => {
                const o = baseOptions(th);
                o.scales.x.type = 'linear'; o.scales.x.ticks.callback = (v) => ymd(v / 1000); o.scales.x.min = pts[0].x; o.scales.x.max = pts[pts.length - 1].x;
                o.scales.y.min = lo; o.scales.y.max = hi;
                o.scales.y.grid.color = th.mut + '55';
                if (byRank) {
                    // 目盛りはランクごと。範囲が広いときは、帯の初めのランク（Ⅰ）だけ
                    const shown = [];
                    for (let rank = Math.ceil(lo); rank <= Math.floor(hi); rank++) if (hi - lo <= 9 || (rank - 1) % 3 === 0) shown.push(rank);
                    o.scales.y.afterBuildTicks = (axis) => { axis.ticks = shown.map((value) => ({ value })); };
                    o.scales.y.ticks.maxTicksLimit = 23;
                    o.scales.y.ticks.callback = (v) => rankName(v);
                    o.scales.y.grid.display = false;
                    o.plugins.tooltip.callbacks = { title: (it) => ymdFull(it[0].parsed.x / 1000), label: (it) => rankName(it.parsed.y) };
                } else {
                    o.scales.y.afterBuildTicks = (axis) => { axis.ticks = ticks.map((value) => ({ value })); };
                    o.scales.y.ticks.callback = (v) => nf(v);
                    o.scales.y.ticks.maxTicksLimit = 30;
                    o.plugins.tooltip.callbacks = { title: (it) => ymdFull(it[0].parsed.x / 1000), label: (it) => T.pointsTip(nf(it.parsed.y), rankName(Math.max(1, rankOf(it.parsed.y)))) };
                }
                return { type: 'line', data: { datasets: [{ data: pts, borderColor: th.ink, borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: byRank ? 0 : 0.15, stepped: byRank }] }, options: o, plugins: [bands] };
            });
            const first = pts[0].y, last = pts[pts.length - 1].y;
            const cap = byRank ? `${rankName(first)} → ${rankName(last)}` : `${nf(first)} → ${nf(last)}（${signed(last - first)}）`;
            return card('ranked', 'bx-span2', byRank ? T.rankedRank : T.rankedScore, cap, '', '<div class="bx-chart bx-chart--tall"><canvas id="bxRanked"></canvas></div>');
        }

        // ---------- ガチバトル: モード・マップ別の成績（マップごとによく使ったキャラ。グラフは勝率のレーダー） ----------
        function cardRankedModes() {
            const g = D.modes.map((row) => ({ id: row[0], a: toAgg(row, 1) })).sort((x, y) => y.a.n - x.a.n);
            if (!g.length) return '';
            const maps = D.maps.map((row) => ({ m: row[0], e: row[1], a: toAgg(row, 2) }));
            const picks = new Map((D.picks || []).map(([m, e, top]) => [m + ':' + e, top]));
            const tools = (S.mview === 'list' ? seg('msort', S.msort, [['n', T.plays], ['wr', T.winRate]]) : seg('mgraph', S.mgraph, [['mode', T.byMode], ['map', T.byMap]])) + seg('mview', S.mview, [['list', T.list], ['pie', T.graph]]);
            if (S.mview === 'pie') {
                const radar = (id, labels, values, counts, color) => mountChart(id, (th) => ({
                    type: 'radar', data: { labels, datasets: [{ data: values, borderColor: color, backgroundColor: color + '33', borderWidth: 2, pointRadius: 3, pointBackgroundColor: color }] },
                    options: { responsive: true, maintainAspectRatio: false, animation: { duration: 250 }, plugins: { legend: { display: false }, tooltip: { ...baseOptions(th).plugins.tooltip, callbacks: { label: (it) => T.radarTip(it.parsed.r.toFixed(1), nf(counts[it.dataIndex])) } } },
                        scales: { r: { min: 0, max: 100, ticks: { stepSize: 25, color: th.mut, backdropColor: 'transparent', font: { size: 9.5 }, callback: (v) => (v === 50 || v === 100 ? v + '%' : '') }, grid: { color: (ctx) => (ctx.tick.value === 50 ? th.mut : th.grid) }, angleLines: { color: th.grid }, pointLabels: { color: th.ink, font: { size: 11.5 } } } } },
                }));
                const rated = g.filter((x) => wr(x.a) != null);
                let graph;
                if (S.mgraph === 'mode') {
                    if (rated.length < 3) graph = `<div class="bx-empty">${T.radarNeedModes}</div>`;
                    else { radar('bxRadarM', rated.map((x) => mname(x.id)), rated.map((x) => wr(x.a)), rated.map((x) => x.a.n), chartTheme().accent); graph = '<div class="bx-chart bx-radar"><canvas id="bxRadarM"></canvas></div>'; }
                } else {
                    const blocks = rated.map(({ id }) => {
                        const ms = maps.filter((x) => x.m === id && wr(x.a) != null).sort((x, y) => y.a.n - x.a.n).slice(0, 8);
                        if (ms.length < 3) return '';
                        const icon = micon(id);
                        radar('bxRadarMap' + id, ms.map((x) => mapname(x.e)), ms.map((x) => wr(x.a)), ms.map((x) => x.a.n), mcolor(id));
                        return `<div class="bx-radarcell"><div class="bx-radarcell__title"><span class="bx-modeicon" style="--c:${mcolor(id)}">${icon ? `<img src="${esc(icon)}" alt="" onerror="this.remove()">` : ''}</span>${esc(mname(id))}</div><div class="bx-chart bx-radar bx-radar--s"><canvas id="bxRadarMap${id}"></canvas></div></div>`;
                    }).filter(Boolean);
                    graph = blocks.length ? `<div class="bx-radargrid">${blocks.join('')}</div>` : `<div class="bx-empty">${T.radarNeedMaps}</div>`;
                }
                return card('modes', 'bx-span2', T.modesMaps, S.mgraph === 'mode' ? T.modeRates : T.mapRates, tools, graph);
            }
            if (S.msort === 'wr') byRate(g, Math.max(5, Math.round(total.n * 0.01)));
            const max = Math.max(...g.map((x) => x.a.n));
            const wide = window.matchMedia('(min-width: 840px)').matches;  // PC 幅は初めからマップを開く。スマホ幅は押してから
            const list = V.mmore ? g : g.slice(0, 8);
            const body = list.map(({ id, a }) => {
                const ms = maps.filter((x) => x.m === id).sort((x, y) => y.a.n - x.a.n);
                const open = wide ? !V.mopen.has('c' + id) : V.mopen.has(id), icon = micon(id);
                const mapRows = ms.map((x) => {
                    const top = (picks.get(id + ':' + x.e) || []).map(([b, n]) => `<img src="${bimg(b)}" alt="" loading="lazy"${tipAttr(T.pickTip(bname(b), nf(n)))}>`).join('');
                    return `<div class="bx-row bx-row--map${x.a.n < 3 ? ' is-thin' : ''}"><span></span><span class="bx-name">${esc(mapname(x.e))}</span><span class="bx-bar" style="--c:${mcolor(id)}"><i style="width:${x.a.n / max * 100}%"></i></span><span class="bx-num">${times(x.a.n)}</span>${wrCell(x.a)}<span class="bx-picks">${top}</span><span></span></div>`;
                }).join('');
                return `<div class="bx-modeblock"><button type="button" class="bx-row${open ? ' is-open' : ''}" data-act="mopen" data-id="${id}"${ms.length ? '' : ' disabled'}>
                    <span class="bx-modeicon" style="--c:${mcolor(id)}">${icon ? `<img src="${esc(icon)}" alt="" loading="lazy" onerror="this.remove()">` : ''}</span><span class="bx-name">${esc(mname(id))}</span>
                    <span class="bx-bar" style="--c:${mcolor(id)}"><i style="width:${a.n / max * 100}%"></i></span><span class="bx-num">${times(a.n)}</span>${wrCell(a)}<span></span>${ms.length ? '<span class="bx-chev"></span>' : '<span></span>'}</button>
                    ${ms.length ? `<div class="bx-maps${open ? ' is-open' : ''}"><div>${mapRows}</div></div>` : ''}</div>`;
            }).join('');
            const more = g.length > 8 ? `<button type="button" class="bx-more" data-act="mmore">${V.mmore ? T.showTop : T.showAllModes(g.length)}</button>` : '';
            return card('modes', 'bx-span2', T.modesMaps, T.picksCap, tools, `<div class="bx-scrollx"><div class="bx-list bx-list--mode has-picks bx-modecols">${body}</div></div>${more}`);
        }

        // ---------- ガチバトル: BAN ----------
        function cardBans() {
            const bans = D.bans;
            if (!bans) return '';
            const groups = [[T.banMine, bans.mine, 'bxPieBan0'], [T.banOpp, bans.opp, 'bxPieBan1'], [T.banTeam, bans.team, 'bxPieBan2']].filter((x) => x[1].length);
            let body;
            if (S.banview === 'pie') body = `<div class="bx-banpies">${groups.map(([label, list, id]) => `<div class="bx-bangroup"><div class="bx-bangroup__label">${label}</div>${pieBody(id, list.map(([b, n]) => ({ label: bname(b), n, color: rcolor(b) })), true)}</div>`).join('')}</div>`;
            else body = groups.map(([label, list]) => `<div class="bx-bangroup"><div class="bx-bangroup__label">${label}</div><div class="bx-bans">${list.slice(0, 9).map(([b, n]) => `<div class="bx-ban"${tipAttr(T.banTip(bname(b), nf(n), (n / bans.n * 100).toFixed(1)))}><img src="${bimg(b)}" alt="${esc(bname(b))}" loading="lazy"><span>${nf(n)}</span></div>`).join('')}</div></div>`).join('');
            return card('bans', '', T.bans, T.bansCap(nf(bans.n)), viewSeg('banview', S.banview), body);
        }

        // ---------- キャラ別の成績 ----------
        function cardBrawlers() {
            const g = D.brawlers.map((row) => ({ id: row[0], a: toAgg(row, 1) }));
            if (!g.length) return '';
            const minN = Math.max(3, Math.round(total.n * 0.004));
            if (S.bsort === 'wr') byRate(g, minN); else g.sort((x, y) => y.a.n - x.a.n);
            const tools = (S.bview === 'list' ? seg('bsort', S.bsort, [['n', T.uses], ['wr', T.winRate]]) : '') + viewSeg('bview', S.bview);
            if (S.bview === 'pie') return card('brawlers', '', T.brawlers, T.brawlerCount(g.length), tools, pieBody('bxPieB', g.map((x) => ({ label: bname(x.id), n: x.a.n, color: rcolor(x.id) }))));
            const max = Math.max(...g.map((x) => x.a.n));
            const list = V.bmore ? g : g.slice(0, 8);
            const head = `<div class="bx-row bx-row--head"><span></span><span>${T.brawler}</span><span>${T.uses}</span><span></span><span>${T.winRate}</span><span style="text-align:right">${T.kills}${SL}${T.deaths}</span><span style="text-align:right">${T.damageShort}</span></div>`;
            const body = list.map(({ id, a }) => `<a class="bx-row${a.n < minN ? ' is-thin' : ''}" href="${esc(root.dataset.brawlerHref + id)}"><img class="bx-ava" src="${bimg(id)}" alt="" loading="lazy"><span class="bx-name">${esc(bname(id))}</span>
                <span class="bx-bar" style="--c:${rcolor(id)}"><i style="width:${a.n / max * 100}%"></i></span><span class="bx-num">${times(a.n)}</span>${wrCell(a)}
                <span class="bx-num">${a.sn ? `${f1(a.k / a.sn)}${SL}${f1(a.dth / a.sn)}` : '—'}</span><span class="bx-num">${a.sn ? short(a.dd / a.sn) : '—'}</span></a>`).join('');
            const more = g.length > 8 ? `<button type="button" class="bx-more" data-act="bmore">${V.bmore ? T.showTop : T.showAllBrawlers(g.length)}</button>` : '';
            return card('brawlers', '', T.brawlers, T.brawlerCount(g.length), tools, `<div class="bx-scrollx"><div class="bx-list bx-list--brawler">${head}${body}</div></div>${more}`);
        }

        // ---------- モード別の成績 ----------
        function cardModes() {
            if (D.tab === 'powerleague') return cardRankedModes();
            const g = D.modes.map((row) => ({ id: row[0], a: toAgg(row, 1) })).sort((x, y) => y.a.n - x.a.n);
            if (!g.length) return '';
            if (S.msort === 'wr') byRate(g, Math.max(5, Math.round(total.n * 0.01)));
            const max = Math.max(...g.map((x) => x.a.n));
            const maps = D.maps.map((row) => ({ m: row[0], e: row[1], a: toAgg(row, 2) }));
            // 形式の内訳（1本の帯）
            let fmtHtml = '';
            if (D.formats.length > 1) {
                const top = D.formats.slice(0, 5).map(([k, n]) => [k, n]), rest = D.formats.slice(5).reduce((x, f) => x + f[1], 0);
                if (rest) { const o = top.find((f) => f[0] === 'other'); if (o) o[1] += rest; else top.push(['other', rest]); }
                const totalN = top.reduce((x, f) => x + f[1], 0);
                fmtHtml = `<div class="bx-fmt">${top.map(([k, n]) => { const p = n / totalN * 100; return `<i style="flex:${n};--c:${FMT_COLORS[k] || '#9a9a9a'}" class="${FMT_DARK.has(k) ? 'is-dark' : ''}"${tipAttr(T.fmtTip(fmtName(k), nf(n), unitWord, p.toFixed(1)))}>${p >= 9 ? p.toFixed(0) + '%' : ''}</i>`; }).join('')}</div>
                    <div class="bx-fmtkeys">${top.map(([k]) => `<span class="bx-key" style="--c:${FMT_COLORS[k] || '#9a9a9a'}">${fmtName(k)}</span>`).join('')}</div>`;
            }
            const tools = (S.mview === 'list' ? seg('msort', S.msort, [['n', T.plays], ['wr', T.winRate]]) : '') + viewSeg('mview', S.mview);
            if (S.mview === 'pie') return card('modes', '', T.modes, '', tools, fmtHtml + pieBody('bxPieM', g.map((x) => ({ label: mname(x.id), n: x.a.n, color: mcolor(x.id) }))));
            const list = V.mmore ? g : g.slice(0, 8);
            const head = `<div class="bx-row bx-row--head"><span></span><span>${T.mode}</span><span>${T.plays}</span><span></span><span>${T.winRate}</span><span></span></div>`;
            const body = list.map(({ id, a }) => {
                const ms = maps.filter((x) => x.m === id).sort((x, y) => y.a.n - x.a.n);
                const open = V.mopen.has(id), icon = micon(id);
                const mapRows = ms.map((x) => `<div class="bx-row bx-row--map${x.a.n < 3 ? ' is-thin' : ''}"><span></span><span class="bx-name">${esc(mapname(x.e))}</span><span class="bx-bar" style="--c:${mcolor(id)}"><i style="width:${x.a.n / max * 100}%"></i></span><span class="bx-num">${times(x.a.n)}</span>${wrCell(x.a)}<span></span></div>`).join('');
                return `<div class="bx-modeblock"><button type="button" class="bx-row${open ? ' is-open' : ''}" data-act="mopen" data-id="${id}"${ms.length ? '' : ' disabled'}>
                    <span class="bx-modeicon" style="--c:${mcolor(id)}">${icon ? `<img src="${esc(icon)}" alt="" loading="lazy" onerror="this.remove()">` : ''}</span><span class="bx-name">${esc(mname(id))}</span>
                    <span class="bx-bar" style="--c:${mcolor(id)}"><i style="width:${a.n / max * 100}%"></i></span><span class="bx-num">${times(a.n)}</span>${wrCell(a)}${ms.length ? '<span class="bx-chev"></span>' : '<span></span>'}</button>
                    ${ms.length ? `<div class="bx-maps${open ? ' is-open' : ''}"><div>${mapRows}</div></div>` : ''}</div>`;
            }).join('');
            const more = g.length > 8 ? `<button type="button" class="bx-more" data-act="mmore">${V.mmore ? T.showTop : T.showAllModes(g.length)}</button>` : '';
            return card('modes', '', T.modes, T.modesCap, tools, `${fmtHtml}<div class="bx-scrollx"><div class="bx-list bx-list--mode">${head}${body}</div></div>${more}`);
        }

        // ---------- 一緒にプレイしたプレイヤー ----------
        function cardMates() {
            const friendly = D.tab === 'friendly';
            const g = D.mates.map(([tag, name, n, w, d, l]) => ({ tag, name, a: { n, w, d, l } }));
            if (!g.length) return '';
            const cap = friendly ? T.matesCapFriendly : T.matesCap;
            const tools = (S.mateview === 'list' && !friendly ? seg('matesort', S.matesort, [['n', T.count], ['wr', T.winRate]]) : '') + viewSeg('mateview', S.mateview);
            if (S.mateview === 'pie') return card('mates', '', T.mates, cap, tools, pieBody('bxPieP', g.map((x) => ({ label: x.name, n: x.a.n }))));
            if (S.matesort === 'wr' && !friendly) byRate(g, Math.max(3, Math.round(g[0].a.n * 0.1)));
            const max = Math.max(...g.map((x) => x.a.n)), list = V.matemore ? g : g.slice(0, 6);
            const head = `<div class="bx-row bx-row--head"><span>${T.player}</span><span>${T.count}</span><span></span><span>${friendly ? '' : T.rateWith}</span></div>`;
            const body = list.map(({ tag, name, a }) => `<a class="bx-row" href="/${lang}/player/profile/${encodeURIComponent(tag.replace(/^#/, ''))}"><span class="bx-name">${esc(name)}</span><span class="bx-bar"><i style="width:${a.n / max * 100}%"></i></span><span class="bx-num">${times(a.n)}</span>${friendly ? '<span></span>' : wrCell(a)}</a>`).join('');
            const more = g.length > 6 ? `<button type="button" class="bx-more" data-act="matemore">${V.matemore ? T.showTop : T.showMoreMates(g.length)}</button>` : '';
            return card('mates', '', T.mates, cap, tools, `<div class="bx-scrollx"><div class="bx-list bx-list--mate">${head}${body}</div></div>${more}`);
        }

        // ---------- トロフィー ----------
        function cardTrophyTrend() {
            const tr = D.trophy;
            if (!tr || tr.points.length < 2) return '';
            const pts = tr.points.map(([t, y]) => ({ x: t * 1000, y })), abs = tr.mode === 'abs';
            mountChart('bxTrophy', (th) => {
                const o = baseOptions(th);
                o.scales.x.type = 'linear'; o.scales.x.ticks.callback = (v) => ymd(v / 1000); o.scales.x.min = pts[0].x; o.scales.x.max = pts[pts.length - 1].x;
                o.scales.y.ticks.callback = (v) => (abs ? nf(v) : signed(v));
                if (!abs) o.scales.y.grid.color = (c) => (c.tick.value === 0 ? th.mut : th.grid);
                o.plugins.tooltip.callbacks = abs
                    ? { title: (it) => ymdFull(it[0].parsed.x / 1000), label: (it) => `${nf(it.parsed.y)} ${T.trophyUnit}` }
                    : { title: (it) => ymd(it[0].parsed.x / 1000), label: (it) => T.trophySumTip(signed(it.parsed.y)) };
                return { type: 'line', data: { datasets: [{ data: pts, borderColor: '#e0a100', backgroundColor: 'rgba(224,161,0,.12)', fill: abs ? true : 'origin', borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: 0.15 }] }, options: o };
            });
            const body = '<div class="bx-chart bx-chart--tall"><canvas id="bxTrophy"></canvas></div>';
            if (abs) {
                const only = D.brawlers.length === 1 ? bname(D.brawlers[0][0]) + ': ' : '';
                return card('trophy', 'bx-span2', T.trophyAbs, `${esc(only)}${nf(pts[0].y)} → ${nf(pts[pts.length - 1].y)}`, '', body);
            }
            return card('trophy', 'bx-span2', T.trophySum, T.trophySumCap(signed(pts[pts.length - 1].y)), '', body);
        }
        function cardTrophyByBrawler() {
            const g = ((D.trophy && D.trophy.byBrawler) || []).map(([id, games, tc]) => ({ id, games, tc }));
            if (g.length < 2) return '';
            g.sort((x, y) => y.tc - x.tc);
            // 増えたキャラと減ったキャラの両方があれば5つずつ。片方しか無ければ、変動の大きい順に10
            const mixed = g[0].tc > 0 && g[g.length - 1].tc < 0;
            const pick = g.length <= 10 ? g : mixed ? g.slice(0, 5).concat(g.slice(-5)) : g[0].tc > 0 ? g.slice(0, 10) : g.slice(-10);
            const max = Math.max(...pick.map((x) => Math.abs(x.tc))), one = pick.every((x) => x.tc > 0);
            const body = pick.map(({ id, games, tc }) => `<a class="bx-row" href="${esc(root.dataset.brawlerHref + id)}"><img class="bx-ava" src="${bimg(id)}" alt="" loading="lazy"><span class="bx-name">${esc(bname(id))}</span>
                <span class="bx-delta${one ? ' bx-delta--one' : ''}"${tipAttr(T.trophyByTip(nf(games), signed(tc)))}>${tc < 0 ? `<i class="neg" style="width:${Math.abs(tc) / max * 100}%"></i>` : `<i class="pos" style="width:${tc / max * 100}%"></i>`}</span><span class="bx-num ${tc > 0 ? 'bx-pos' : 'bx-neg'}">${signed(tc)}</span></a>`).join('');
            return card('trophyb', '', T.trophyBy, g.length > 10 ? (mixed ? T.trophyByCap : T.trophyByTop) : '', '', `<div class="bx-scrollx"><div class="bx-list bx-list--delta">${body}</div></div>`);
        }

        // ---------- 描画 ----------
        const CARD_FN = { daily: cardDaily, hours: cardHours, brawlers: cardBrawlers, modes: cardModes, mates: cardMates, bans: cardBans };
        const runAfter = () => { const run = after; after = []; run.forEach((f) => f()); };
        function render() {
            const summarySlot = root.querySelector('[data-bx-slot="summary"]'), cardsSlot = root.querySelector('[data-bx-slot="cards"]');
            if (!summarySlot || !cardsSlot) return;
            summarySlot.innerHTML = cardSummary();
            const filtered = root.dataset.brawlerFilter === '1';
            const ranked = D.tab === 'powerleague';
            let html = '';
            if (D.tab === 'trophy') html += cardTrophyTrend();
            if (ranked) html += cardRankedTrend();
            html += cardDaily() + cardHours();
            if (ranked) html += cardModes();
            if (!filtered) html += cardBrawlers();
            if (!ranked) html += cardModes();
            if (D.tab === 'trophy' && !filtered) html += cardTrophyByBrawler();
            if (ranked) html += cardBans();
            html += cardMates();
            cardsSlot.innerHTML = html;
            runAfter();
        }
        // カード1枚だけ描き直す（ほかのカードのグラフは動かさない）
        function rerenderCard(key) {
            const el = root.querySelector(`[data-card="${key}"]`);
            if (!el || !CARD_FN[key]) return;
            for (let i = charts.length - 1; i >= 0; i--) if (el.contains(charts[i].canvas)) { charts[i].destroy(); charts.splice(i, 1); }
            after = [];
            const html = CARD_FN[key]();
            if (!html) return;
            el.outerHTML = html;
            runAfter();
        }

        const SEG_CARD = { dailyRange: 'daily', heatMode: 'hours', bsort: 'brawlers', bview: 'brawlers', msort: 'modes', mview: 'modes', mgraph: 'modes', matesort: 'mates', mateview: 'mates', banview: 'bans' };
        root.addEventListener('click', (e) => {
            const segBtn = e.target.closest('.bx-seg button');
            if (segBtn && root.contains(segBtn)) {
                const name = segBtn.parentElement.dataset.seg;
                if (!SEG_CARD[name]) return;
                S[name] = name === 'dailyRange' ? Number(segBtn.dataset.v) : segBtn.dataset.v;
                savePrefs();
                rerenderCard(SEG_CARD[name]);
                return;
            }
            const act = e.target.closest('[data-act]');
            if (!act || !root.contains(act)) return;
            const a = act.dataset.act;
            if (a === 'fold') {
                const cardEl = act.closest('.bx-card'), closed = closedSet();
                const nowClosed = cardEl.classList.toggle('is-closed');
                act.setAttribute('aria-expanded', String(!nowClosed));
                if (nowClosed) closed.add(cardEl.dataset.card); else closed.delete(cardEl.dataset.card);
                writeJson(CLOSED_KEY, [...closed]);
                if (!nowClosed) setTimeout(() => { charts.forEach((c) => { if (cardEl.contains(c.canvas)) c.resize(); }); fitWbars(); }, 220);
            } else if (a === 'mopen') {
                // ガチバトルの PC 幅は「初めから開いている」ので、閉じたものを覚える
                const key = D.tab === 'powerleague' && window.matchMedia('(min-width: 840px)').matches ? 'c' + act.dataset.id : Number(act.dataset.id);
                if (V.mopen.has(key)) V.mopen.delete(key); else V.mopen.add(key);
                act.classList.toggle('is-open');
                const mapsEl = act.nextElementSibling;
                if (mapsEl && mapsEl.classList.contains('bx-maps')) mapsEl.classList.toggle('is-open');
            } else if (a === 'bmore') { V.bmore = !V.bmore; rerenderCard('brawlers'); }
            else if (a === 'mmore') { V.mmore = !V.mmore; rerenderCard('modes'); }
            else if (a === 'matemore') { V.matemore = !V.matemore; rerenderCard('mates'); }
        });

        current = { root, fitWbars };
        render();
        // フラグメントは隠れた状態で差し込まれることがあるので、幅が決まった・変わったときに帯の文字を詰め直す
        if (window.ResizeObserver) new ResizeObserver(() => fitWbars()).observe(root);
    };
})();
