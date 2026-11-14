// バトル履歴ページ: 「詳細分析」タブ
// ログイン中は、1人ぶんのバトル（1試合1行の数値データ）を1回だけ読み、絞り込み・集計・並べ替えはここで行う。
// 未ログインには、サーバーが埋め込んだ集計（見せるぶんだけ）で機能の紹介を描く。
// フラグメントはサブタブの切り替えのたびに差し替わるので、差し込まれるたびに window.initBattleAnalysis(root) を呼ぶ
(function () {
    'use strict';

    const K = window.BattleStatsKit;  // battle_stats.js の小物
    if (!K) return;
    const { CLOSED_KEY, PREF_KEY, CHART_FONT, SL, esc, nf, f1, f2, pc, signed, dur, toAgg, wr, readJson, writeJson } = K;
    const PER_PAGE = 30;
    const TYPE_ICON = ['ui/trophy.png', 'ui/ranked.png', 'mode_icons/friendly.png', null];
    const AGG_KEYS = ['n', 'w', 'd', 'l', 'sp', 'sn', 'k', 'dth', 'dd', 'he', 'dt', 'dn', 'du', 'tc', 'g', 'mg'];
    const NUM_KEYS = ['kmin', 'ddmin', 'dumin', 'dumax', 'rkmax', 'trmin', 'trmax'];  // 数値で絞り込む条件
    const CARD_CACHE_MAX = 12;

    // [この部分は公開用リポジトリでは非公開にされています]

    const TEXT = {
        ja: {
            dow: ['日', '月', '火', '水', '木', '金', '土'], dowName: (d) => d + '曜', hourName: (h) => `${h}時台`,
            types: ['トロフィー', 'ガチバトル', 'フレンドバトル', 'その他'], other: 'その他', unknownMap: '不明なマップ', typeOther: '他',
            dateTo: '〜', seasonPick: 'シーズンを選択',
            durRange: (a, b) => `バトル時間 ${a}〜${b} 秒`, rkmax: (input) => `順位 ${input} 位以内`, trRange: (a, b) => `トロフィー ${a}〜${b}`, ban: 'BAN: ',
            highlights: 'ハイライト', highlightsCap: '一定の回数以上のものから',
            hl: { brawler: '勝率の高いキャラ', mapGood: '得意なマップ', mapBad: '苦手なマップ', oppGood: '得意な相手キャラ', oppBad: '苦手な相手キャラ', mate: '相性のいい味方' },
            hlSub: (rate, w, l) => `勝率 ${rate}（${w}勝${l}敗）`,
            trend: '推移', metrics: { wr: '勝率', kd: 'キルデス比', dd: '与ダメ', dt: '被ダメ', he: '回復', du: 'バトル時間', tc: 'トロフィー増減' },
            day: '日', week: '週', chart: 'グラフ', table: '表', trendNeed: '推移を出すにはバトルが足りません', from: '〜',
            trendCount: (n, game, w, l) => `${n}${game ? '試合' : '件'}・${w}勝${l}敗`, colCount: '回数', colWL: '勝敗', wl: (w, l) => `${w}勝${l}敗`,
            hist: { dd: '与ダメ', k: 'キル', d: 'デス', dt: '被ダメ', du: 'バトル時間', rk: '順位' },
            histTitle: { dd: '与ダメージの分布', k: 'キルの分布', d: 'デスの分布', dt: '被ダメージの分布', du: 'バトル時間の分布', rk: '順位の分布' },
            histCap: '1試合ごと', histNeed: '分布を出すにはバトルが足りません', histTip: (range, n, p) => `${range}\n${n}試合（${p}%）`, avg: '平均', median: '中央値', max: '最大', games: (n) => `${n}試合`,
            hours: 'プレイ時間帯', tz: '時刻: ', tzJapan: '日本時間', tzDevice: 'この端末の時刻', hourMark: '時', battleCount: 'バトル数',
            heatCell: (dow, h, n, rate) => `${dow}曜 ${h}時台\n${n}戦・勝率 ${rate}`, few: '少', many: '多', rateHigh: '勝率 高', rateLow: '低（3戦未満は色なし）',
            mostActive: '最もプレイした時間帯は', mostActiveAt: (dow, h, n) => `<b>${dow}曜 ${h}時台</b>（${n}戦）`, bestRate: '勝率が高いのは', bestRateAt: (from, to, rate) => `<b>${from}〜${to}時</b>（${rate}%）`,
            viewCard: 'カード', viewTable: '表', openResults: 'リザルトを開く', cardsLoading: 'バトルを読み込み中...', cardsCap: (n, game) => `${n}${game ? '試合' : '件'}`,
            featHl: 'ハイライト', featHlLead: '得意・苦手なキャラやマップが、条件を変えるたびにすぐ分かります。', ctaHl: 'ログインしてすべて見る',
            featTrend: 'スタッツの推移と分布', featTrendLead: '勝率やキルデス比、与ダメージが、日ごと・週ごとにどう変わってきたかをグラフで追えます。', ctaTrend: 'ログインして推移を見る',
            fType: '種別', fPeriod: '期間', fPicks: '条件', fMore: '詳細条件', fResult: '結果', fFormat: '形式', fDow: '曜日', fValues: '数値', fUnit: '集計',
            periods: [['all', 'すべて'], [7, '7日'], [30, '30日'], [90, '90日'], [365, '1年']],
            results: ['勝ち', '引き分け', '負け'], mvpOnly: '★ MVPのみ',
            kmin: (input) => `キル ${input} 以上`, ddmin: (input) => `与ダメ ${input} 以上`,
            unitSet: 'セット単位', unitGame: '試合単位', clear: '条件をクリア', remove: '外す',
            count: (n, all, game) => `<b>${n}</b>${game ? '試合' : '件'} / 全${all}${game ? '試合' : '件'}`,
            capped: (n) => `バトルが多いため、直近の${n}試合が対象です。`,
            mate: '味方: ', opp: '敵: ', search: (label) => `${label}を検索`, notFound: '見つかりません',
            dims: { brawler: 'キャラ', mode: 'モード', map: 'マップ', fmt: '形式', mate: '味方のプレイヤー', tbr: '味方のキャラ', obr: '敵のキャラ', dow: '曜日', hour: '時間帯', ban: '自分のBAN' },
            fmt: { solo: 'ソロ', duo: 'デュオ', trio: 'トリオ', other: 'その他' }, fmtVs: (n) => `${n}対${n}`,
            summary: 'サマリー', lastBattles: (n, game) => `過去<b>${n}</b>${game ? '試合' : '件'}のバトル`,
            winRate: '勝率', w: '勝', d: '分', l: '敗', wdl: (w, d, l) => `${w}勝 ${d ? d + '分 ' : ''}${l}敗`,
            maxStreak: '最大連勝', loseStreak: '連敗', streakW: '連勝', streakL: '連敗', nowW: (n) => `現在${n}連勝中`, nowL: (n) => `現在${n}連敗中`,
            mvpRate: '★ MVP率', times: '回', avgKD: `平均キル${SL}デス`, killsUnit: 'キル', deathsUnit: 'デス', kdRatio: 'キルデス比',
            avgDamage: '平均与ダメージ', damageTaken: '被ダメージ', avgTime: '平均バトル時間', totalTime: '合計', hoursUnit: '時間', minutesUnit: '分',
            avgHeal: '平均回復', damagePerMin: '1分あたりの与ダメージ',
            noteHead: 'キル / デス・与ダメージ・バトル時間は、', noteOld: '2026年11月アップデート以降の', noteMode: 'バトルロイヤルなど一部のモードを除く', noteTail: (n) => `バトル（${n}試合）が対象です。`, noteJoin: '、',
            pivot: '内訳', pivotCap: '見出しを押すと並べ替え・行を押すと条件に追加', groupBy: '切り口', minCount: (input) => `${input} 回以上`,
            cols: { n: '回数', wr: '勝率', sp: 'MVP率', k: 'キル', d: 'デス', kd: 'キルデス比', dd: '与ダメ', he: '回復', dt: '被ダメ', du: '時間', tc: 'トロフィー' },
            none: '該当なし', showAll: (n) => `すべて表示（全${n}件）`, showTop: '上位だけ表示',
            battles: 'バトル', battlesCap: (n, game) => (game ? `${n}試合` : `${n}件・セットの行を押して試合ごとのデータを閲覧`),
            bt: { t: '日時', r: '結果', b: 'キャラ', tc: `トロフィー / ランク`, ty: '種別', m: 'モード', e: 'マップ', k: 'キル', d: 'デス', dd: '与ダメ', he: '回復', dt: '被ダメ', du: '時間' },
            res: ['勝', '分', '敗'], place: (n) => `${n}位`, gameNo: (n) => `${n}戦目`, prev: '前へ', next: '次へ',
            noMatch: '条件に合うバトルがありません。条件を減らしてみてください。', empty: 'バトルがありません',
            loadError: 'データを読み込めませんでした。', retry: '再試行', rate: '短い間に何度も読み込まれたため、いまは表示できません。少し待ってからもう一度お試しください。',
            needLogin: 'ログインの有効期限が切れました。もう一度ログインすると表示されます。',
            heroTitle: '詳細分析', heroLead: (name, n) => `キャラ・モード・マップ・一緒にプレイした人などを自由に組み合わせて、<b>${name}</b>のバトル ${n}試合を掘り下げられます。ログインすると、すべての機能が使えるようになります。`,
            register: 'アカウントを作成', login: 'ログイン', free: 'アカウントの作成は無料です', heroMore: '続きは、ログインすると表示されます。',
            featFilter: '条件を組み合わせて絞り込み', featFilterLead: 'キャラやモード、マップは複数選べます。「この味方と組んだとき」「敵にこのキャラがいたとき」といった条件も重ねられます。', demo30: '30日',
            featPivot: 'キャラ・モード・マップごとの詳しい成績', featPivotLead: '勝率だけでなく、キル・デス・与ダメージ・回復・被ダメージ・MVP率まで。好きな項目で並べ替えられます。', ctaPivot: (n) => `ログインして全${n}キャラを見る`,
            featOpp: '敵のキャラとの相性', featOppLead: '敵にどのキャラがいると勝ちにくいのか、味方に誰がいると勝ちやすいのかを確かめられます。', ctaOpp: 'ログインして相性を見る',
            featTable: 'バトルを表で見る', featTableLead: '条件に合うバトルを表にして、与ダメージの多い順・バトル時間の長い順などに並べ替えられます。', ctaTable: 'ログインして表で見る',
        },
        en: {
            dow: ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'], dowName: (d) => d, hourName: (h) => `${h}:00`,
            types: ['Trophy', 'Ranked', 'Friendly', 'Other'], other: 'Other', unknownMap: 'Unknown map', typeOther: '–',
            dateTo: '–', seasonPick: 'Pick a season',
            durRange: (a, b) => `Battle time ${a}–${b} s`, rkmax: (input) => `Place ≤ ${input}`, trRange: (a, b) => `Trophies ${a}–${b}`, ban: 'Ban: ',
            highlights: 'Highlights', highlightsCap: 'Among those with enough battles',
            hl: { brawler: 'Best brawler', mapGood: 'Best map', mapBad: 'Toughest map', oppGood: 'Easiest enemy brawler', oppBad: 'Toughest enemy brawler', mate: 'Best teammate' },
            hlSub: (rate, w, l) => `Win rate ${rate} (${w}W ${l}L)`,
            trend: 'Trend', metrics: { wr: 'Win rate', kd: 'K/D', dd: 'Damage', dt: 'Taken', he: 'Healing', du: 'Battle time', tc: 'Trophies' },
            day: 'Day', week: 'Week', chart: 'Chart', table: 'Table', trendNeed: 'Not enough battles to show a trend', from: '–',
            trendCount: (n, game, w, l) => `${n} ${game ? 'games' : 'battles'}, ${w}W ${l}L`, colCount: 'Battles', colWL: 'W / L', wl: (w, l) => `${w}W ${l}L`,
            hist: { dd: 'Damage', k: 'Kills', d: 'Deaths', dt: 'Taken', du: 'Battle time', rk: 'Place' },
            histTitle: { dd: 'Damage dealt', k: 'Kills', d: 'Deaths', dt: 'Damage taken', du: 'Battle time', rk: 'Place' },
            histCap: 'Distribution per game', histNeed: 'Not enough battles to show a distribution', histTip: (range, n, p) => `${range}\n${n} games (${p}%)`, avg: 'Average', median: 'Median', max: 'Max', games: (n) => `${n} games`,
            hours: 'Play times', tz: 'Time zone: ', tzJapan: 'Japan Standard Time', tzDevice: 'this device', hourMark: 'h', battleCount: 'Battles',
            heatCell: (dow, h, n, rate) => `${dow} ${h}:00\n${n} battles, win rate ${rate}`, few: 'Fewer', many: 'More', rateHigh: 'Win rate: high', rateLow: 'low (no color under 3 battles)',
            mostActive: 'Most active:', mostActiveAt: (dow, h, n) => `<b>${dow} ${h}:00</b> (${n} battles)`, bestRate: 'Best win rate:', bestRateAt: (from, to, rate) => `<b>${from}:00–${to}:00</b> (${rate}%)`,
            viewCard: 'Cards', viewTable: 'Table', openResults: 'Open results', cardsLoading: 'Loading battles...', cardsCap: (n, game) => `${n} ${game ? 'games' : 'battles'}`,
            featHl: 'Highlights', featHlLead: 'Your best and toughest brawlers and maps update the moment you change a filter.', ctaHl: 'Log in to see them all',
            featTrend: 'Trends and distributions', featTrendLead: 'Follow how your win rate, K/D and damage have changed by day or by week.', ctaTrend: 'Log in to see trends',
            fType: 'Type', fPeriod: 'Period', fPicks: 'Filters', fMore: 'More filters', fResult: 'Result', fFormat: 'Format', fDow: 'Day', fValues: 'Values', fUnit: 'Count',
            periods: [['all', 'All'], [7, '7d'], [30, '30d'], [90, '90d'], [365, '1y']],
            results: ['Win', 'Draw', 'Loss'], mvpOnly: '★ MVP only',
            kmin: (input) => `Kills ≥ ${input}`, ddmin: (input) => `Damage ≥ ${input}`,
            unitSet: 'By set', unitGame: 'By game', clear: 'Clear filters', remove: 'Remove',
            count: (n, all, game) => `<b>${n}</b> of ${all} ${game ? 'games' : 'battles'}`,
            capped: (n) => `This player has many battles, so the latest ${n} games are used.`,
            mate: 'With: ', opp: 'Vs: ', search: (label) => `Search ${label.toLowerCase()}`, notFound: 'Nothing found',
            dims: { brawler: 'Brawler', mode: 'Mode', map: 'Map', fmt: 'Format', mate: 'Teammate', tbr: 'Ally brawler', obr: 'Enemy brawler', dow: 'Day', hour: 'Hour', ban: 'Your ban' },
            fmt: { solo: 'Solo', duo: 'Duo', trio: 'Trio', other: 'Other' }, fmtVs: (n) => `${n}v${n}`,
            summary: 'Summary', lastBattles: (n, game) => `Last <b>${n}</b> ${game ? 'games' : 'battles'}`,
            winRate: 'Win rate', w: 'W', d: 'D', l: 'L', wdl: (w, d, l) => `${w}W ${d ? d + 'D ' : ''}${l}L`,
            maxStreak: 'Best win', loseStreak: 'loss streak', streakW: 'W', streakL: 'L', nowW: (n) => `On a ${n}-win streak`, nowL: (n) => `On a ${n}-loss streak`,
            mvpRate: '★ MVP rate', times: '', avgKD: `Avg. kills${SL}deaths`, killsUnit: '', deathsUnit: '', kdRatio: 'K/D ratio',
            avgDamage: 'Avg. damage dealt', damageTaken: 'Taken', avgTime: 'Avg. battle time', totalTime: 'Total', hoursUnit: 'h', minutesUnit: 'm',
            avgHeal: 'Avg. healing', damagePerMin: 'Damage per minute',
            noteHead: 'Kills / deaths, damage and battle time are based on ', noteOld: 'battles since the November 2026 update', noteMode: 'battles in modes that have stats (Showdown and a few other modes are not included)', noteBoth: 'battles since the November 2026 update in modes that have stats (Showdown and a few other modes are not included)', noteTail: (n) => `: ${n} games.`, noteJoin: '',
            pivot: 'Breakdown', pivotCap: 'Tap a header to sort, or a row to add it as a filter', groupBy: 'Group by', minCount: (input) => `Min. ${input}`,
            cols: { n: 'Battles', wr: 'Win rate', sp: 'MVP rate', k: 'Kills', d: 'Deaths', kd: 'K/D', dd: 'Damage', he: 'Healing', dt: 'Taken', du: 'Time', tc: 'Trophies' },
            none: 'Nothing here', showAll: (n) => `Show all ${n}`, showTop: 'Show top only',
            battles: 'Battles', battlesCap: (n, game) => (game ? `${n} games` : `${n} battles · tap a set row to see each game`),
            bt: { t: 'Date', r: 'Result', b: 'Brawler', tc: 'Trophies / Rank', ty: 'Type', m: 'Mode', e: 'Map', k: 'Kills', d: 'Deaths', dd: 'Damage', he: 'Healing', dt: 'Taken', du: 'Time' },
            res: ['W', 'D', 'L'], place: (n) => `#${n}`, gameNo: (n) => `Game ${n}`, prev: 'Prev', next: 'Next',
            noMatch: 'No battles match these filters. Try removing some.', empty: 'No battles',
            loadError: 'The data could not be loaded.', retry: 'Retry', rate: 'This was loaded too many times in a short while. Please wait a moment and try again.',
            needLogin: 'Your login has expired. Log in again to see this.',
            heroTitle: 'Analysis', heroLead: (name, n) => `Combine brawlers, modes, maps, teammates and more to dig into <b>${name}</b>'s ${n} games. Log in to use everything.`,
            register: 'Create an account', login: 'Log in', free: 'Creating an account is free', heroMore: 'Log in to see the rest.',
            featFilter: 'Combine filters', featFilterLead: 'Pick several brawlers, modes and maps at once, and add conditions such as "when teamed with this player" or "when the enemy had this brawler".', demo30: '30d',
            featPivot: 'Detailed stats by brawler, mode and map', featPivotLead: 'Not just win rate: kills, deaths, damage, healing, damage taken and MVP rate. Sort by any column.', ctaPivot: (n) => `Log in to see all ${n} brawlers`,
            featOpp: 'Matchups against enemy brawlers', featOppLead: 'See which enemy brawlers are hard to beat, and which allies help you win.', ctaOpp: 'Log in to see matchups',
            featTable: 'Battles as a table', featTableLead: 'List the battles that match your filters and sort them by damage, battle time and more.', ctaTable: 'Log in to see the table',
        },
    };

    // いま表示している詳細分析（選択のポップアップを閉じる・画面幅が変わったときの詰め直しに使う）
    let current = null;
    document.addEventListener('pointerdown', (e) => {
        if (current && current.pop && !current.pop.el.contains(e.target) && !(e.target.closest && e.target.closest('[data-pick]'))) current.closePop();
    });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && current) current.closePop(); });
    window.addEventListener('resize', () => { if (current && document.body.contains(current.root)) current.fitWbars(); });

    window.initBattleAnalysis = function (root) {
        if (!root || root.dataset.bxaReady) return;
        root.dataset.bxaReady = '1';
        if (current) current.closePop();
        const lang = root.dataset.lang === 'ja' ? 'ja' : 'en';
        const T = TEXT[lang];
        const IMG = root.dataset.imgBase;
        const STATIC = IMG.replace(/\/images$/, '');
        const topSlot = root.querySelector('[data-bxa-slot="top"]'), bottomSlot = root.querySelector('[data-bxa-slot="bottom"]');
        const filtersEl = document.getElementById('battleAnalysisFilters');
        if (filtersEl) filtersEl.dataset.lang = lang;
        let names = { brawlers: {}, modes: {}, maps: {} }, players = [], seasons = [], statsSince = 0, capped = 0;
        let ROWS = [], setCount = 0;
        let after = [];
        const charts = [];
        const cardCache = new Map();  // カード表示: 読み込んだページの HTML
        let cardLoadId = 0;
        const me = { root, pop: null, closePop, fitWbars };
        current = me;

        const prefs = readJson(PREF_KEY, {});
        const newF = () => {
            const F = { types: new Set(), period: 'all', from: '', to: '', season: '', brawler: new Set(), mode: new Set(), map: new Set(), mate: new Set(), tbr: new Set(), obr: new Set(), ban: new Set(),
                res: new Set(), fmt: new Set(), dow: new Set(), mvp: false, unit: 'set' };
            for (const key of NUM_KEYS) F[key] = null;
            return F;
        };
        const S = {
            F: newF(), moreOpen: false, curRows: [],
            pv: { dim: prefs.pvDim || 'brawler', sort: 'n', dir: -1, min: 3, more: false },
            bt: { sort: 't', dir: -1, page: 0, open: new Set(), view: prefs.aBtView === 'card' ? 'card' : 'table' },
            trend: Object.assign({ metric: 'wr', bucket: null, view: 'chart' }, prefs.aTrend || {}),
            hist: prefs.aHist || 'dd', heatMode: prefs.heatMode === 'wr' ? 'wr' : 'n',
        };
        const savePref = (values) => writeJson(PREF_KEY, Object.assign(readJson(PREF_KEY, {}), values));

        // ---------- 小物 ----------
        const bname = (id) => (names.brawlers[id] ? names.brawlers[id][0] : String(id));
        const bimg = (id) => `${IMG}/brawler_borderless_portraits/${id}.png`;
        const mname = (id) => (id && names.modes[id] ? names.modes[id][0] : T.other);
        const micon = (id) => (id && names.modes[id] && names.modes[id][2] ? STATIC + names.modes[id][2] : null);
        const mapname = (id) => (names.maps[id] ? names.maps[id][0] : T.unknownMap);
        const mapMode = (id) => (names.maps[id] ? names.maps[id][1] : 0);
        const fmtName = (key) => (T.fmt[key] ? T.fmt[key] : T.fmtVs(key.charAt(0)));
        const ymdFull = (t) => { const d = new Date(t * 1000); return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()} ${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`; };
        const hm = (t) => { const d = new Date(t * 1000); return `${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`; };
        function short(n) {
            if (n == null || isNaN(n)) return '—';
            const a = Math.abs(n);
            if (a < 10000) return nf(n);
            if (lang === 'ja') { const m = n / 10000; return (a < 100000 ? m.toFixed(2) : a < 1000000 ? m.toFixed(1) : String(Math.round(m))) + '<small>万</small>'; }
            return a < 1000000 ? (n / 1000).toFixed(a < 100000 ? 1 : 0) + '<small>k</small>' : (n / 1000000).toFixed(2) + '<small>M</small>';
        }
        const shortText = (n) => short(n).replace(/<[^>]+>/g, '');
        const tipAttr = (text) => ` data-tip="${esc(text)}" tabindex="0"`;
        const seg = (name, value, opts) => `<span class="bx-seg" data-seg="${name}">${opts.map(([v, l]) => `<button type="button" data-v="${v}"${String(v) === String(value) ? ' class="is-on"' : ''}>${l}</button>`).join('')}</span>`;
        const parseId = (raw) => (raw !== '' && !isNaN(Number(raw)) ? Number(raw) : raw);
        function wrCell(a) {
            const v = wr(a);
            return `<span class="bx-wr"${tipAttr(T.wdl(a.w, a.d, a.l))}><span class="bx-split${v == null ? ' is-none' : ''}"><i style="width:${v == null ? 0 : v}%"></i></span><b>${pc(v)}</b></span>`;
        }
        const closedSet = () => new Set(readJson(CLOSED_KEY, []));
        // カード。foldable なら見出しで折りたためる（開閉はカードの種類ごとに端末に記憶する。サマリーはほかのタブと共通）
        function card(key, cls, title, cap, tools, body, foldable) {
            const closed = foldable && closedSet().has(key);
            const head = foldable
                ? `<button type="button" class="bx-card__toggle" data-act="fold" aria-expanded="${!closed}"><h3 class="bx-card__title">${title}</h3><span class="bx-chev"></span></button>`
                : `<h3 class="bx-card__title">${title}</h3>`;
            return `<section class="bx-card ${cls}${closed ? ' is-closed' : ''}" data-card="${key}"><div class="bx-card__head">${head}
                ${cap ? `<span class="bx-card__cap">${cap}</span>` : ''}${tools ? `<div class="bx-card__tools">${tools}</div>` : ''}</div><div class="bx-card__fold"><div>${body}</div></div></section>`;
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
        const runAfter = () => { const run = after; after = []; run.forEach((f) => f()); };
        function chartTheme() {
            const cs = getComputedStyle(topSlot);
            const v = (n) => cs.getPropertyValue(n).trim();
            return { card: v('--bx-card') || '#fff', mut: v('--color-777') || '#777', grid: v('--color-eee') || '#eee', accent: v('--bx-accent') || '#5b7fd6', ink: v('--color-111') };
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
        const destroyCharts = (within) => { for (let i = charts.length - 1; i >= 0; i--) if (!within || within.contains(charts[i].canvas)) { charts[i].destroy(); charts.splice(i, 1); } };
        const dayLabel = (day) => { const d = new Date(day * 86400000); return `${d.getUTCMonth() + 1}/${d.getUTCDate()}`; };
        const dayIndex = (text) => { const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text || ''); return m ? Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])) / 86400000 : null; };

        // ---------- データ ----------
        function fmtOf(r) {
            if (r.nt === 2) return r.ts >= 1 && r.ts <= 5 ? `${r.ts}v${r.ts}` : 'other';
            if (r.nt > 2) return r.ts === 1 ? 'solo' : r.ts === 2 ? 'duo' : r.ts === 3 ? 'trio' : 'other';
            return 'other';
        }
        function buildRows(P) {
            const c = P.cols, n = P.n, rows = new Array(n);
            for (let i = 0; i < n; i++) {
                const d = new Date(c.t[i] * 1000);
                const r = {
                    t: c.t[i], ty: c.ty[i], m: c.m[i] || 0, e: c.e[i] || null, r: c.r[i], sr: c.sr[i], sid: c.sid[i], rk: c.rk[i], sp: c.sp[i], mv: c.mv[i], b: c.b[i], bs: (c.bs && c.bs[i]) || (c.b[i] ? [c.b[i]] : []),
                    tr: c.tr[i], tc: c.tc[i], rs: c.rs[i], ts: c.ts[i], nt: c.nt[i], du: c.du[i], k: c.k[i], d: c.d[i], dd: c.dd[i], he: c.he[i], dt: c.dt[i], bn: c.bn[i],
                    tm: c.tm[i], tb: c.tb[i], ob: c.ob[i],
                    day: Math.floor((c.t[i] - d.getTimezoneOffset() * 60) / 86400), dow: d.getDay(), hr: d.getHours(),
                };
                r.fmt = fmtOf(r);
                rows[i] = r;
            }
            // セットのMVP（最後の試合にだけ付く）を、セットの全試合から引けるようにする
            const starred = new Set();
            for (const r of rows) if (r.sp) starred.add(r.sid);
            // ガチバトル: セットの終わりのポイントと、1つ前のセットのポイント（記録があるときだけ）
            const setScore = new Map();
            for (const r of rows) if (r.ty === 1 && r.rs > 0) setScore.set(r.sid, r.rs);
            let prevSid = null, prevScore = null, before = null;
            for (const r of rows) {
                r.ssp = starred.has(r.sid) ? 1 : 0;
                if (r.ty !== 1) continue;
                if (r.sid !== prevSid) { before = prevSid != null ? prevScore : null; prevSid = r.sid; prevScore = setScore.get(r.sid) || null; }
                r.srs = setScore.get(r.sid) || null;
                r.srsPrev = before;
            }
            return rows;
        }
        const markHeads = (rows) => { for (let i = 0; i < rows.length; i++) rows[i].head = i === 0 || rows[i - 1].sid !== rows[i].sid; return rows; };
        const A = () => { const a = {}; for (const key of AGG_KEYS) a[key] = 0; return a; };
        // 1試合ぶんを足す。勝敗とMVPは1件で1回（セット単位ならセットの初めの行で数える）、スタッツ・バトル時間・トロフィーは試合ごと
        function add(a, r, unit) {
            a.g++;
            if (unit === 'game' || r.head) {
                a.n++;
                const res = unit === 'game' ? r.r : r.sr;
                if (res === 0) a.w++; else if (res === 2) a.l++; else a.d++;
                // MVPはセットに1回だけ選ばれる。MVPが選ばれないバトル（バトルロイヤル系・デュエル）は分母に入れない
                if (r.mv) { a.mg++; if (r.ssp) a.sp++; }
            }
            a.tc += r.tc || 0;
            if (r.k != null) { a.sn++; a.k += r.k; a.dth += r.d || 0; a.dd += r.dd || 0; a.he += r.he || 0; a.dt += r.dt || 0; }
            if (r.du > 0) { a.dn++; a.du += r.du; }
        }
        const total = (rows, unit) => { const a = A(); for (const r of rows) add(a, r, unit); return a; };
        function groupBy(rows, keyFn, unit) {
            const m = new Map();
            for (const r of rows) {
                let ks = keyFn(r);
                if (ks == null) continue;
                if (!Array.isArray(ks)) ks = [ks];
                for (const k of ks) { if (k == null) continue; let a = m.get(k); if (!a) { a = A(); m.set(k, a); } add(a, r, unit); }
            }
            return m;
        }
        function streaks(rows, unit) {
            let bw = 0, bl = 0, cw = 0, cl = 0;
            for (const r of rows) {
                if (unit !== 'game' && !r.head) continue;
                const res = unit === 'game' ? r.r : r.sr;
                if (res === 0) { cw++; cl = 0; } else if (res === 2) { cl++; cw = 0; } else { cw = 0; cl = 0; }
                if (cw > bw) bw = cw;
                if (cl > bl) bl = cl;
            }
            return { bw, bl, cw, cl };
        }

        // ---------- 絞り込み ----------
        const DIMS = {
            brawler: { key: (r) => r.bs, name: bname, img: bimg, f: 'brawler' },  // デュエルは使ったキャラそれぞれに数える
            mode: { key: (r) => r.m, name: mname, img: micon, f: 'mode' },
            map: { key: (r) => r.e, name: mapname, img: (id) => micon(mapMode(id)), f: 'map' },
            fmt: { key: (r) => r.fmt, name: fmtName, f: 'fmt' },
            mate: { key: (r) => r.tm, name: (i) => players[i] || '', f: 'mate' },
            tbr: { key: (r) => r.tb, name: bname, img: bimg, f: 'tbr' },
            obr: { key: (r) => r.ob, name: bname, img: bimg, f: 'obr' },
            dow: { key: (r) => r.dow, name: (d) => T.dowName(T.dow[d]), f: 'dow' },
            hour: { key: (r) => r.hr, name: T.hourName },
            ban: { key: (r) => r.bn, name: bname, img: bimg, f: 'ban' },
        };
        const PICKS = ['brawler', 'mode', 'map', 'mate', 'tbr', 'obr', 'ban'];
        // 種別と期間（プルダウンの候補は、ここまで絞ったものから出す）。日付を指定しているときは、この端末の日付で区切る
        function typeRows() {
            const F = S.F, dayFrom = dayIndex(F.from), dayTo = dayIndex(F.to), custom = dayFrom != null || dayTo != null;
            const from = custom || F.period === 'all' ? null : Date.now() / 1000 - Number(F.period) * 86400;
            return ROWS.filter((r) => (!F.types.size || F.types.has(r.ty)) && (from == null || r.t >= from) && (dayFrom == null || r.day >= dayFrom) && (dayTo == null || r.day <= dayTo));
        }
        function analysisRows() {
            const F = S.F, game = F.unit === 'game';
            const has = (set, arr) => { for (const x of arr) if (set.has(x)) return true; return false; };
            return markHeads(typeRows().filter((r) => (!F.brawler.size || has(F.brawler, r.bs)) && (!F.mode.size || F.mode.has(r.m)) && (!F.map.size || F.map.has(r.e))
                && (!F.res.size || F.res.has(game ? r.r : r.sr)) && (!F.mvp || r.ssp) && (!F.mate.size || has(F.mate, r.tm)) && (!F.tbr.size || has(F.tbr, r.tb)) && (!F.obr.size || has(F.obr, r.ob))
                && (!F.fmt.size || F.fmt.has(r.fmt)) && (!F.dow.size || F.dow.has(r.dow)) && (F.kmin == null || (r.k != null && r.k >= F.kmin)) && (F.ddmin == null || (r.dd != null && r.dd >= F.ddmin))
                && (!F.ban.size || F.ban.has(r.bn))
                && ((F.dumin == null && F.dumax == null) || (r.du > 0 && (F.dumin == null || r.du >= F.dumin) && (F.dumax == null || r.du <= F.dumax)))
                && (F.rkmax == null || (r.rk != null && r.rk <= F.rkmax))
                // トロフィーは、キャラのトロフィーが入っているバトル（ガチバトル・フレンドバトル以外）だけが対象
                && ((F.trmin == null && F.trmax == null) || (r.ty !== 1 && r.ty !== 2 && r.tr != null && r.tr >= 0 && (F.trmin == null || r.tr >= F.trmin) && (F.trmax == null || r.tr <= F.trmax)))));
        }
        function renderFilters(rows) {
            if (!filtersEl) return;
            const F = S.F, game = F.unit === 'game';
            const chip = (group, v, label, on) => `<button type="button" class="bx-chip${on ? ' is-on' : ''}" data-fchip="${group}:${v}">${label}</button>`;
            const selected = [];
            for (const dim of PICKS) for (const id of F[dim]) {
                const d = DIMS[dim], img = d.img && d.img(id);
                selected.push(`<span class="bx-chip bx-chip--sel">${img ? `<img src="${img}" alt="" onerror="this.remove()">` : ''}${dim === 'tbr' ? T.mate : dim === 'obr' ? T.opp : dim === 'ban' ? T.ban : ''}${esc(d.name(id))}<button type="button" data-fdel="${dim}:${esc(id)}" aria-label="${T.remove}">×</button></span>`);
            }
            const custom = !!(F.from || F.to);
            const any = selected.length || F.types.size || F.res.size || F.fmt.size || F.dow.size || F.mvp || NUM_KEYS.some((key) => F[key] != null) || F.period !== 'all' || custom;
            const fmts = [...new Set(typeRows().map((r) => r.fmt))];
            for (const k of F.fmt) if (!fmts.includes(k)) fmts.push(k);
            const numInput = (key, step) => `<input type="number" min="0"${step ? ` step="${step}"` : ''} class="bxf-num" data-fnum="${key}" value="${F[key] == null ? '' : F[key]}" placeholder="${key.endsWith('max') ? '' : '0'}" inputmode="numeric">`;
            const dateInput = (key) => `<input type="date" class="bxf-date" data-fdate="${key}" value="${esc(F[key])}">`;
            const seasonSelect = seasons.length ? `<select class="bxf-select" data-fseason><option value="">${T.seasonPick}</option>${seasons.map(([number, label]) => `<option value="${number}"${String(F.season) === String(number) ? ' selected' : ''}>${esc(label)}</option>`).join('')}</select>` : '';
            const shown = rows.filter((r) => game || r.head).length;
            // 描き直しで入力中の欄からフォーカスが外れないよう、どの欄にいたかを覚えておく
            const active = document.activeElement && filtersEl.contains(document.activeElement) ? document.activeElement : null;
            const focusKey = active && (active.dataset.fnum ? `[data-fnum="${active.dataset.fnum}"]` : active.dataset.fdate ? `[data-fdate="${active.dataset.fdate}"]` : null);
            filtersEl.innerHTML = `
                <div class="bxf-row"><span class="bxf-label">${T.fType}</span>${T.types.map((l, i) => chip('types', i, l, F.types.has(i))).join('')}</div>
                <div class="bxf-row"><span class="bxf-label">${T.fPeriod}</span>${T.periods.map(([v, l]) => chip('period', v, l, !custom && String(F.period) === String(v))).join('')}
                    <span class="bxf-dates">${dateInput('from')}<span>${T.dateTo}</span>${dateInput('to')}</span>${seasonSelect}</div>
                <div class="bxf-row"><span class="bxf-label">${T.fPicks}</span>${PICKS.map((dim) => `<button type="button" class="bx-chip bx-chip--pick" data-pick="${dim}">${T.dims[dim]}${F[dim].size ? `<b>${F[dim].size}</b>` : ''}</button>`).join('')}
                    <button type="button" class="bx-chip${S.moreOpen ? ' is-on' : ''}" data-act="fmore" aria-expanded="${S.moreOpen}">${T.fMore}</button></div>
                <div class="bxf-more${S.moreOpen ? ' is-open' : ''}"><div>
                    <div class="bxf-row"><span class="bxf-label">${T.fResult}</span>${T.results.map((l, v) => chip('res', v, l, F.res.has(v))).join('')}${chip('mvp', 1, T.mvpOnly, F.mvp)}</div>
                    <div class="bxf-row"><span class="bxf-label">${T.fFormat}</span>${fmts.map((k) => chip('fmt', k, fmtName(k), F.fmt.has(k))).join('')}</div>
                    <div class="bxf-row"><span class="bxf-label">${T.fDow}</span>${[1, 2, 3, 4, 5, 6, 0].map((d) => chip('dow', d, T.dow[d], F.dow.has(d))).join('')}</div>
                    <div class="bxf-row"><span class="bxf-label">${T.fValues}</span><label class="bxf-numlabel">${T.kmin(numInput('kmin'))}</label><label class="bxf-numlabel">${T.ddmin(numInput('ddmin', 1000))}</label>
                        <span class="bxf-numlabel">${T.durRange(numInput('dumin', 10), numInput('dumax', 10))}</span></div>
                    <div class="bxf-row"><span class="bxf-label"></span><label class="bxf-numlabel">${T.rkmax(numInput('rkmax'))}</label><span class="bxf-numlabel">${T.trRange(numInput('trmin', 50), numInput('trmax', 50))}</span></div>
                </div></div>
                ${selected.length ? `<div class="bxf-row">${selected.join('')}</div>` : ''}
                <div class="bxf-row bxf-foot"><span class="bxf-label">${T.fUnit}</span>${seg('unit', F.unit, [['set', T.unitSet], ['game', T.unitGame]])}
                    ${any ? `<button type="button" class="bxf-clear" data-act="fclear">${T.clear}</button>` : ''}
                    <span class="bxf-count">${T.count(nf(shown), nf(game ? ROWS.length : setCount), game)}</span></div>
                ${capped ? `<p class="bxf-note">${T.capped(nf(capped))}</p>` : ''}`;
            const again = focusKey && filtersEl.querySelector(focusKey);
            if (again) again.focus({ preventScroll: true });
        }

        // ---------- 条件を URL に入れる（共有・再読み込みで同じ条件に戻れるように） ----------
        const URL_SETS = { ty: 'types', br: 'brawler', mo: 'mode', mp: 'map', tb: 'tbr', ob: 'obr', bn: 'ban', rs: 'res', fm: 'fmt', dw: 'dow' };
        function writeUrl() {
            const F = S.F, q = new URLSearchParams();
            q.set('tab', 'analysis');
            for (const [param, key] of Object.entries(URL_SETS)) if (F[key].size) q.set(param, [...F[key]].join('.'));
            // 味方のプレイヤーは名前で入れる（番号は読み込みのたびに変わりうるため）
            for (const index of F.mate) if (players[index]) q.append('mt', players[index]);
            if (F.from) q.set('df', F.from);
            if (F.to) q.set('dt', F.to);
            if (!F.from && !F.to && F.period !== 'all') q.set('pd', F.period);
            if (F.mvp) q.set('mvp', '1');
            for (const key of NUM_KEYS) if (F[key] != null) q.set(key, F[key]);
            if (F.unit === 'game') q.set('un', 'game');
            const url = `${location.pathname}?${q.toString()}`;
            if (url === location.pathname + location.search) return;
            try { history.replaceState(history.state, '', url); } catch (e) { /* URL を書き換えられなくても、絞り込みはできる */ }
        }
        function readUrl() {
            const q = new URLSearchParams(location.search), F = S.F;
            const limits = { ty: 3, rs: 2, dw: 6 };
            for (const [param, key] of Object.entries(URL_SETS)) {
                for (const raw of (q.get(param) || '').split('.')) {
                    if (!raw) continue;
                    if (param === 'fm') { if (/^[a-z0-9]{2,5}$/.test(raw)) F.fmt.add(raw); continue; }
                    const value = Number(raw);
                    if (!Number.isInteger(value) || value < 0 || (limits[param] != null && value > limits[param])) continue;
                    F[key].add(value);
                }
            }
            for (const name of q.getAll('mt')) { const index = players.indexOf(name); if (index >= 0) F.mate.add(index); }
            if (dayIndex(q.get('df')) != null) F.from = q.get('df');
            if (dayIndex(q.get('dt')) != null) F.to = q.get('dt');
            const season = seasons.find((x) => x[2] === F.from && x[3] === F.to);
            if (season) F.season = season[0];
            if (!F.from && !F.to && ['7', '30', '90', '365'].includes(q.get('pd'))) F.period = q.get('pd');
            F.mvp = q.get('mvp') === '1';
            for (const key of NUM_KEYS) { const raw = q.get(key), value = Number(raw); if (raw != null && raw !== '' && isFinite(value) && value >= 0) F[key] = value; }
            if (q.get('un') === 'game') F.unit = 'game';
            // 詳細条件が入っているときは、初めから開いておく
            if (F.res.size || F.fmt.size || F.dow.size || F.mvp || NUM_KEYS.some((key) => F[key] != null)) S.moreOpen = true;
        }

        // ---------- 選択のポップアップ（キャラ・モード・マップ・味方など） ----------
        function closePop() { if (me.pop) { me.pop.el.remove(); me.pop = null; } }
        function placePop(el, btn) {
            const rect = btn.getBoundingClientRect();
            el.style.top = rect.bottom + window.scrollY + 5 + 'px';
            el.style.left = Math.max(10, Math.min(rect.left + window.scrollX, window.scrollX + document.documentElement.clientWidth - el.offsetWidth - 10)) + 'px';
        }
        function openPop(btn, dim) {
            closePop();
            const d = DIMS[dim], F = S.F;
            const counts = [...groupBy(typeRows(), d.key, 'game').entries()].sort((a, b) => b[1].g - a[1].g);
            const el = document.createElement('div');
            el.className = 'bx-pop';
            el.innerHTML = `<input type="search" placeholder="${esc(T.search(T.dims[dim]))}"><div class="bx-pop__grid"></div>`;
            document.body.appendChild(el);
            placePop(el, btn);
            const grid = el.querySelector('.bx-pop__grid'), input = el.querySelector('input');
            const draw = () => {
                const q = input.value.trim().toLowerCase();
                let html = '', lastMode = null;
                let list = counts.filter(([id]) => !q || String(d.name(id)).toLowerCase().includes(q));
                if (dim === 'map') list = list.slice().sort((a, b) => mapMode(a[0]) - mapMode(b[0]) || b[1].g - a[1].g);
                for (const [id, a] of list.slice(0, 300)) {
                    if (dim === 'map') { const mode = mapMode(id); if (mode !== lastMode) { html += `<div class="bx-pop__group">${esc(mname(mode))}</div>`; lastMode = mode; } }
                    const img = d.img && d.img(id);
                    html += `<button type="button" class="bx-opt${F[dim].has(id) ? ' is-on' : ''}" data-id="${esc(id)}">${img ? `<img src="${img}" alt="" loading="lazy" onerror="this.remove()">` : ''}<span>${esc(d.name(id))}</span><em>${nf(a.g)}</em></button>`;
                }
                grid.innerHTML = html || `<div class="bx-empty" style="grid-column:1/-1">${T.notFound}</div>`;
            };
            draw();
            input.addEventListener('input', draw);
            grid.addEventListener('click', (e) => {
                const o = e.target.closest('.bx-opt');
                if (!o) return;
                const id = parseId(o.dataset.id);
                if (F[dim].has(id)) F[dim].delete(id); else F[dim].add(id);
                o.classList.toggle('is-on');
                S.bt.page = 0;
                const keep = me.pop; me.pop = null; render(); me.pop = keep;  // 選び続けられるよう、ポップアップは開いたままにする
                const again = filtersEl && filtersEl.querySelector(`[data-pick="${dim}"]`);
                if (again) placePop(el, again);
            });
            me.pop = { el, dim };
            if (window.matchMedia('(hover: hover)').matches) input.focus();
        }

        // ---------- サマリー ----------
        function statsNote(rows, a) {
            if (!a.sn || a.sn >= a.g) return '';
            let old = false, mode = false;
            for (const r of rows) {
                if (r.k != null) continue;
                if (!r.mv) mode = true; else if (r.t < statsSince) old = true;
            }
            if (!old && !mode) return '';
            const who = lang === 'ja' ? (old ? T.noteOld : '') + (old && mode ? T.noteJoin : '') + (mode ? T.noteMode : '') : old && mode ? T.noteBoth : old ? T.noteOld : T.noteMode;
            return `<p class="bx-note">${T.noteHead}${who}${T.noteTail(nf(a.sn))}</p>`;
        }
        function cardSummary(rows) {
            const unit = S.F.unit, a = total(rows, unit), st = streaks(rows, unit), v = wr(a), tiles = [];
            const part = (cls, kind, n, unitText) => (n ? `<i class="${cls}" data-kind="${kind}" style="flex:${n}"><span><b>${nf(n)}</b>${unitText}</span></i>` : '');
            tiles.push(`<div class="bx-tile bx-tile--wr"><span class="bx-tile__label">${T.winRate}</span><div class="bx-wrline"><span class="bx-tile__value">${v == null ? '—' : v.toFixed(1)}<small>%</small></span></div>
                <div class="bx-wbar-out"></div><div class="bx-wbar">${part('w', 'win', a.w, T.w)}${part('d', 'draw', a.d, T.d)}${part('l', 'lose', a.l, T.l)}</div></div>`);
            after.push(fitWbars);
            tiles.push(`<div class="bx-tile"><span class="bx-tile__label"><span class="bx-nw"><img src="${IMG}/ui/win_streak.png" alt="">${T.maxStreak}</span>${SL}<span class="bx-nw"><img src="${IMG}/ui/win_streak_blue.png" alt="">${T.loseStreak}</span></span>
                <span class="bx-tile__value"><span class="bx-nw">${st.bw}<small>${T.streakW}</small></span> <span class="bx-nw">${SL}${st.bl}<small>${T.streakL}</small></span></span><span class="bx-tile__sub">${st.cw >= 2 ? T.nowW(st.cw) : st.cl >= 2 ? T.nowL(st.cl) : '&nbsp;'}</span></div>`);
            // MVP率は、MVPが選ばれるバトルだけを分母にする。1つも無ければ項目ごと出さない
            if (a.mg) tiles.push(`<div class="bx-tile"><span class="bx-tile__label">${T.mvpRate}</span><span class="bx-tile__value">${(a.sp / a.mg * 100).toFixed(1)}<small>%</small></span><span class="bx-tile__sub">${nf(a.sp)}${T.times}</span></div>`);
            if (a.sn) {
                tiles.push(`<div class="bx-tile"><span class="bx-tile__label">${T.avgKD}</span><span class="bx-tile__value"><span class="bx-nw">${f1(a.k / a.sn)}<small>${T.killsUnit}</small></span> <span class="bx-nw">${SL}${f1(a.dth / a.sn)}<small>${T.deathsUnit}</small></span></span><span class="bx-tile__sub">${T.kdRatio} ${a.dth ? f2(a.k / a.dth) : '—'}</span></div>`);
                tiles.push(`<div class="bx-tile"><span class="bx-tile__label">${T.avgDamage}</span><span class="bx-tile__value">${short(a.dd / a.sn)}</span><span class="bx-tile__sub">${T.damageTaken} ${shortText(a.dt / a.sn)}</span></div>`);
            }
            if (a.dn) tiles.push(`<div class="bx-tile"><span class="bx-tile__label">${T.avgTime}</span><span class="bx-tile__value">${dur(a.du / a.dn)}</span><span class="bx-tile__sub">${T.totalTime} ${a.du >= 3600 ? nf(a.du / 3600) + T.hoursUnit : nf(a.du / 60) + T.minutesUnit}</span></div>`);
            if (a.sn) {
                tiles.push(`<div class="bx-tile"><span class="bx-tile__label">${T.avgHeal}</span><span class="bx-tile__value">${short(a.he / a.sn)}</span><span class="bx-tile__sub">&nbsp;</span></div>`);
                tiles.push(`<div class="bx-tile"><span class="bx-tile__label">${T.damagePerMin}</span><span class="bx-tile__value">${a.du ? short(a.dd / (a.du / 60)) : '—'}</span><span class="bx-tile__sub">&nbsp;</span></div>`);
            }
            return card('summary', 'bx-span2', T.summary, T.lastBattles(nf(a.n), unit === 'game'), '', `<div class="bx-tiles bx-tiles--dense">${tiles.join('')}</div>${statsNote(rows, a)}`, true);
        }

        // ---------- ハイライト ----------
        const HL_DIM = { brawler: 'brawler', mapGood: 'map', mapBad: 'map', oppGood: 'obr', oppBad: 'obr', mate: 'mate' };
        function hlItem(kind, name, img, w, l) {
            return `<div class="bx-hlitem">${img ? `<img src="${img}" alt="" loading="lazy" onerror="this.remove()">` : ''}<div><span class="bx-hlitem__label">${T.hl[kind]}</span><span class="bx-hlitem__name">${esc(name)}</span><span class="bx-hlitem__sub">${T.hlSub(pc(w + l ? w / (w + l) * 100 : null), nf(w), nf(l))}</span></div></div>`;
        }
        function cardHighlights(rows) {
            const unit = S.F.unit, F = S.F, minN = Math.max(5, Math.round(rows.length * 0.01));
            // 候補が2つ以上あるときだけ出す。1つだけ選んで絞り込んでいる項目そのもの（そのキャラ・その相手など）は候補から外す
            const cands = (dim) => { const fixed = F[dim].size === 1 ? F[dim] : null; return [...groupBy(rows, DIMS[dim].key, unit).entries()].filter((x) => x[1].n >= minN && wr(x[1]) != null && !(fixed && fixed.has(x[0]))); };
            const best = (dim, dir) => { const list = cands(dim); return list.length >= 2 ? list.sort((a, b) => dir * (wr(b[1]) - wr(a[1])) || b[1].n - a[1].n)[0] : null; };
            const item = (kind, x) => { if (!x) return ''; const d = DIMS[HL_DIM[kind]]; return hlItem(kind, d.name(x[0]), d.img && d.img(x[0]), x[1].w, x[1].l); };
            // 得意と苦手が同じものになるときは、苦手のほうを出さない
            const pair = (dim, good, bad) => { const g = best(dim, 1), b = best(dim, -1); return [item(good, g), g && b && g[0] !== b[0] ? item(bad, b) : '']; };
            const items = [item('brawler', best('brawler', 1)), ...pair('map', 'mapGood', 'mapBad'), ...pair('obr', 'oppGood', 'oppBad'), item('mate', best('mate', 1))].filter(Boolean);
            return items.length ? card('hl', 'bx-span2', T.highlights, T.highlightsCap, '', `<div class="bx-hl">${items.join('')}</div>`, true) : '';
        }

        // ---------- 推移 ----------
        const METRICS = {
            wr: { val: (a) => wr(a), fmt: (v) => v.toFixed(1) + '%' },
            kd: { val: (a) => (a.sn && a.dth ? a.k / a.dth : null), fmt: (v) => v.toFixed(2) },
            dd: { val: (a) => (a.sn ? a.dd / a.sn : null), fmt: (v) => shortText(v) },
            dt: { val: (a) => (a.sn ? a.dt / a.sn : null), fmt: (v) => shortText(v) },
            he: { val: (a) => (a.sn ? a.he / a.sn : null), fmt: (v) => shortText(v) },
            du: { val: (a) => (a.dn ? a.du / a.dn : null), fmt: (v) => dur(v) },
            tc: { val: (a) => a.tc, fmt: (v) => signed(v) },
        };
        function trendChart(id, labels, values, metric, tooltip) {
            const M = METRICS[metric];
            mountChart(id, (th) => {
                const o = baseOptions(th);
                o.scales.y.ticks.callback = (v) => M.fmt(v);
                if (metric === 'wr') { o.scales.y.min = 0; o.scales.y.max = 100; o.scales.y.grid.color = (c) => (c.tick.value === 50 ? th.mut : th.grid); }
                if (metric === 'tc') o.scales.y.grid.color = (c) => (c.tick.value === 0 ? th.mut : th.grid);
                if (tooltip) o.plugins.tooltip.callbacks = tooltip; else o.plugins.tooltip.enabled = false;
                return { type: 'line', data: { labels, datasets: [{ data: values, borderColor: th.accent, backgroundColor: th.accent + '1c', fill: metric !== 'tc', borderWidth: 2, pointRadius: values.length > 45 ? 0 : 2.5, pointBackgroundColor: th.accent, pointHoverRadius: 4, tension: 0.2 }] }, options: o };
            });
            return `<div class="bx-chart bx-chart--tall"><canvas id="${id}"></canvas></div>`;
        }
        function cardTrend(rows) {
            const V = S.trend, M = METRICS[V.metric] ? V.metric : 'wr', unit = S.F.unit, game = unit === 'game';
            // 選んでいないうちは、期間が長ければ週ごとにする
            const auto = rows.length && rows[rows.length - 1].day - rows[0].day > 150 ? 'week' : 'day';
            const bucket = V.bucket || auto, step = bucket === 'week' ? 7 : 1;
            const pts = [...groupBy(rows, (r) => Math.floor(r.day / step), unit).entries()].sort((a, b) => a[0] - b[0]).map(([k, a]) => ({ day: k * step, a, v: METRICS[M].val(a) })).filter((x) => x.v != null);
            const label = (x) => dayLabel(x.day) + (step === 7 ? T.from : '');
            let body;
            if (pts.length < 2) body = `<div class="bx-empty">${T.trendNeed}</div>`;
            else if (V.view === 'table') {
                body = `<div class="bx-tablewrap" style="max-height:240px;overflow-y:auto"><table class="bx-table bx-table--plain"><thead><tr><th>${step === 7 ? T.week : T.day}</th><th>${T.metrics[M]}</th><th>${T.colCount}</th><th>${T.colWL}</th></tr></thead><tbody>${pts.slice().reverse().map((x) => `<tr><td>${label(x)}</td><td>${METRICS[M].fmt(x.v)}</td><td>${nf(x.a.n)}</td><td>${T.wl(x.a.w, x.a.l)}</td></tr>`).join('')}</tbody></table></div>`;
            } else {
                body = trendChart('bxaTrend', pts.map(label), pts.map((x) => x.v), M, { title: (it) => label(pts[it[0].dataIndex]), label: (it) => `${T.metrics[M]} ${METRICS[M].fmt(it.parsed.y)}`, afterLabel: (it) => { const a = pts[it.dataIndex].a; return T.trendCount(nf(a.n), game, a.w, a.l); } });
            }
            const tools = `<div class="bx-pvtools">${seg('trendMetric', M, Object.keys(METRICS).map((k) => [k, T.metrics[k]]))}${seg('trendBucket', bucket, [['day', T.day], ['week', T.week]])}${seg('trendView', V.view, [['chart', T.chart], ['table', T.table]])}</div>`;
            return card('trend', 'bx-span2', T.trend, '', tools, body, true);
        }

        // ---------- 分布（1試合ごと） ----------
        const HIST = { dd: [(r) => r.dd, (v) => shortText(v)], k: [(r) => r.k, (v) => String(Math.round(v))], d: [(r) => r.d, (v) => String(Math.round(v))], dt: [(r) => r.dt, (v) => shortText(v)], du: [(r) => (r.du > 0 ? r.du : null), (v) => dur(v)], rk: [(r) => r.rk, (v) => T.place(Math.round(v))] };
        function cardHist(rows) {
            const key = HIST[S.hist] ? S.hist : 'dd', [get, fmt] = HIST[key];
            const vals = rows.map(get).filter((v) => v != null).sort((a, b) => a - b);
            let body;
            if (vals.length < 5) body = `<div class="bx-empty">${T.histNeed}</div>`;
            else {
                // 極端に大きい値で棒がつぶれないよう、上位1%は最後の区間にまとめる
                const lo = vals[0], hi = vals[Math.min(vals.length - 1, Math.floor(vals.length * 0.99))];
                const discrete = (key === 'k' || key === 'd' || key === 'rk') && hi - lo <= 24;
                const nb = discrete ? Math.max(1, hi - lo + 1) : 14, w = discrete ? 1 : (hi - lo) / nb || 1;
                const bins = new Array(nb).fill(0);
                for (const v of vals) bins[Math.min(nb - 1, Math.max(0, Math.floor((v - lo) / w)))]++;
                const max = Math.max(...bins), mid = vals[Math.floor(vals.length / 2)], avg = vals.reduce((a, b) => a + b, 0) / vals.length;
                body = `<div class="bx-hist">${bins.map((n, i) => `<i style="height:${n / max * 100}%"${tipAttr(T.histTip(discrete ? fmt(lo + i) : fmt(lo + i * w) + T.from + fmt(lo + (i + 1) * w), nf(n), (n / vals.length * 100).toFixed(1)))}></i>`).join('')}</div>
                    <div class="bx-histaxis"><span>${fmt(lo)}</span><span>${fmt(lo + (hi - lo) / 2)}</span><span>${fmt(hi)}${discrete ? '' : T.from}</span></div>
                    <div class="bx-facts"><span>${T.avg} <b>${key === 'k' || key === 'd' || key === 'rk' ? avg.toFixed(1) : fmt(avg)}</b></span><span>${T.median} <b>${fmt(mid)}</b></span><span>${T.max} <b>${fmt(vals[vals.length - 1])}</b></span><span>${T.games(nf(vals.length))}</span></div>`;
            }
            return card('hist', '', T.histTitle[key], T.histCap, `<div class="bx-pvtools">${seg('hist', key, Object.keys(HIST).map((k) => [k, T.hist[k]]))}</div>`, body, true);
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
        function cardHours(rows) {
            const game = S.F.unit === 'game';
            const cells = Array.from({ length: 7 }, () => Array.from({ length: 24 }, () => ({ n: 0, w: 0, l: 0 })));
            let max = 0;
            for (const r of rows) {
                if (!game && !r.head) continue;
                const c = cells[r.dow][r.hr], res = game ? r.r : r.sr;
                c.n++;
                if (res === 0) c.w++; else if (res === 2) c.l++;
                if (c.n > max) max = c.n;
            }
            let html = '<div class="bx-heat"><span></span>';
            for (let h = 0; h < 24; h++) html += `<span class="bx-heat__h">${h % 3 === 0 ? h + (h === 21 ? `<small>${T.hourMark}</small>` : '') : ''}</span>`;
            let best = null;
            for (const d of [1, 2, 3, 4, 5, 6, 0]) {
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
            for (let d = 0; d < 7; d++) for (let h = 0; h < 24; h++) { const slot = slots[Math.floor(h / 3)], c = cells[d][h]; slot.n += c.n; slot.w += c.w; slot.l += c.l; }
            const totalN = slots.reduce((x, slot) => x + slot.n, 0);
            const good = slots.map((slot, i) => ({ i, n: slot.n, rate: slot.w + slot.l ? slot.w / (slot.w + slot.l) * 100 : null })).filter((slot) => slot.rate != null && slot.n >= Math.max(5, totalN * 0.04)).sort((a, b) => b.rate - a.rate)[0];
            const legend = S.heatMode === 'wr'
                ? `<div class="bx-heatlegend">${T.rateHigh}<i style="background:var(--bx-win)"></i><i style="background:color-mix(in srgb,var(--bx-win) 40%,var(--bx-track))"></i><i style="background:var(--bx-track)"></i><i style="background:color-mix(in srgb,var(--bx-lose) 40%,var(--bx-track))"></i><i style="background:var(--bx-lose)"></i>${T.rateLow}</div>`
                : `<div class="bx-heatlegend">${T.few}<i style="background:rgba(91,127,214,.14)"></i><i style="background:rgba(91,127,214,.4)"></i><i style="background:rgba(91,127,214,.7)"></i><i style="background:rgba(91,127,214,1)"></i>${T.many}</div>`;
            const facts = `<div class="bx-facts bx-facts--col">${best ? `<span>${T.mostActive} ${T.mostActiveAt(T.dow[best.d], best.h, nf(best.n))}</span>` : ''}${good ? `<span>${T.bestRate} ${T.bestRateAt(good.i * 3, good.i * 3 + 3, good.rate.toFixed(1))}</span>` : ''}</div>`;
            return card('hours', '', T.hours, `${T.tz}${esc(tzLabel())}`, seg('heatMode', S.heatMode, [['n', T.battleCount], ['wr', T.winRate]]), html + legend + facts, true);
        }

        // ---------- 内訳（切り口を選ぶ表） ----------
        const PV_COLS = [
            ['n', (a) => a.n], ['wr', (a) => wr(a)], ['sp', (a) => (a.mg ? a.sp / a.mg * 100 : null)], ['k', (a) => (a.sn ? a.k / a.sn : null)], ['d', (a) => (a.sn ? a.dth / a.sn : null)],
            ['kd', (a) => (a.sn && a.dth ? a.k / a.dth : null)], ['dd', (a) => (a.sn ? a.dd / a.sn : null)], ['he', (a) => (a.sn ? a.he / a.sn : null)], ['dt', (a) => (a.sn ? a.dt / a.sn : null)],
            ['du', (a) => (a.dn ? a.du / a.dn : null)], ['tc', (a) => a.tc],
        ];
        const pvValue = (key, a) => PV_COLS.find((c) => c[0] === key)[1](a);
        // entries: [[ID, 集計], ...]（並べ替え済み）。sort・dir は見出しの印、clickable なら行を押して条件に足せる
        function pivotHtml(dimKey, entries, sort, dir, clickable) {
            const d = DIMS[dimKey];
            const max = Math.max(1, ...entries.map((x) => x[1].n));
            const td = (key, a) => {
                const v = pvValue(key, a);
                if (key === 'n') return `<td><span class="bx-cellbar">${nf(a.n)}<i style="width:${Math.max(2, a.n / max * 60)}px"></i></span></td>`;
                if (key === 'wr') return `<td><span class="bx-cellwr">${wrCell(a)}</span></td>`;
                if (v == null) return '<td class="is-mut">—</td>';
                if (key === 'sp') return `<td>${v.toFixed(1)}%</td>`;
                if (key === 'k' || key === 'd') return `<td>${v.toFixed(1)}</td>`;
                if (key === 'kd') return `<td>${v.toFixed(2)}</td>`;
                if (key === 'du') return `<td>${dur(v)}</td>`;
                if (key === 'tc') return `<td class="${v > 0 ? 'bx-pos' : v < 0 ? 'bx-neg' : 'is-mut'}">${signed(v)}</td>`;
                return `<td>${short(v)}</td>`;
            };
            const head = `<tr><th>${T.dims[dimKey]}</th>${PV_COLS.map(([k]) => `<th data-pvsort="${k}" class="${sort === k ? 'is-sort' + (dir > 0 ? ' is-asc' : '') : ''}">${T.cols[k]}</th>`).join('')}</tr>`;
            const body = entries.map(([id, a]) => {
                const img = d.img && d.img(id);
                return `<tr${clickable && d.f ? ` data-fadd="${d.f}:${esc(id)}" style="cursor:pointer"` : ''}><td><span class="bx-cellname">${img ? `<img src="${img}" alt="" loading="lazy" onerror="this.remove()">` : ''}<span>${esc(d.name(id))}</span></span></td>${PV_COLS.map(([k]) => td(k, a)).join('')}</tr>`;
            }).join('');
            return `<div class="bx-tablewrap"><table class="bx-table"><thead>${head}</thead><tbody>${body || `<tr><td colspan="${PV_COLS.length + 1}" class="is-mut" style="text-align:center">${T.none}</td></tr>`}</tbody></table></div>`;
        }
        function cardPivot(rows) {
            const V = S.pv, d = DIMS[V.dim];
            const all = [...groupBy(rows, d.key, S.F.unit).entries()].filter((x) => x[1].n >= V.min);
            all.sort((a, b) => { const x = pvValue(V.sort, a[1]), y = pvValue(V.sort, b[1]); if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1; return V.dir * (x - y) || b[1].n - a[1].n; });
            const list = V.more ? all : all.slice(0, 12);
            const tools = `<div class="bx-pvtools"><span class="bx-card__cap">${T.groupBy}</span>${seg('pvDim', V.dim, Object.keys(DIMS).map((k) => [k, T.dims[k]]))}
                <label class="bx-card__cap">${T.minCount(`<input type="number" class="bxf-num" style="width:52px" min="1" data-pvmin value="${V.min}" inputmode="numeric">`)}</label></div>`;
            const more = all.length > 12 ? `<button type="button" class="bx-more" data-act="pvmore">${V.more ? T.showTop : T.showAll(nf(all.length))}</button>` : '';
            return card('pivot', 'bx-span2', T.pivot, T.pivotCap, tools, pivotHtml(V.dim, list, V.sort, V.dir, true) + more, true);
        }

        // ---------- バトルの表 ----------
        // [キー, 並べ替えの値, 左揃えか]
        const BT_COLS = [['t', (r) => r.t, 1], ['r', (r) => r.r, 1], ['b', (r) => r.b, 1], ['tc', (r) => (r.ty === 1 ? r.rsDiff : r.tc) || null, 1], ['ty', (r) => r.ty, 1], ['m', (r) => r.m, 1], ['e', (r) => r.e, 1],
            ['k', (r) => r.k], ['d', (r) => r.d], ['dd', (r) => r.dd], ['he', (r) => r.he], ['dt', (r) => r.dt], ['du', (r) => (r.du > 0 ? r.du : null)]];
        // 表の1行ぶん。セット単位では、セットを1行にまとめる（スタッツとバトル時間は合計。押すと試合ごと）
        function battleItems(rows, unit) {
            const items = [];
            const finish = (it) => {
                // ガチバトルのポイント変動。勝敗と矛盾する・大きすぎる変動は出さない（バトルカードと同じ）
                it.rsDiff = null;
                if (it.ty === 1 && it.srs && it.srsPrev) { const diff = it.srs - it.srsPrev; if (!((it.r === 0 && diff <= 0) || (it.r === 2 && diff >= 0) || Math.abs(diff) > 250)) it.rsDiff = diff; }
                items.push(it);
            };
            if (unit === 'game') { for (const r of rows) finish({ ...r, games: null, score: null, star: r.ssp }); return items; }
            let i = 0;
            while (i < rows.length) {
                let k = i + 1;
                while (k < rows.length && rows[k].sid === rows[i].sid) k++;
                const games = rows.slice(i, k), last = games[games.length - 1], first = games[0];
                if (games.length === 1) finish({ ...first, r: first.sr, games: null, score: null, star: first.ssp });
                else {
                    const sum = (key) => (games.some((g) => g[key] != null) ? games.reduce((s, g) => s + (g[key] || 0), 0) : null);
                    finish({ ...last, r: first.sr, tr: first.tr, star: first.ssp, tc: sum('tc'), k: sum('k'), d: sum('d'), dd: sum('dd'), he: sum('he'), dt: sum('dt'),
                        du: games.reduce((s, g) => s + (g.du > 0 ? g.du : 0), 0) || null, games, score: `${games.filter((g) => g.r === 0).length}-${games.filter((g) => g.r === 2).length}` });
                }
                i = k;
            }
            return items;
        }
        // 並べ替えて、表示するページのぶんを取り出す
        function pageItems(items, B, limit) {
            const col = BT_COLS.find((c) => c[0] === B.sort) || BT_COLS[0];
            const sorted = items.slice().sort((a, b) => { const x = col[1](a), y = col[1](b); if (x == null && y == null) return b.t - a.t; if (x == null) return 1; if (y == null) return -1; return B.dir * (x - y) || b.t - a.t; });
            return sorted.slice(B.page * limit, B.page * limit + limit);
        }
        function battleTable(items, B, limit) {
            const list = pageItems(items, B, limit);
            const head = `<tr>${BT_COLS.map(([k, , left]) => `<th data-btsort="${k}" class="${left ? 'is-l ' : ''}${B.sort === k ? 'is-sort' + (B.dir > 0 ? ' is-asc' : '') : ''}">${T.bt[k]}${k === 'r' ? '<small class="bx-thnote"><span class="bx-star">★</span>: MVP</small>' : ''}</th>`).join('')}</tr>`;
            const num = (v, f) => (v == null ? '<td class="is-mut">—</td>' : `<td>${f ? f(v) : nf(v)}</td>`);
            const delta = (v) => `<b class="${v > 0 ? 'bx-pos' : 'bx-neg'}">(${signed(v)})</b>`;
            const trophy = (r) => {
                if (r.ty === 1) {
                    const icon = r.tr >= 1 && r.tr <= 22 ? `<img src="${IMG}/ranked_icons/${r.tr}.png" alt="">` : '';
                    if (r.rsDiff != null) return `${icon}${nf(r.srsPrev)} → ${nf(r.srs)} ${delta(r.rsDiff)}`;
                    if (r.srs) return `${icon}${nf(r.srs)}`;  // 前のセットのポイントが記録されていないときは、終了時のポイントだけ
                    return icon || '<span class="is-mut">—</span>';
                }
                if (r.tr == null || r.tr < 0 || r.ty === 2) return '<span class="is-mut">—</span>';
                const icon = `<img src="${IMG}/ui/trophy.png" alt="">`;
                return r.tc ? `${icon}${nf(r.tr)} → ${nf(r.tr + r.tc)} ${delta(r.tc)}` : `${icon}${nf(r.tr)}`;
            };
            // MVP の★: セット単位ではセットの行にだけ（開いた試合の行には付けない）、試合単位ではそのセットの全部の試合に付ける
            const cells = (r, sub) => {
                const star = !sub && r.star, modeIcon = micon(r.m);
                return `<td class="is-l"><span class="bx-rescell"><span class="bx-res bx-res--${r.r}">${T.res[r.r]}</span><span class="bx-resx">${r.score ? r.score : r.rk ? T.place(r.rk) : ''}</span><span class="bx-star"${star ? ' title="MVP"' : ''}>${star ? '★' : ''}</span></span></td>
                <td class="is-l">${r.bs.length > 1
                    // デュエルは使ったキャラを並べる（名前は吹き出しで）
                    ? `<span class="bx-cellicons">${r.bs.map((id) => `<img src="${bimg(id)}" alt="${esc(bname(id))}" loading="lazy"${tipAttr(bname(id))}>`).join('')}</span>`
                    : `<span class="bx-cellname">${r.b ? `<img src="${bimg(r.b)}" alt="" loading="lazy" onerror="this.remove()">` : ''}<span>${r.b ? esc(bname(r.b)) : '—'}</span></span>`}</td>
                <td class="is-l bx-tro">${sub ? '' : trophy(r)}</td>
                <td class="is-l">${TYPE_ICON[r.ty] ? `<img class="bx-tyicon" src="${IMG}/${TYPE_ICON[r.ty]}" alt="${T.types[r.ty]}"${tipAttr(T.types[r.ty])}>` : `<span class="bx-tyother"${tipAttr(T.types[3])}>${T.typeOther}</span>`}</td>
                <td class="is-l"><span class="bx-cellname bx-cellname--mode">${modeIcon ? `<img src="${modeIcon}" alt="" loading="lazy" onerror="this.remove()">` : ''}<span>${esc(mname(r.m))}</span></span></td>
                <td class="is-l">${r.e ? `<a class="bx-maplink" href="/${lang}/tools/map/${r.e}">${esc(mapname(r.e))}</a>` : '<span class="is-mut">—</span>'}</td>
                ${num(r.k)}${num(r.d)}${num(r.dd)}${num(r.he)}${num(r.dt)}${num(r.du > 0 ? r.du : null, dur)}`;
            };
            const body = list.map((r) => {
                const open = r.games && B.open.has(r.sid);
                const main = `<tr${r.games ? ` class="bx-setrow${open ? ' is-open' : ''}" data-btopen="${r.sid}"` : ''}><td class="is-l">${r.games ? '<span class="bx-chev"></span>' : '<span class="bx-chevpad"></span>'}${ymdFull(r.t)}</td>${cells(r, false)}</tr>`;
                if (!open) return main;
                return main + r.games.map((g, n) => `<tr class="bx-subrow"><td class="is-l"><span class="bx-chevpad"></span>${T.gameNo(n + 1)} ${hm(g.t)}</td>${cells({ ...g, score: null }, true)}</tr>`).join('');
            }).join('');
            return `<div class="bx-tablewrap"><table class="bx-table bx-table--battle"><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
        }
        function cardBattles(rows) {
            const B = S.bt, game = S.F.unit === 'game', items = battleItems(rows, S.F.unit), pages = Math.max(1, Math.ceil(items.length / PER_PAGE));
            if (B.page >= pages) B.page = pages - 1;
            const pager = pages > 1 ? `<div class="bx-pager"><button type="button" data-act="btprev"${B.page ? '' : ' disabled'}>${T.prev}</button><span>${nf(B.page + 1)} / ${nf(pages)}</span><button type="button" data-act="btnext"${B.page < pages - 1 ? '' : ' disabled'}>${T.next}</button></div>` : '';
            const tools = seg('btView', B.view, [['card', T.viewCard], ['table', T.viewTable]]);
            if (B.view === 'card') {
                // カードは、表示するページのぶんだけサーバーから HTML で受け取る（通常のタブと同じカード）
                const list = pageItems(items, B, PER_PAGE);
                after.push(() => loadCards(list));
                return card('battles', 'bx-span2', T.battles, T.cardsCap(nf(items.length), game), tools, `<div class="bxa-cards" data-lang="${lang}" data-img-base="${esc(IMG)}"><div class="bx-loading"><span class="player-shell__spinner"></span><span>${T.cardsLoading}</span></div></div>${pager}`, false);
            }
            return card('battles', 'bx-span2', T.battles, T.battlesCap(nf(items.length), game), tools, battleTable(items, B, PER_PAGE) + pager, false);
        }
        function loadCards(list) {
            const box = root.querySelector('.bxa-cards');
            if (!box) return;
            const game = S.F.unit === 'game';
            // セットごとに「試合の時刻を . でつないだもの[:終わりのポイント[:1つ前のポイント]]」。ポイントは表と同じ値をカードにも出すために渡す
            const key = list.map((it) => (it.games ? it.games.map((g) => g.t).join('.') : String(it.t)) + (!game && it.ty === 1 && it.srs ? `:${it.srs}${it.srsPrev ? ':' + it.srsPrev : ''}` : '')).join(',');
            const loadId = ++cardLoadId;
            const show = (html) => {
                if (loadId !== cardLoadId || !document.body.contains(box)) return;
                box.innerHTML = `<div class="bcr-tools bxa-cardtools"><button type="button" class="bcr-all" role="switch" aria-checked="false"><span>${T.openResults}</span><span class="bcr-all__track"></span></button></div>${html}`;
                if (typeof window.initBattleResults === 'function') window.initBattleResults(box);
            };
            const fail = (html) => { if (loadId === cardLoadId && document.body.contains(box)) box.innerHTML = `<div class="bx-empty">${html}</div>`; };
            if (cardCache.has(key)) { show(cardCache.get(key)); return; }
            gatedFetch(`${root.dataset.cardsSrc}?sets=${encodeURIComponent(key)}`).then((res) => {
                if (res.status === 401) { fail(`${T.needLogin} <a class="bx-maplink" href="${esc(root.dataset.login)}">${T.login}</a>`); return null; }
                if (res.status === 429) { fail(T.rate); return null; }
                if (!res.ok) throw new Error(String(res.status));
                return res.text();
            }).then((html) => {
                if (html == null) return;
                if (cardCache.size >= CARD_CACHE_MAX) cardCache.delete(cardCache.keys().next().value);
                cardCache.set(key, html);
                show(html);
            }).catch(() => fail(T.loadError));
        }

        // ---------- 描画 ----------
        const CARD_FN = { pivot: cardPivot, battles: cardBattles, trend: cardTrend, hist: cardHist, hours: cardHours };
        function render() {
            after = [];
            destroyCharts();
            closePop();
            const rows = analysisRows();
            S.curRows = rows;
            renderFilters(rows);
            writeUrl();
            if (!rows.length) {
                topSlot.innerHTML = `<div class="bx-empty bx-span2">${T.noMatch}</div>`;
                bottomSlot.innerHTML = '';
                return;
            }
            topSlot.innerHTML = cardSummary(rows) + cardHighlights(rows) + cardTrend(rows) + cardPivot(rows) + cardHist(rows) + cardHours(rows);
            bottomSlot.innerHTML = cardBattles(rows);
            runAfter();
        }
        // カード1枚だけ描き直す（ほかのカードのグラフは動かさない）
        function rerenderCard(key) {
            const el = root.querySelector(`[data-card="${key}"]`);
            if (!el || !CARD_FN[key]) { render(); return; }
            destroyCharts(el);
            after = [];
            el.outerHTML = CARD_FN[key](S.curRows);
            runAfter();
        }
        function showMessage(html) {
            destroyCharts();
            topSlot.innerHTML = `<div class="bx-empty bx-span2">${html}</div>`;
            bottomSlot.innerHTML = '';
            if (filtersEl) filtersEl.innerHTML = '';
        }

        // ---------- 未ログイン: 機能の紹介（サーバーが埋め込んだ集計の上のほうだけ見せる） ----------
        function renderGate(preview) {
            names = preview.names;
            statsSince = preview.statsSince || 0;
            // ログイン後は、いま開いている URL（共有された条件つきのものなら、その条件のまま）へ戻る
            const here = /(^|[?&])tab=analysis(&|$)/.test(location.search) ? location.pathname + location.search : null;
            const loginUrl = esc(here ? `/${lang}/login?next=${encodeURIComponent(here)}` : root.dataset.login), registerUrl = esc(root.dataset.register);
            const entries = (list) => list.map((x) => [x[0], toAgg(x, 1)]);
            const brawlers = entries(preview.brawlers || []), opponents = entries(preview.opponents || []);
            const tableRows = markHeads(buildRows(preview.table));
            const lockCta = (text) => `<a class="bx-lock__cta" href="${loginUrl}"><img src="${IMG}/ui/locked.png" alt="">${text}</a>`;
            const feat = (cls, title, lead, body) => `<section class="bx-card ${cls}"><div class="bx-card__head"><h3 class="bx-card__title">${title}</h3></div><p class="bx-feat__lead">${lead}</p>${body}</section>`;
            const cta = `<a class="button button--primary" href="${registerUrl}">${T.register}</a><a class="button button--secondary" href="${loginUrl}">${T.login}</a>`;
            const hero = `<div class="bx-hero bx-span2"><div class="bx-hero__text"><h2 class="bx-hero__title"><img src="${IMG}/ui/settings.png" alt="">${T.heroTitle}</h2>
                <p class="bx-hero__lead">${T.heroLead(esc(root.dataset.playerName), nf(preview.games))}</p></div>
                <div class="bx-hero__cta">${cta}<p class="bx-hero__free">${T.free}</p></div></div>`;
            const demoChips = `<div class="bx-demo-chips"><span class="bx-chip is-on">${T.types[1]}</span><span class="bx-chip is-on">${T.demo30}</span>${brawlers.slice(0, 3).map(([id]) => `<span class="bx-chip bx-chip--sel"><img src="${bimg(id)}" alt="">${esc(bname(id))}</span>`).join('')}
                <span class="bx-chip bx-chip--pick">${T.dims.map}</span><span class="bx-chip bx-chip--pick">${T.dims.mate}</span><span class="bx-chip bx-chip--pick">${T.dims.obr}</span><span class="bx-chip">${T.mvpOnly}</span></div>`;
            let top = hero + feat('bx-span2', T.featFilter, T.featFilterLead, demoChips);
            const hl = (preview.highlights || []).map(([kind, id, w, l]) => {
                const dim = HL_DIM[kind];
                return dim === 'mate' ? hlItem(kind, id, null, w, l) : hlItem(kind, DIMS[dim].name(id), DIMS[dim].img(id), w, l);
            });
            if (hl.length) top += feat('bx-span2', T.featHl, T.featHlLead, `<div class="bx-lock" style="--h:150px;--keep:52%"><div class="bx-lock__body"><div class="bx-hl">${hl.join('')}</div></div>${lockCta(T.ctaHl)}</div>`);
            if (brawlers.length) top += feat(opponents.length ? '' : 'bx-span2', T.featPivot, T.featPivotLead, `<div class="bx-lock" style="--h:250px;--keep:48%"><div class="bx-lock__body">${pivotHtml('brawler', brawlers, 'n', -1, false)}</div>${lockCta(T.ctaPivot(nf(preview.brawlerCount)))}</div>`);
            if (opponents.length) top += feat('', T.featOpp, T.featOppLead, `<div class="bx-lock" style="--h:250px;--keep:40%"><div class="bx-lock__body">${pivotHtml('obr', opponents, 'wr', 1, false)}</div>${lockCta(T.ctaOpp)}</div>`);
            // 推移の見本は、週ごとの勝率
            const weeks = (preview.trend || []).filter((x) => x[1] + x[2] > 0);
            const hasTrend = weeks.length >= 3;
            // 半分の幅の箱が偶数個なら、推移は横いっぱいにする（右が空かないように）
            if (hasTrend) top += feat((brawlers.length ? 1 : 0) + (opponents.length ? 1 : 0) === 1 ? '' : 'bx-span2', T.featTrend, T.featTrendLead, `<div class="bx-lock" style="--h:230px;--keep:55%"><div class="bx-lock__body">${trendChart('bxaGateTrend', weeks.map((x) => dayLabel(x[0] / 86400)), weeks.map((x) => x[1] / (x[1] + x[2]) * 100), 'wr', null)}</div>${lockCta(T.ctaTrend)}</div>`);
            topSlot.innerHTML = top;
            const table = battleTable(battleItems(tableRows, 'set'), { sort: 't', dir: -1, page: 0, open: new Set() }, 6);
            bottomSlot.innerHTML = feat('bx-span2', T.featTable, T.featTableLead, `<div class="bx-lock" style="--h:230px;--keep:45%"><div class="bx-lock__body">${table}</div>${lockCta(T.ctaTable)}</div>`)
                + `<div class="bx-hero bx-hero--slim bx-span2"><div class="bx-hero__text"><p class="bx-hero__lead">${T.heroMore}</p></div><div class="bx-hero__cta">${cta}</div></div>`;
            runAfter();
        }

        // ---------- 操作 ----------
        function toggleFilterValue(group, raw) {
            const F = S.F;
            if (group === 'period') { F.period = raw; F.from = ''; F.to = ''; F.season = ''; }
            else if (group === 'mvp') F.mvp = !F.mvp;
            else { const v = parseId(raw); if (F[group].has(v)) F[group].delete(v); else F[group].add(v); }
            S.bt.page = 0;
            render();
        }
        if (filtersEl) {
            filtersEl.addEventListener('click', (e) => {
                const segBtn = e.target.closest('.bx-seg button');
                if (segBtn) { if (segBtn.parentElement.dataset.seg === 'unit') { S.F.unit = segBtn.dataset.v; S.bt.page = 0; S.bt.open.clear(); render(); } return; }
                const pick = e.target.closest('[data-pick]');
                if (pick) { if (me.pop && me.pop.dim === pick.dataset.pick) closePop(); else openPop(pick, pick.dataset.pick); return; }
                const fdel = e.target.closest('[data-fdel]');
                if (fdel) { const [group, raw] = fdel.dataset.fdel.split(':'); S.F[group].delete(parseId(raw)); S.bt.page = 0; render(); return; }
                const fchip = e.target.closest('[data-fchip]');
                if (fchip) { const [group, raw] = fchip.dataset.fchip.split(':'); toggleFilterValue(group, raw); return; }
                const act = e.target.closest('[data-act]');
                if (!act) return;
                if (act.dataset.act === 'fmore') {
                    // 開閉の動きが途切れないよう、描き直さずにその場で切り替える
                    S.moreOpen = !S.moreOpen;
                    act.classList.toggle('is-on', S.moreOpen);
                    act.setAttribute('aria-expanded', String(S.moreOpen));
                    filtersEl.querySelector('.bxf-more').classList.toggle('is-open', S.moreOpen);
                } else if (act.dataset.act === 'fclear') {
                    const unit = S.F.unit;
                    S.F = newF(); S.F.unit = unit; S.bt.page = 0;
                    render();
                }
            });
            filtersEl.addEventListener('change', (e) => {
                const t = e.target, F = S.F;
                if (t.dataset.fnum) F[t.dataset.fnum] = t.value === '' ? null : Math.max(0, Number(t.value) || 0);
                else if (t.dataset.fdate) { F[t.dataset.fdate] = dayIndex(t.value) == null ? '' : t.value; F.season = ''; }
                else if (t.dataset.fseason !== undefined) {
                    // シーズンを選ぶと、その期間が日付に入る
                    const season = seasons.find((x) => String(x[0]) === t.value);
                    F.season = season ? season[0] : ''; F.from = season ? season[2] : ''; F.to = season ? season[3] : '';
                } else return;
                S.bt.page = 0;
                // 次の欄へ移った直後に描き直すと、移った先の欄が置き換わってしまうので、フォーカスが移ってから描き直す
                setTimeout(render, 0);
            });
        }
        root.addEventListener('click', (e) => {
            if (root.dataset.mode !== 'full') return;
            const segBtn = e.target.closest('.bx-seg button');
            if (segBtn) {
                const name = segBtn.parentElement.dataset.seg, v = segBtn.dataset.v;
                if (name === 'pvDim') { S.pv.dim = v; S.pv.more = false; savePref({ pvDim: v }); rerenderCard('pivot'); }
                else if (name === 'trendMetric' || name === 'trendBucket' || name === 'trendView') { S.trend[{ trendMetric: 'metric', trendBucket: 'bucket', trendView: 'view' }[name]] = v; savePref({ aTrend: S.trend }); rerenderCard('trend'); }
                else if (name === 'hist') { S.hist = v; savePref({ aHist: v }); rerenderCard('hist'); }
                else if (name === 'heatMode') { S.heatMode = v; savePref({ heatMode: v }); rerenderCard('hours'); }
                else if (name === 'btView') { S.bt.view = v; savePref({ aBtView: v }); rerenderCard('battles'); }
                return;
            }
            // カード表示のキャラを押したら、そのキャラを条件に足す（通常のタブへは移動しない）
            const brawlerLink = e.target.closest('.bxa-cards a.battle-card__brawler-filter-link');
            if (brawlerLink) {
                const id = Number(new URL(brawlerLink.href, location.href).searchParams.get('brawler_id'));
                if (id) { e.preventDefault(); S.F.brawler.add(id); S.bt.page = 0; render(); if (filtersEl) filtersEl.scrollIntoView({ block: 'nearest' }); }
                return;
            }
            const act = e.target.closest('[data-act]');
            if (act) {
                const a = act.dataset.act;
                if (a === 'fold') {
                    const cardEl = act.closest('.bx-card'), closed = closedSet();
                    const nowClosed = cardEl.classList.toggle('is-closed');
                    act.setAttribute('aria-expanded', String(!nowClosed));
                    if (nowClosed) closed.add(cardEl.dataset.card); else closed.delete(cardEl.dataset.card);
                    writeJson(CLOSED_KEY, [...closed]);
                    if (!nowClosed) setTimeout(() => { charts.forEach((c) => { if (cardEl.contains(c.canvas)) c.resize(); }); fitWbars(); }, 220);
                } else if (a === 'pvmore') { S.pv.more = !S.pv.more; rerenderCard('pivot'); }
                else if (a === 'btprev' || a === 'btnext') {
                    S.bt.page += a === 'btnext' ? 1 : -1;
                    rerenderCard('battles');
                    const cardEl = root.querySelector('[data-card="battles"]');
                    if (cardEl && cardEl.getBoundingClientRect().top < 0) cardEl.scrollIntoView({ block: 'start' });
                } else if (a === 'retry') load();
                return;
            }
            const pvs = e.target.closest('[data-pvsort]');
            if (pvs) { const k = pvs.dataset.pvsort; if (S.pv.sort === k) S.pv.dir = -S.pv.dir; else { S.pv.sort = k; S.pv.dir = -1; } rerenderCard('pivot'); return; }
            const bts = e.target.closest('[data-btsort]');
            if (bts) { const k = bts.dataset.btsort; if (S.bt.sort === k) S.bt.dir = -S.bt.dir; else { S.bt.sort = k; S.bt.dir = -1; } S.bt.page = 0; rerenderCard('battles'); return; }
            const btopen = e.target.closest('[data-btopen]');
            if (btopen && !e.target.closest('a')) {
                const sid = Number(btopen.dataset.btopen), wasOpen = S.bt.open.has(sid);
                if (e.shiftKey) {  // Shift + クリックで、セットの行をまとめて開閉
                    if (wasOpen) S.bt.open.clear(); else battleItems(S.curRows, 'set').forEach((it) => { if (it.games) S.bt.open.add(it.sid); });
                    const sel = window.getSelection && window.getSelection();
                    if (sel && sel.removeAllRanges) sel.removeAllRanges();
                } else if (wasOpen) S.bt.open.delete(sid); else S.bt.open.add(sid);
                rerenderCard('battles');
                return;
            }
            const fadd = e.target.closest('[data-fadd]');
            if (fadd) { const [group, raw] = fadd.dataset.fadd.split(':'); toggleFilterValue(group, raw); }
        });
        root.addEventListener('change', (e) => {
            if (e.target.dataset.pvmin === undefined) return;
            S.pv.min = Math.max(1, Number(e.target.value) || 1);
            rerenderCard('pivot');
        });

        // ---------- 開始 ----------
        function load() {
            gatedFetch(root.dataset.src).then((res) => {
                if (res.status === 401) { showMessage(`${T.needLogin} <a class="bx-maplink" href="${esc(root.dataset.login)}">${T.login}</a>`); return null; }
                if (res.status === 429) { showMessage(T.rate); return null; }
                if (!res.ok) throw new Error(String(res.status));
                return res.json();
            }).then((P) => {
                if (!P || !document.body.contains(root)) return;
                names = P.names; players = P.players || []; seasons = P.seasons || []; statsSince = P.statsSince || 0; capped = P.capped ? P.cap : 0;
                ROWS = buildRows(P);
                setCount = ROWS.length ? ROWS[ROWS.length - 1].sid + 1 : 0;
                if (!ROWS.length) { showMessage(T.empty); return; }
                readUrl();
                if (setCount < 60) S.pv.min = 1;  // バトルが少ないうちは、内訳の「◯回以上」で何も出なくなるので下げておく
                render();
            }).catch(() => {
                if (document.body.contains(root)) showMessage(`${T.loadError} <button type="button" class="bxf-clear" data-act="retry">${T.retry}</button>`);
            });
        }
        if (root.dataset.mode === 'full') load();
        else {
            const previewEl = root.querySelector('#battleAnalysisPreview');
            let preview = null;
            try { preview = previewEl ? JSON.parse(previewEl.textContent) : null; } catch (e) { preview = null; }
            if (preview) renderGate(preview); else showMessage(T.empty);
        }
        // フラグメントは隠れた状態で差し込まれることがあるので、幅が決まった・変わったときに帯の文字を詰め直す
        if (window.ResizeObserver) new ResizeObserver(() => fitWbars()).observe(root);
    };
})();
