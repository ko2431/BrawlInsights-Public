// バトル履歴ページ: バトルカードの「リザルト」の開閉と、勝敗ダイジェストの吹き出し。
// 開いたときの中身は、ページに埋め込んだ JSON（#battleResultsData。app/services/battle_result_view.py が作る）から、初めて開いたときに描く。
// フラグメントはサブタブの切り替えや更新のたびに差し替わるので、そのつど window.initBattleResults(差し込んだ要素) を呼ぶ。
(function () {
    'use strict';

    const OPEN_ALL_KEY = 'bi_battles_results_open';
    const TEXT = {
        ja: {
            kd: 'キル/デス', dmg: '与ダメ', heal: '回復', taken: '被ダメ',
            modeStat: { 1: 'エメ回収', 2: 'ゴール', 3: '星', 4: '金庫ダメ', 5: 'ゾーン' }, modeStatOther: 'モード', sec: '秒',
            ally: '味方', enemy: '敵', allyTeam: '味方チーム', enemyTeam: '敵チーム',
            total: '合計', game: (n) => `${n}戦目`, result: { w: '勝利', l: '敗北', d: '引き分け' }, games: (n) => `(${n}試合)`,
            aborted: '開始前に終了', bot: 'ボット', mvp: 'MVP',
            rankOf: (count, solo, rank) => `${count}${solo ? '人' : 'チーム'}中 <b>${rank}位</b>`,
            noteAborted: 'バトルが始まる前に終了したため、スタッツはありません。',
            noteOld: '2026年11月アップデート以前のバトルのスタッツは保存されていません。',
            noteNone: 'このバトルのスタッツは保存されていません。',
        },
        en: {
            kd: 'K/D', dmg: 'DMG', heal: 'Heal', taken: 'Taken',
            modeStat: { 1: 'Gems', 2: 'Goals', 3: 'Stars', 4: 'Safe', 5: 'Zone' }, modeStatOther: 'Mode', sec: 's',
            ally: 'Ally', enemy: 'Enemy', allyTeam: 'Your Team', enemyTeam: 'Enemy Team',
            total: 'Total', game: (n) => `Game ${n}`, result: { w: 'Win', l: 'Loss', d: 'Draw' }, games: (n) => `(${n} games)`,
            aborted: 'Ended before start', bot: 'Bot', mvp: 'Star Player',
            rankOf: (count, solo, rank) => `<b>#${rank}</b> of ${count}${solo ? '' : ' teams'}`,
            noteAborted: 'No stats: this battle ended before it started.',
            noteOld: 'Stats are not available for battles before the November 2026 update.',
            noteNone: 'Stats were not saved for this battle.',
        },
    };
    const CLOCK_SVG = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true"><circle cx="8" cy="8" r="6.2"/><path d="M8 4.6V8l2.3 1.5"/></svg>';
    // プレイヤーの配列の並び: [タグ, 名前, キャラ, パワー, トロフィー, BANしたキャラ]
    const P_TAG = 0, P_NAME = 1, P_BRAWLER = 2, P_POWER = 3, P_TROPHIES = 4, P_BAN = 5;

    const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    // ボットはタグが短い（#8GQ など）
    const isBot = (tag) => !tag || tag.length <= 5;
    const fmtDuration = (seconds) => `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;

    let tipEl = null;
    let gridObserver = null;
    // スクロールしたら吹き出しを消す（タイルから離れるので）
    window.addEventListener('scroll', () => { if (tipEl) tipEl.classList.remove('is-on'); }, { passive: true });

    window.initBattleResults = function (root) {
        const dataEl = root && root.querySelector('#battleResultsData');
        const grid = root && root.querySelector('.battle-card-grid');
        if (!dataEl || !grid) return;
        let data;
        try {
            data = JSON.parse(dataEl.textContent);
        } catch (e) {
            return;
        }
        const lang = root.dataset.lang === 'ja' ? 'ja' : 'en';
        const T = TEXT[lang];
        const IMG = root.dataset.imgBase || '/static/images';
        const cards = data.cards || {};
        const names = data.names || {};
        const state = new WeakMap(); // カードごとの、選んでいる試合（セット）

        const fmtNum = (n) => (n == null ? '-' : n.toLocaleString('en-US'));
        // 収まらないときの短い表記。日本語は万（有効3桁: 1.23万・12.3万・123万）、英語は k
        const fmtShort = (n) => {
            if (n == null) return '-';
            if (n < 10000) return n.toLocaleString('en-US');
            if (lang !== 'ja') return (n / 1000).toFixed(1) + 'k';
            const v = n / 10000;
            return (v >= 100 ? Math.round(v).toLocaleString('en-US') : v >= 10 ? v.toFixed(1) : v.toFixed(2)) + '<span class="bcr-u">万</span>';
        };
        const brawlerImg = (id) => `${IMG}/brawler_border_portraits/${id}.png`;
        const imgFallback = `onerror="this.onerror=null; this.src='${IMG}/player_icon/0.png';"`;
        const profileHref = (tag) => `/${lang}/player/profile/${encodeURIComponent(tag.slice(1))}`;
        const powerImg = (power, cls) => (power > 0 ? `<img src="${IMG}/power/${Math.min(power, 11)}.png" alt="" class="${cls}">` : '');

        // ---------- 帯: 収まらない人は隠して +N ----------
        function fitStrip(card) {
            const block = card.querySelector('.bcr-strip > .battle-card__new-playedwith-block');
            if (!block || !block.offsetWidth) return;
            const units = [...block.children].filter((el) => el.matches('.battle-card__new-playedwith-block__player')
                || (el.matches('.battle-card__playedwith-group') && el !== block.firstElementChild));
            if (!units.length) return;
            let more = block.querySelector('.bcr-strip__more');
            if (!more) {
                more = document.createElement('span');
                more.className = 'bcr-strip__more';
                block.appendChild(more);
            }
            units.forEach((unit) => { unit.hidden = false; });
            more.hidden = true;
            if (block.scrollWidth <= block.clientWidth + 1) return;
            more.hidden = false;
            const countOf = (unit) => (unit.matches('.battle-card__new-playedwith-block__player') ? 1 : unit.querySelectorAll('.battle-card__new-playedwith-block__player').length);
            let hiddenCount = 0;
            for (let i = units.length - 1; i >= 0 && block.scrollWidth > block.clientWidth + 1; i--) {
                units[i].hidden = true;
                hiddenCount += countOf(units[i]);
                more.textContent = `+${hiddenCount}`;
            }
        }

        // ---------- 開いたときの中身 ----------
        // セットの「合計」
        function totalGame(view) {
            const withStats = view.games.filter((game) => game.s);
            if (!withStats.length) return { d: null, s: null, sp: null };
            const sum = withStats[0].s.map((row) => row.map(() => 0));
            let hasModeStat = false;
            withStats.forEach((game) => game.s.forEach((row, i) => row.forEach((value, j) => {
                if (value != null) {
                    sum[i][j] += value;
                    if (j === 5) hasModeStat = true;
                }
            })));
            if (!hasModeStat) sum.forEach((row) => { row[5] = null; });
            // セットのMVPはセット全体で決まり、APIでは最後の試合にだけ付いて来る。試合ごとではなく合計のところに出す
            const star = view.games[view.games.length - 1].sp;
            return { d: view.games.reduce((total, game) => total + Math.max(0, game.d || 0), 0), s: sum, sp: star ?? null };
        }
        function gameOf(view, selected) {
            if (selected === 'all') return totalGame(view);
            const game = view.games[selected];
            return view.games.length > 1 ? { ...game, sp: null } : game;
        }

        function stripMetaHTML(view, selected) {
            if (view.k === 'r') {
                return `<span class="bcr-chip">${T.rankOf(view.teams.length, view.ts === 1, view.rank)}</span>`;
            }
            if (view.note === 'aborted') return `<span class="bcr-chip bcr-chip--warn">${T.aborted}</span>`;
            const game = view.k === 's' ? gameOf(view, selected) : view.games[0];
            if (!game || !(game.d > 0)) return '';
            const count = view.k === 's' && selected === 'all' && view.games.length > 1 ? `<span class="bcr-chip__sub">${T.games(view.games.length)}</span>` : '';
            return `<span class="bcr-chip">${CLOCK_SVG}<b>${fmtDuration(game.d)}</b>${count}</span>`;
        }

        function playerCellHTML(view, player, slot, game) {
            const star = game.sp === slot ? `<img src="${IMG}/ui/starplayer.png" alt="${T.mvp}" class="bcr-pl__star">` : '';
            const bot = isBot(player[P_TAG]) ? `<span class="bcr-pl__bot">${T.bot}</span>` : '';
            const sub = [];
            if (view.t === 'soloRanked' && player[P_TROPHIES] > 0) sub.push(`<img src="${IMG}/ranked_icons/${Math.min(player[P_TROPHIES], 22)}.png" alt="" class="bcr-pl__rank">`);
            if (view.t === 'ranked' && player[P_TROPHIES] != null) sub.push(`<span><img src="${IMG}/ui/trophy.png" alt="">${fmtNum(player[P_TROPHIES])}</span>`);
            if (player[P_BAN]) {
                const banName = esc(names[player[P_BAN]] || '');
                sub.push(`<span class="bcr-ban" title="BAN: ${banName}">BAN<span class="bcr-ban__img"><img src="${brawlerImg(player[P_BAN])}" alt="${banName}" ${imgFallback}></span></span>`);
            }
            const power = view.t === 'ranked' || view.t === 'soloRanked' ? powerImg(player[P_POWER], 'bcr-pl__power') : '';
            const inner = `<span class="bcr-pl__icon"><img src="${brawlerImg(player[P_BRAWLER])}" alt="" loading="lazy" ${imgFallback}>${power}</span>
                <span class="bcr-pl__txt"><span class="bcr-pl__name"><span>${esc(player[P_NAME])}</span>${star}${bot}</span>${sub.length ? `<span class="bcr-pl__sub">${sub.join('')}</span>` : ''}</span>`;
            return isBot(player[P_TAG]) ? `<span class="bcr-pl">${inner}</span>` : `<a class="bcr-pl" href="${profileHref(player[P_TAG])}">${inner}</a>`;
        }

        function statsTableHTML(view, game) {
            const players = [view.me, ...view.mates, ...view.opps];
            const allyCount = 1 + view.mates.length;
            const hasModeStat = view.ms != null && game.s.some((row) => row[5] != null);
            const max = [0, 1, 2, 3, 4, 5].map((j) => Math.max(1, ...game.s.map((row) => row[j] || 0)));
            // 10万以上の値がある表は、その表だけ短い表記にする（ブロウルアリーナなど）
            const big = max.some((value, j) => j >= 2 && value >= 100000);
            const unit = view.ms === 5 ? `<span class="bcr-u">${T.sec}</span>` : '';
            const word = (single, team) => (view.ts > 1 ? team : single);
            const numCell = (value, j) => `<span class="bcr-num${value === max[j] && value > 0 ? ' is-top' : ''}${!value ? ' is-zero' : ''}">${big ? fmtShort(value) : fmtNum(value)}${j === 5 ? unit : ''}<i class="bcr-num__bar" style="--p:${Math.round(((value || 0) / max[j]) * 100)}%"></i></span>`;
            const row = (player, slot) => {
                const s = game.s[slot];
                return `<div class="bcr-row ${slot < allyCount ? 'bcr-row--ally' : 'bcr-row--enemy'}${slot === 0 ? ' is-me' : ''}">
                    ${playerCellHTML(view, player, slot, game)}
                    <span class="bcr-num bcr-num--kd"><b>${fmtNum(s[0])}</b><i>/</i>${fmtNum(s[1])}</span>
                    ${numCell(s[2], 2)}${numCell(s[3], 3)}${numCell(s[4], 4)}${hasModeStat ? numCell(s[5], 5) : ''}
                </div>`;
            };
            const team = (label, cls, from, to) => {
                const total = [0, 0, 0, 0, 0, 0];
                for (let i = from; i < to; i++) game.s[i].forEach((value, j) => { total[j] += value || 0; });
                return `<div class="bcr-team ${cls}"><span class="bcr-team__label">${label}</span><span class="bcr-num">${total[0]}/${total[1]}</span><span class="bcr-num">${fmtShort(total[2])}</span><span class="bcr-num">${fmtShort(total[3])}</span><span class="bcr-num">${fmtShort(total[4])}</span>${hasModeStat ? `<span class="bcr-num">${fmtShort(total[5])}${unit}</span>` : ''}</div>`;
            };
            let html = `<div class="bcr-table${hasModeStat ? ' has-ms' : ''}${view.ms === 4 || view.ms === 1 ? ' is-wide-ms' : ''}">
                <div class="bcr-head"><span></span><span>${T.kd}</span><span>${T.dmg}</span><span>${T.heal}</span><span>${T.taken}</span>${hasModeStat ? `<span>${T.modeStat[view.ms] || T.modeStatOther}</span>` : ''}</div>`;
            html += team(word(T.ally, T.allyTeam), 'bcr-team--ally', 0, allyCount);
            for (let i = 0; i < allyCount; i++) html += row(players[i], i);
            if (players.length > allyCount) {
                html += team(word(T.enemy, T.enemyTeam), 'bcr-team--enemy', allyCount, players.length);
                for (let i = allyCount; i < players.length; i++) html += row(players[i], i);
            }
            return html + '</div>';
        }

        // 名前とキャラ（順位の一覧・メンバーだけの表示で使う）。extra があればパワーとトロフィーも
        function memberHTML(player, self, extra) {
            const tag = player[P_TAG];
            const icon = `<img src="${brawlerImg(player[P_BRAWLER])}" alt="" loading="lazy" ${imgFallback}>`;
            let inner = `${icon}<span>${esc(player[P_NAME])}</span>`;
            let cls = 'bcr-mem';
            if (extra) {
                const trophies = extra.trophies && player[P_TROPHIES] != null ? `<em class="bcr-mem__tr"><img src="${IMG}/ui/trophy.png" alt="">${fmtNum(player[P_TROPHIES])}</em>` : '';
                const iconBox = `<span class="bcr-mem__icon">${icon}${powerImg(player[P_POWER], 'bcr-mem__power')}</span>`;
                inner = extra.stack ? `${iconBox}<span class="bcr-mem__col"><span>${esc(player[P_NAME])}</span>${trophies}</span>` : `${iconBox}<span>${esc(player[P_NAME])}</span>${trophies}`;
                if (extra.stack) cls += ' bcr-mem--stack';
            }
            if (self) cls += ' is-self';
            return isBot(tag) || self ? `<span class="${cls}">${inner}</span>` : `<a class="${cls}" href="${profileHref(tag)}">${inner}</a>`;
        }

        function ranksHTML(view) {
            const everyone = view.teams.flatMap((team) => team.p);
            const hasTrophies = view.t === 'ranked' && everyone.some((player) => player[P_TROPHIES] != null && !isSelf(view, player));
            const hasPower = everyone.some((player) => player[P_POWER] > 0 && !isSelf(view, player));
            const extra = hasTrophies || hasPower ? { trophies: hasTrophies, stack: hasTrophies && view.ts >= 3 } : null;
            const rows = view.teams.map((team, i) => {
                const rank = i + 1;
                const zone = !view.rule ? 'n' : rank <= view.rule[0] ? 'w' : rank <= view.rule[1] ? 'd' : 'l';
                return `<div class="bcr-rank${team.me ? ' is-me' : ''}"><span class="bcr-rank__no bcr-rank__no--${zone}">${rank}</span><div class="bcr-rank__members">${team.p.map((player, j) => memberHTML(player, !!team.me && j === 0, extra)).join('')}</div></div>`;
            });
            const twoCol = view.ts === 1 && view.teams.length > 5;
            return `<div class="bcr-ranks${twoCol ? ' bcr-ranks--2col' : ''}"${twoCol ? ` style="grid-template-rows: repeat(${Math.ceil(view.teams.length / 2)}, auto);"` : ''}>${rows.join('')}</div>`;
        }
        // 順位の一覧で、自分のチームの先頭が自分
        function isSelf(view, player) {
            const own = view.teams.find((team) => team.me);
            return !!own && own.p[0] === player;
        }

        function rosterHTML(view) {
            const word = (single, team) => (view.ts > 1 ? team : single);
            const column = (label, cls, members) => `<div class="bcr-roster__team"><span class="bcr-roster__label ${cls}">${label}</span>${members.map((player) => memberHTML(player, player === view.me, null)).join('')}</div>`;
            const allies = column(word(T.ally, T.allyTeam), 'bcr-team--ally', [view.me, ...view.mates]);
            if (!view.opps.length) return `<div class="bcr-roster bcr-roster--single">${allies}</div>`;
            return `<div class="bcr-roster">${allies}${column(word(T.enemy, T.enemyTeam), 'bcr-team--enemy', view.opps)}</div>`;
        }

        function duelHTML(view) {
            const icons = (ids) => (ids || []).map((id) => `<img src="${brawlerImg(id)}" alt="" loading="lazy" ${imgFallback}>`).join('');
            const rows = view.opps.map((opponent) => {
                const name = isBot(opponent[0]) ? `<span class="bcr-duel__name">${esc(opponent[1])}</span>` : `<a class="bcr-duel__name" href="${profileHref(opponent[0])}">${esc(opponent[1])}</a>`;
                return `<div class="bcr-duel__row">${name}${icons(opponent[2])}</div>`;
            });
            return `<div class="bcr-duel"><div class="bcr-duel__row is-me"><span class="bcr-duel__name">${esc(view.me[1])}</span>${icons(view.me[2])}</div>${rows.join('')}</div>`;
        }

        function panelHTML(view, selected) {
            if (view.k === 'd') return duelHTML(view);
            if (view.k === 'r') return ranksHTML(view);
            if (view.k === 'm') {
                const note = { aborted: T.noteAborted, old: T.noteOld, none: T.noteNone }[view.note];
                return rosterHTML(view) + (note ? `<p class="bcr-foot">${note}</p>` : '');
            }
            let html = '';
            if (view.games.length > 1) {
                const segment = (key, inner) => `<button type="button" data-bcr-game="${key}" class="${String(selected) === String(key) ? 'is-on' : ''}">${inner}</button>`;
                html += `<div class="bcr-games">${segment('all', T.total)}${view.games.map((game, i) => segment(i, `<span>${T.game(i + 1)}</span><span class="bcr-games__r bcr-games__r--${game.r}">${T.result[game.r] || ''}</span>`)).join('')}</div>`;
            }
            const game = gameOf(view, selected);
            return html + (game.s ? statsTableHTML(view, game) : rosterHTML(view) + `<p class="bcr-foot">${T.noteNone}</p>`);
        }

        // ---------- 開閉 ----------
        const viewOf = (card) => cards[card.dataset.bcr];
        function renderPanel(card) {
            const view = viewOf(card);
            if (!view) return;
            let selected = state.get(card);
            if (selected === undefined) {
                selected = view.k === 's' && view.games.length > 1 ? 'all' : 0;
                state.set(card, selected);
            }
            card.querySelector('.bcr-panel').innerHTML = panelHTML(view, selected);
            const meta = stripMetaHTML(view, selected);
            card.querySelector('.bcr-strip__meta').innerHTML = meta;
            // 帯に出すものが無いバトルは、開いても味方と敵の並びを残す
            card.classList.toggle('bcr-keep', !meta);
        }
        function setOpen(card, open) {
            if (!viewOf(card)) return;
            if (open && !card.querySelector('.bcr-panel').childElementCount) renderPanel(card);
            card.classList.toggle('is-open', open);
            const toggle = card.querySelector('.bcr-toggle');
            if (toggle) toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
            if (!open) requestAnimationFrame(() => fitStrip(card));
        }
        const allSwitch = root.querySelector('.bcr-all');
        function setAll(open, anchor, remember) {
            // 押した場所が動かないように、開閉の前後でスクロールを合わせる
            const before = anchor ? anchor.getBoundingClientRect().top : 0;
            root.classList.add('bcr--noanim');
            grid.querySelectorAll('.battle-card[data-bcr]').forEach((card) => setOpen(card, open));
            if (anchor) window.scrollBy(0, anchor.getBoundingClientRect().top - before);
            requestAnimationFrame(() => root.classList.remove('bcr--noanim'));
            if (allSwitch) allSwitch.setAttribute('aria-checked', open ? 'true' : 'false');
            if (remember) {
                try {
                    localStorage.setItem(OPEN_ALL_KEY, open ? '1' : '0');
                } catch (e) {
                    // 保存できない環境では、次回は閉じた状態から始まる
                }
            }
        }

        grid.addEventListener('click', (event) => {
            const toggle = event.target.closest('.bcr-toggle');
            if (toggle) {
                const card = toggle.closest('.battle-card');
                const open = !card.classList.contains('is-open');
                if (event.shiftKey) setAll(open, toggle, true);
                else setOpen(card, open);
                return;
            }
            const gameButton = event.target.closest('[data-bcr-game]');
            if (gameButton) {
                const card = gameButton.closest('.battle-card');
                state.set(card, gameButton.dataset.bcrGame === 'all' ? 'all' : Number(gameButton.dataset.bcrGame));
                renderPanel(card);
            }
        });
        if (allSwitch) {
            allSwitch.addEventListener('click', () => setAll(allSwitch.getAttribute('aria-checked') !== 'true', allSwitch, true));
        }

        // ---------- 勝敗ダイジェスト ----------
        const digest = root.querySelector('.battle-results-digest-box');
        if (digest) {
            if (!tipEl || !tipEl.isConnected) {
                tipEl = document.createElement('div');
                tipEl.className = 'bcr-tip';
                tipEl.setAttribute('role', 'tooltip');
                document.body.appendChild(tipEl);
            }
            const hideTip = () => tipEl.classList.remove('is-on');
            if (window.matchMedia('(hover: hover)').matches) {
                digest.addEventListener('mouseover', (event) => {
                    const tile = event.target.closest('.battle-results-digest');
                    const source = tile && tile.querySelector('.bcr-tip-src');
                    if (!source) return;
                    tipEl.innerHTML = source.innerHTML;
                    tipEl.classList.add('is-on');
                    const rect = tile.getBoundingClientRect();
                    const width = tipEl.offsetWidth, height = tipEl.offsetHeight;
                    tipEl.style.left = Math.min(window.innerWidth - width - 8, Math.max(8, rect.left + rect.width / 2 - width / 2)) + 'px';
                    tipEl.style.top = (rect.top - height - 8 < 8 ? rect.bottom + 8 : rect.top - height - 8) + 'px';
                });
                digest.addEventListener('mouseleave', hideTip);
            }
            digest.addEventListener('click', (event) => {
                const tile = event.target.closest('[data-battle-card-target]');
                const card = tile && document.getElementById(tile.dataset.battleCardTarget);
                hideTip();
                if (!card) return;
                // 移動先のカードの枠を一度光らせる（移動そのものはフラグメント側の処理）
                card.classList.remove('is-flash');
                void card.offsetWidth;
                card.classList.add('is-flash');
            });
        }

        // ---------- 初期化 ----------
        // 帯の詰め直し。フラグメントは隠れた状態で差し込まれることがあるので、大きさが決まったとき・変わったときに行う
        const fitAll = () => grid.querySelectorAll('.battle-card[data-bcr]:not(.is-open), .battle-card.bcr-keep').forEach(fitStrip);
        if (gridObserver) gridObserver.disconnect();
        let fitTimer = null;
        let lastWidth = -1;
        gridObserver = new ResizeObserver(() => {
            const width = grid.clientWidth;
            if (width === lastWidth) return; // 開閉で高さが変わっただけのときは何もしない
            lastWidth = width;
            clearTimeout(fitTimer);
            fitTimer = setTimeout(() => { if (grid.isConnected) fitAll(); }, 60);
        });
        gridObserver.observe(grid);
        fitAll();
        // 帯の中の画像（VS など）が読み込まれて幅が決まったら詰め直す
        grid.addEventListener('load', (event) => {
            const card = event.target.closest && event.target.closest('.bcr-strip') && event.target.closest('.battle-card');
            if (card && (!card.classList.contains('is-open') || card.classList.contains('bcr-keep'))) fitStrip(card);
        }, true);
        let openAll = false;
        try {
            openAll = localStorage.getItem(OPEN_ALL_KEY) === '1';
        } catch (e) {
            openAll = false;
        }
        if (openAll) setAll(true, null, false);
    };
})();
