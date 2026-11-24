// /app/static/js/hub.js
// 統計データタブ・ツールタブ（ハブ）。templates/components/hub.html・css/components/hub.css・services/hub_catalog.py。
// - サブタブ: リロードなしで切り替え、URL の ?sub と保存領域（Web はタブを閉じるまで、アプリは端末）に残す。左右の矢印キーでも移動。
//   スクロールしてから切り替えたら、そのサブタブの先頭へ戻す
// - 並び替え: サブタブごと。未ログインは端末（localStorage）、ログイン中はアカウント（端末にも写しを置く）。
//   初めてログインしたとき、アカウントに並びが無ければ端末の並びを引き継ぐ。広告は動かさない（読み込み直しになるため）
// - タイトル: 入るときは大きく。入らないときは --fit-min まで2行そろって小さくし、残りは横に縮める（長体）
// - ライブ表示: マップ周期（次の切り替えまでの残り時間。周期は手元で計算し、切り替わってもサーバーに問い合わせない）と
//   大会開催ツール（表示が変わる時刻・大会の通知・画面に戻ったときだけ取り直す）
(function () {
    'use strict';

    const MIN_SCALE = 0.72;     // 長体はここまで。それでも入らなければ文字を小さくする
    const STACK_AT = 0.9;       // NEW と説明の札を並べると説明の札がこれより縮むなら、2行に分ける
    const LONG_PRESS_MS = 280;  // タッチは少し長押ししてからつかむ（すぐ動かすとスクロール）
    const REFETCH_AWAY_MS = 60 * 1000;
    const HOUR_MS = 60 * 60 * 1000;

    const orderKey = (hub, sub) => `bi_hub_order_${hub}_${sub}`;
    // 端末の並びが、ログイン中に置いたアカウントの写しか（写しはアカウントへ引き継がない。「デフォルトに戻す」が元に戻るため）
    const mirrorKey = (hub) => `bi_hub_order_${hub}_from_account`;
    const noticeKey = (id) => `bi_hub_notice_${id}`;

    function lsGet(key) {
        try { return localStorage.getItem(key); } catch (e) { return null; }
    }
    function lsSet(key, value) {
        try {
            if (value === null) localStorage.removeItem(key);
            else localStorage.setItem(key, value);
        } catch (e) { /* 保存できなくても、この画面の間は使える */ }
    }
    function readOrder(hub, sub) {
        try {
            const value = JSON.parse(lsGet(orderKey(hub, sub)) || 'null');
            return Array.isArray(value) ? value.filter((id) => typeof id === 'string') : null;
        } catch (e) {
            return null;
        }
    }

    // 保存した並びを今の定義に合わせる（services/hub_catalog.py の merge_order と同じ）
    function mergeOrder(saved, defaults) {
        if (!saved || !saved.length) return defaults.slice();
        const result = [];
        saved.forEach((id) => { if (defaults.includes(id) && !result.includes(id)) result.push(id); });
        defaults.forEach((id, index) => {
            if (result.includes(id)) return;
            const before = defaults.slice(0, index).reverse().find((prev) => result.includes(prev));
            result.splice(before ? result.indexOf(before) + 1 : 0, 0, id);
        });
        return result;
    }

    // カードを ids の順に並べ直す。グリッドの中の広告（6個目のあと）は動かさず、カードのほうを前後へ動かす
    function applyOrder(grid, ids) {
        const cards = {};
        grid.querySelectorAll(':scope > .hub-card').forEach((el) => { cards[el.dataset.id] = el; });
        const ad = grid.querySelector(':scope > .bi-adsense');
        ids.forEach((id, index) => {
            const el = cards[id];
            if (!el) return;
            if (ad && index < 6) grid.insertBefore(el, ad);
            else grid.appendChild(el);
        });
    }
    function domOrder(grid) {
        return Array.from(grid.querySelectorAll(':scope > .hub-card')).map((el) => el.dataset.id);
    }

    function fmtLeft(ms, ja) {
        const s = Math.max(0, Math.floor(ms / 1000));
        const h = Math.floor(s / 3600);
        const m = Math.floor((s % 3600) / 60);
        const sec = s % 60;
        if (h > 0) return ja ? `${h}時間 ${m}分` : `${h}h ${m}m`;
        if (m > 0) return ja ? `${m}分 ${sec}秒` : `${m}m ${sec}s`;
        return ja ? `${sec}秒` : `${sec}s`;
    }

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    const TN_ROLES = { host: ['主催', 'Host'], cohost: ['副主催', 'Co-host'], entry: ['参加中', 'Joined'] };
    const TN_STATES = {
        match_checkin: ['試合のチェックイン', 'Match check-in', 'alert'],
        checkin: ['チェックイン受付中', 'Check-in open', 'alert'],
        live: ['開催中', 'Live', 'live'],
        checked_in: ['チェックイン済み', 'Checked in', ''],
        before: ['開始前', 'Upcoming', ''],
        entry: ['エントリー受付中', 'Entries open', ''],
    };

    document.addEventListener('alpine:init', () => {
        Alpine.data('hubPage', () => ({
            sub: 'all',
            editing: false,
            editList: [],
            pillStyle: '',
            noticeClosed: false,
            touch: window.matchMedia('(hover: none)').matches,

            init() {
                const root = this.$root;
                this.data = JSON.parse(document.getElementById('hub-data').textContent);
                this.ja = root.dataset.lang === 'ja';
                this.hub = this.data.hub;
                this.ids = this.data.subtabs;
                this.storage = root.dataset.platform === 'web' ? window.sessionStorage : window.localStorage;
                this.storageKey = root.dataset.storageKey;
                this.orders = {};

                const fromUrl = new URLSearchParams(location.search).get('sub');
                let saved = null;
                try { saved = this.storage.getItem(this.storageKey); } catch (e) { /* 無視 */ }
                this.sub = this.ids.includes(fromUrl) ? fromUrl : (this.ids.includes(saved) ? saved : this.ids[0]);
                this.persistSub();

                this.loadOrders();

                const notice = root.querySelector('[data-notice]');
                this.noticeClosed = !!(notice && lsGet(noticeKey(notice.dataset.notice)) === '1');

                // タッチで並び替えている間は、ページのスクロールを止める
                this.$refs.editList.addEventListener('touchmove', (e) => {
                    if (document.documentElement.classList.contains('is-drag-sorting')) e.preventDefault();
                }, { passive: false });

                const refit = () => requestAnimationFrame(() => { this.fitAll(); this.movePill(); });
                if ('ResizeObserver' in window) new ResizeObserver(refit).observe(root);
                else window.addEventListener('resize', refit);
                if (document.fonts && document.fonts.ready) document.fonts.ready.then(refit);
                this.$nextTick(() => {
                    refit();
                    requestAnimationFrame(() => { if (this.$refs.tabs) this.$refs.tabs.querySelector('.hub-tabs__pill').classList.add('is-ready'); });
                });

                this.initRotation();
                this.initTournament();
            },

            // ---- サブタブ ----
            persistSub() {
                try { this.storage.setItem(this.storageKey, this.sub); } catch (e) { /* 無視 */ }
                const url = new URL(window.location.href);
                if (url.searchParams.get('sub') === this.sub) return;
                url.searchParams.set('sub', this.sub);
                window.history.replaceState(window.history.state, '', url);
            },
            setSub(id) {
                if (!this.ids.includes(id) || id === this.sub) return;
                this.sub = id;
                this.persistSub();
                if (this.editing) this.editList = this.editItems(id);
                this.$nextTick(() => {
                    this.movePill();
                    this.fitAll();
                    // スクロールしていれば、このサブタブの先頭（貼り付いたタブのすぐ下）へ戻す
                    const anchor = this.$refs.tabsAnchor;
                    const stickyTop = parseFloat(getComputedStyle(this.$refs.tabs).top) || 0;
                    const target = anchor.getBoundingClientRect().top + window.scrollY - stickyTop;
                    if (window.scrollY > target + 1) window.scrollTo({ top: target, behavior: 'auto' });
                });
            },
            handleKeydown(e) {
                if (e.metaKey || e.ctrlKey || e.altKey) return;
                if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
                const active = document.activeElement;
                if (active && (active.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(active.tagName))) return;
                e.preventDefault();
                const index = Math.max(0, this.ids.indexOf(this.sub));
                const next = e.key === 'ArrowRight' ? (index + 1) % this.ids.length : (index - 1 + this.ids.length) % this.ids.length;
                this.setSub(this.ids[next]);
            },
            movePill() {
                const tab = this.$refs.tabs && this.$refs.tabs.querySelector(`[data-tab="${this.sub}"]`);
                if (!tab) return;
                this.pillStyle = `transform:translate(${tab.offsetLeft}px, ${tab.offsetTop}px);width:${tab.offsetWidth}px;height:${tab.offsetHeight}px`;
            },
            closeNotice() {
                this.noticeClosed = true;
                const notice = this.$root.querySelector('[data-notice]');
                if (notice) lsSet(noticeKey(notice.dataset.notice), '1');
            },

            // ---- 並び順 ----
            grid(sub) {
                return this.$root.querySelector(`.hub-grid[data-grid="${sub}"]`);
            },
            loadOrders() {
                const local = {};
                this.ids.forEach((sub) => {
                    const saved = readOrder(this.hub, sub);
                    if (saved) local[sub] = mergeOrder(saved, this.data.defaults[sub]);
                });
                // 未ログイン: 端末の並び。ログイン中でアカウントに並びが無い: 端末の並びを引き継ぐ（アカウントへ送る）。
                // ただし端末の並びがアカウントの写しなら引き継がない（別の端末で「デフォルトに戻す」をしたあとに、元に戻さないため）
                const mirrored = lsGet(mirrorKey(this.hub)) === '1';
                const useLocal = !this.data.loggedIn || (!this.data.hasAccountOrders && !mirrored);
                const upload = {};
                this.ids.forEach((sub) => {
                    const grid = this.grid(sub);
                    if (!grid) return;
                    if (useLocal && local[sub]) {
                        applyOrder(grid, local[sub]);
                        if (this.data.loggedIn && this.isCustomOrder(sub, local[sub])) upload[sub] = local[sub];
                    }
                    this.orders[sub] = domOrder(grid);
                    // ログイン中は、アカウントの並びの写しを端末にも置く（ログアウトしても同じ並びのまま）
                    if (this.data.loggedIn && !useLocal) this.storeLocal(sub, this.orders[sub]);
                });
                if (this.data.loggedIn) lsSet(mirrorKey(this.hub), '1');
                if (Object.keys(upload).length) {
                    this.data.hasAccountOrders = true;
                    this.sendOrders(upload);
                }
            },
            isCustomOrder(sub, ids) {
                return ids.join(',') !== this.data.defaults[sub].join(',');
            },
            get isCustom() {
                return this.isCustomOrder(this.sub, this.editList.map((item) => item.id));
            },
            storeLocal(sub, ids) {
                lsSet(orderKey(this.hub, sub), this.isCustomOrder(sub, ids) ? JSON.stringify(ids) : null);
            },
            sendOrders(orders) {
                if (!this.data.loggedIn || !this.data.saveUrl) return;
                fetch(this.data.saveUrl, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ hub: this.hub, orders }),
                    credentials: 'same-origin',
                }).catch(() => { /* 端末には保存済み。次に開いたときにも端末の並びのまま */ });
            },
            saveOrder(sub, ids) {
                const grid = this.grid(sub);
                if (grid) applyOrder(grid, ids);
                this.orders[sub] = ids.slice();
                this.storeLocal(sub, ids);
                // 未ログインで並べ替えた端末の並びは、次にログインしたときにアカウントへ引き継ぐ
                lsSet(mirrorKey(this.hub), this.data.loggedIn ? '1' : null);
                this.sendOrders({ [sub]: this.isCustomOrder(sub, ids) ? ids : null });
            },
            editItems(sub) {
                const items = {};
                (this.data.edit[sub] || []).forEach((item) => { items[item.id] = item; });
                return (this.orders[sub] || []).map((id) => items[id]).filter(Boolean);
            },
            startEdit() {
                this.editList = this.editItems(this.sub);
                this.editStart = this.editList.map((item) => item.id).join(',');
                this.editing = true;
            },
            finishEdit() {
                const ids = this.editList.map((item) => item.id);
                if (ids.join(',') !== this.editStart) this.saveOrder(this.sub, ids);
                this.editing = false;
                this.$nextTick(() => requestAnimationFrame(() => this.fitAll()));
            },
            onReorder() { /* 並びは「完了」で保存する */ },
            dragFrom(e) {
                biDragSort.start(e, this.editList, () => this.onReorder());
            },
            // 行のどこからでもつかめる。マウスはすぐ、タッチは動かさずに少し押し続けたら（それまでに指が動いたらスクロール）
            rowDown(e) {
                if (e.button !== 0) return;
                const row = e.currentTarget;
                if (e.pointerType === 'mouse') { this.dragFrom(e); return; }
                const x0 = e.clientX;
                const y0 = e.clientY;
                let lastY = e.clientY;
                row.classList.add('is-pressing');
                const stop = () => {
                    clearTimeout(timer);
                    row.classList.remove('is-pressing');
                    row.removeEventListener('pointermove', move);
                    row.removeEventListener('pointerup', stop);
                    row.removeEventListener('pointercancel', stop);
                };
                const move = (ev) => {
                    lastY = ev.clientY;
                    if (Math.hypot(ev.clientX - x0, ev.clientY - y0) > 8) stop();
                };
                const timer = setTimeout(() => {
                    stop();
                    if (navigator.vibrate) navigator.vibrate(8);
                    this.dragFrom({ button: 0, currentTarget: row, preventDefault() {}, pointerId: e.pointerId, pointerType: e.pointerType, clientY: lastY });
                }, LONG_PRESS_MS);
                row.addEventListener('pointermove', move);
                row.addEventListener('pointerup', stop);
                row.addEventListener('pointercancel', stop);
            },
            resetOrder() {
                if (!window.confirm(this.ja ? 'コンテンツをデフォルトの並び順に戻しますか？' : 'Restore the default order of the contents?')) return;
                const defaults = this.data.defaults[this.sub].slice();
                this.saveOrder(this.sub, defaults);
                this.editList = this.editItems(this.sub);
                this.editStart = defaults.join(',');
            },

            // ---- タイトルの大きさ・長体 ----
            // 書き込み→読み取りが交互にならないよう、まとめて戻す→まとめて測る→まとめて当てる。表示中のサブタブだけ
            fitAll() {
                const panel = this.$root.querySelector(`.hub-panel[data-sub="${this.sub}"]`);
                if (!panel) return;
                const tops = Array.from(panel.querySelectorAll('.hub-card__top'));
                const titles = Array.from(panel.querySelectorAll('.hub-card__title'));
                const fits = Array.from(panel.querySelectorAll('.hub-fit'));
                tops.forEach((node) => node.classList.remove('is-stacked'));
                titles.forEach((node) => { node.style.fontSize = ''; });
                fits.forEach((node) => { node.style.transform = ''; node.style.fontSize = ''; });

                // NEW と説明の札: 並べると説明の札を STACK_AT より縮めることになるなら2行に分ける
                const stack = tops.map((top) => {
                    const tag = top.querySelector('.hub-card__tag');
                    const badge = top.querySelector('.hub-card__badge');
                    if (!tag || !badge || !tag.offsetWidth || !badge.offsetWidth) return false;
                    const fit = tag.querySelector('.hub-fit');
                    const cs = getComputedStyle(tag);
                    const need = badge.offsetWidth + (parseFloat(getComputedStyle(top).columnGap) || 0)
                        + fit.offsetWidth + parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight);
                    return top.clientWidth / need < STACK_AT;
                });
                tops.forEach((top, i) => { if (stack[i]) top.classList.add('is-stacked'); });

                const plans = titles.map((title) => {
                    const lines = Array.from(title.querySelectorAll('.hub-fit'));
                    const avail = title.clientWidth;
                    if (!avail || !lines.length) return null;
                    const cs = getComputedStyle(title);
                    return {
                        title, lines, avail,
                        widths: lines.map((line) => line.offsetWidth),
                        fs: parseFloat(cs.fontSize),
                        minRatio: parseFloat(cs.getPropertyValue('--fit-min')) || 1,
                    };
                });
                const others = fits.filter((node) => !node.closest('.hub-card__title')).map((node) => {
                    const box = node.parentElement;
                    const cs = getComputedStyle(box);
                    return {
                        node,
                        avail: box.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight),
                        width: node.offsetWidth,
                        fs: parseFloat(getComputedStyle(node).fontSize),
                    };
                });

                const condense = (node, scale, fs) => {
                    if (scale >= 0.999) return;
                    if (scale < MIN_SCALE) {
                        node.style.fontSize = `${fs * (scale / MIN_SCALE)}px`;
                        scale = MIN_SCALE;
                    }
                    node.style.transform = `scaleX(${scale.toFixed(3)})`;
                };
                plans.forEach((p) => {
                    if (!p) return;
                    const need = Math.min(...p.widths.map((w) => (w ? p.avail / w : 1)));
                    let fs = p.fs;
                    if (need < 1) {
                        fs = Math.max(p.fs * p.minRatio, p.fs * need);
                        if (fs < p.fs - 0.1) p.title.style.fontSize = `${fs.toFixed(2)}px`;
                    }
                    p.lines.forEach((line, i) => {
                        const w = p.widths[i] * (fs / p.fs);
                        if (w > p.avail + 0.5) condense(line, p.avail / w, fs);
                    });
                });
                others.forEach((p) => {
                    if (p.avail && p.width > p.avail + 0.5) condense(p.node, p.avail / p.width, p.fs);
                });
            },

            // ---- マップ周期: 次の切り替えまでの残り時間 ----
            initRotation() {
                const rotation = this.data.rotation;
                const strips = this.$root.querySelectorAll('.hub-strip[data-live="rotation"]');
                if (!strips.length || !rotation || !rotation.slots || !rotation.slots.length) return;
                const render = () => {
                    const next = this.nextRotation();
                    if (!next) return;
                    this.rotationAt = next.t;
                    strips.forEach((strip) => {
                        const modes = el('span', 'hub-s-modes');
                        next.modes.forEach((key) => {
                            const paths = rotation.icons[key];
                            if (!paths) return;
                            const img = el('img');
                            img.alt = '';
                            img.src = paths[0];
                            if (paths[1]) img.addEventListener('error', () => { if (img.src !== paths[1]) img.src = paths[1]; }, { once: true });
                            modes.appendChild(img);
                        });
                        const time = el('span', 'hub-s-time', fmtLeft(next.t - Date.now(), this.ja));
                        time.dataset.rotationTime = '';
                        time.classList.toggle('is-ticking', next.t - Date.now() < HOUR_MS);
                        strip.replaceChildren(modes, el('span', 'hub-s-text', this.ja ? '次のマップまで：' : 'Next map in:'), time);
                    });
                };
                render();
                setInterval(() => {
                    if (Date.now() >= this.rotationAt) { render(); return; }
                    const left = this.rotationAt - Date.now();
                    const text = fmtLeft(left, this.ja);
                    strips.forEach((strip) => {
                        const time = strip.querySelector('[data-rotation-time]');
                        if (!time) return;
                        if (time.textContent !== text) time.textContent = text;
                        time.classList.toggle('is-ticking', left < HOUR_MS);
                    });
                }, 1000);
            },
            // スロットごとに、今の枠の終わり（＝次の切り替え）と次のマップのモードを出し、いちばん近いもの（同時ならまとめて）
            nextRotation() {
                const now = Date.now();
                const list = this.data.rotation.slots.map((slot) => {
                    const period = slot.d * 60 * 1000;
                    const steps = Math.floor((now - slot.o) / period);
                    const t = slot.o + (steps + 1) * period;
                    const length = slot.m.length || 1;
                    const index = ((slot.i + steps + 1) % length + length) % length;
                    const mode = slot.m[index];
                    return { t, modes: mode ? [mode] : slot.f };
                });
                if (!list.length) return null;
                const t = Math.min(...list.map((x) => x.t));
                const modes = [];
                list.filter((x) => x.t - t < 60 * 1000).forEach((x) => x.modes.forEach((m) => { if (!modes.includes(m)) modes.push(m); }));
                return { t, modes };
            },

            // ---- 大会開催ツール ----
            initTournament() {
                const tournament = this.data.tournament;
                this.tnStrips = this.$root.querySelectorAll('.hub-strip[data-live="tournament"]');
                if (!this.tnStrips.length || !tournament) return;
                this.renderTournament(tournament.status);
                this.tnFetchedAt = Date.now();
                // 大会の通知が来たとき（試合のチェックインが始まったなど）に取り直す（tournaments/notify.js）
                window.addEventListener('tn-notify', () => {
                    clearTimeout(this.tnNotifyTimer);
                    this.tnNotifyTimer = setTimeout(() => this.refreshTournament(), 1500);
                });
                // しばらく別の画面にいて戻ったとき
                document.addEventListener('visibilitychange', () => {
                    if (!document.hidden && Date.now() - this.tnFetchedAt > REFETCH_AWAY_MS) this.refreshTournament();
                });
            },
            renderTournament(status) {
                if (!status) return;
                this.tnStrips.forEach((strip) => {
                    const nodes = [];
                    if (status.kind === 'mine') {
                        const role = TN_ROLES[status.role] || TN_ROLES.entry;
                        const state = TN_STATES[status.state] || TN_STATES.before;
                        nodes.push(el('span', 'hub-s-role', role[this.ja ? 0 : 1]));
                        nodes.push(el('span', 'hub-s-text', status.title || ''));
                        nodes.push(el('span', `hub-s-state${state[2] ? ` hub-s-state--${state[2]}` : ''}`, state[this.ja ? 0 : 1]));
                    } else if (status.open || status.live) {
                        const text = el('span', 'hub-s-text');
                        const part = (label, n, unit) => {
                            text.append(label + ' ');
                            text.append(el('b', '', String(n)));
                            if (unit) text.append(unit);
                        };
                        part(this.ja ? '参加受付中' : 'Open', status.open || 0, this.ja ? '件' : '');
                        text.append(' · ');
                        part(this.ja ? '開催中' : 'Live', status.live || 0, this.ja ? '件' : '');
                        nodes.push(el('span', 'hub-s-dot'), text);
                    }
                    strip.replaceChildren(...nodes);
                });
                // 表示が変わる時刻を過ぎたら取り直す（同時に開いている人が集まらないよう、少しずらす）
                clearTimeout(this.tnTimer);
                if (status.until) {
                    const wait = Date.parse(status.until) - Date.now() + 2000 + Math.random() * 3000;
                    if (wait > 0 && wait < 6 * 60 * 60 * 1000) this.tnTimer = setTimeout(() => this.refreshTournament(), wait);
                }
            },
            async refreshTournament() {
                const api = this.data.tournament && this.data.tournament.api;
                if (!api || this.tnLoading) return;
                this.tnLoading = true;
                try {
                    const response = await fetch(api, { credentials: 'same-origin' });
                    if (!response.ok) return;
                    const data = await response.json();
                    this.tnFetchedAt = Date.now();
                    if (data && data.status) this.renderTournament(data.status);
                } catch (e) {
                    // 取り直せなくても、今の表示のまま使える
                } finally {
                    this.tnLoading = false;
                }
            },
        }));
    });
})();
