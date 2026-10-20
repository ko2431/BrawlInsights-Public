// /app/static/js/reactions.js
// チャットのリアクション（掲示板のチャット・大会のチャット・大会のお知らせ）で共通の部品。window.BIReactions として使う。
// - 付いているリアクションを押すと、その絵文字を付ける・外す。長押し・右クリックでリアクションした人の一覧（「他」は押しても一覧）
// - 押した瞬間に表示を変え、送るのは最後に押してから少し待ってから（連打しても、送った状態との差分だけを送る）
// - 数が変わると、増えたら上へ・減ったら下へ数字が入れ替わる。入りきらない絵文字は「他」にまとめる
// - メニューの絵文字は2段。上段は最近使った6つと全絵文字のピッカー、下段は固定の7つ。最近使ったものは端末ごと（localStorage）
// - ピッカー（emoji-picker-element）は初めて開くときに読み込む。絵文字データは自前（サーバーが受け付けるものと同じ）
// - 1つのメッセージの絵文字は MAX_KINDS 種類まで（サーバーの reaction_rules.MAX_KINDS_PER_MESSAGE と同じ）
(function () {
    'use strict';
    if (window.BIReactions) return;

    const PRESETS = ['❤️', '👀', '😄', '😲', '🥲', '😭', '😡'];
    // 👍 は絵文字データの表記に合わせて異体字セレクタ（U+FE0F）付き
    const DEFAULT_RECENT = ['👍️', '🤲', '🥳', '🧐', '😪', '😨'];
    const RECENT_SIZE = 6;
    const MAX_KINDS = 10;
    const RECENT_KEY = 'biReactionRecent';
    const SEND_DELAY_MS = 400;
    const HOLD_MS = 450;
    const HOLD_MOVE_PX = 10;
    const CHIP_SELECTOR = '[data-react-chip]';

    const script = document.currentScript;
    const scriptBase = script ? new URL('.', script.src) : new URL('/static/js/', location.href);
    const dataVersion = (script && script.dataset.emojiDataVersion) || '0';
    const lang = document.documentElement.lang === 'en' ? 'en' : 'ja';
    const ja = lang === 'ja';

    const TEXTS = ja ? {
        rate_limited: 'リアクションの操作が多すぎます。少し時間をおいてからもう一度お試しください。',
        too_many_kinds: `このメッセージには、これ以上の種類のリアクションを付けられません（${MAX_KINDS}種類まで）。`,
        invalid_emoji: 'このリアクションは使えません。',
        network: '通信に失敗しました。もう一度お試しください。',
        failed: 'リアクションできませんでした。',
        more: '他',
        picker: 'ほかの絵文字',
        pickerTitle: '絵文字を選ぶ',
        close: '閉じる',
        loading: '読み込み中...',
        loadFailed: '絵文字を読み込めませんでした。',
    } : {
        rate_limited: 'Too many reactions. Please wait a moment and try again.',
        too_many_kinds: `This message can't take more kinds of reactions (up to ${MAX_KINDS}).`,
        invalid_emoji: 'This reaction is not available.',
        network: 'Connection failed. Please try again.',
        failed: 'Could not react.',
        more: '+',
        picker: 'More emojis',
        pickerTitle: 'Pick an emoji',
        close: 'Close',
        loading: 'Loading...',
        loadFailed: "Couldn't load emojis.",
    };
    const MORE_ICON = '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" aria-hidden="true"><circle cx="12" cy="12" r="9.25" stroke="currentColor" stroke-width="1.6"/><circle cx="7.9" cy="12" r="1.35" fill="currentColor"/><circle cx="12" cy="12" r="1.35" fill="currentColor"/><circle cx="16.1" cy="12" r="1.35" fill="currentColor"/></svg>';
    const CLOSE_ICON = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" aria-hidden="true"><path d="M18 6L6 18M6 6l12 12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>';

    // --- 最近使った絵文字（固定の7つは入れない。足りないぶんは初期値で埋める） ---
    function readRecent() {
        try {
            const value = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]');
            return Array.isArray(value) ? value.filter((e) => typeof e === 'string') : [];
        } catch (e) {
            return [];
        }
    }
    function recent() {
        const list = [];
        [...readRecent(), ...DEFAULT_RECENT].forEach((e) => {
            if (list.length < RECENT_SIZE && !PRESETS.includes(e) && !list.includes(e)) list.push(e);
        });
        return list;
    }
    function remember(emoji) {
        if (!emoji || PRESETS.includes(emoji)) return;
        const next = [emoji, ...readRecent().filter((e) => e !== emoji)].slice(0, RECENT_SIZE);
        try { localStorage.setItem(RECENT_KEY, JSON.stringify(next)); } catch (e) { /* 保存できない環境では初期値のまま */ }
    }
    function palette() {
        return { top: recent(), bottom: PRESETS.slice() };
    }

    // --- 集計（絵文字は、その絵文字が最初に付いた順。送信前の自分のリアクションは id が負で、いちばん後に付いたものとして扱う） ---
    let tempSeq = 0;
    const tempId = () => -(++tempSeq);
    const sortKey = (r) => (r.id > 0 ? r.id : 1e15 - r.id);
    function summarize(reactions, myUserId) {
        const map = new Map();
        [...(reactions || [])].sort((a, b) => sortKey(a) - sortKey(b)).forEach((r) => {
            let item = map.get(r.emoji);
            if (!item) {
                item = { emoji: r.emoji, count: 0, mine: false };
                map.set(r.emoji, item);
            }
            item.count += 1;
            if (myUserId && r.user_id === myUserId) item.mine = true;
        });
        return [...map.values()];
    }
    // リアクションした人の一覧（絵文字ごとにまとめ、絵文字の間に区切り線）
    function reactors(reactions) {
        const list = reactions || [];
        const rank = new Map(summarize(list).map((item, i) => [item.emoji, i]));
        const sorted = [...list].sort((a, b) => rank.get(a.emoji) - rank.get(b.emoji) || sortKey(a) - sortKey(b));
        return sorted.map((r, i) => ({ ...r, sep: i > 0 && sorted[i - 1].emoji !== r.emoji }));
    }
    function hasMine(reactions, emoji, myUserId) {
        return !!myUserId && (reactions || []).some((r) => r.emoji === emoji && r.user_id === myUserId);
    }
    // まだ付いていない種類は、上限に達していたら付けられない
    function canAdd(reactions, emoji) {
        const list = reactions || [];
        return list.some((r) => r.emoji === emoji) || new Set(list.map((r) => r.emoji)).size < MAX_KINDS;
    }
    // 自分のリアクションを付けた・外した状態にした新しい配列（付けるときに reaction がなければ送信前のものを作る）
    function withMine(reactions, emoji, myUserId, active, reaction) {
        const list = reactions || [];
        const exists = list.some((r) => r.emoji === emoji && r.user_id === myUserId);
        if (active) {
            if (exists && !reaction) return list;
            const others = list.filter((r) => !(r.emoji === emoji && r.user_id === myUserId));
            return [...others, reaction || { id: tempId(), user_id: myUserId, emoji }];
        }
        return exists ? list.filter((r) => !(r.emoji === emoji && r.user_id === myUserId)) : list;
    }
    function errorText(code, message) {
        return TEXTS[code] || message || TEXTS.failed;
    }

    // --- 送信（押すたびに set() を呼ぶ。最後に押してから少し待って、送った状態と違えば送る） ---
    // send(messageId, emoji, active) は { ok, message } を返す。失敗したら onFail で送った状態に戻してもらう
    function createSender({ send, onFail }) {
        const entries = new Map();
        const keyOf = (messageId, emoji) => `${messageId}|${emoji}`;
        function schedule(key, delay) {
            const entry = entries.get(key);
            clearTimeout(entry.timer);
            entry.timer = setTimeout(() => flush(key), delay);
        }
        function flush(key) {
            const entry = entries.get(key);
            if (!entry || entry.busy) return;
            clearTimeout(entry.timer);
            if (entry.wanted === entry.sent) {
                entries.delete(key);
                return;
            }
            entry.busy = true;
            const active = entry.wanted;
            Promise.resolve()
                .then(() => send(entry.messageId, entry.emoji, active))
                .catch(() => ({ ok: false, message: TEXTS.network }))
                .then((result) => {
                    entry.busy = false;
                    if (!result || !result.ok) {
                        entries.delete(key);
                        onFail(entry.messageId, entry.emoji, entry.sent, (result && result.message) || TEXTS.failed);
                        return;
                    }
                    entry.sent = active;
                    if (entry.wanted !== entry.sent) schedule(key, SEND_DELAY_MS);
                    else entries.delete(key);
                });
        }
        // 画面を離れるときは待たずに送る
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) [...entries.keys()].forEach(flush);
        });
        return {
            set(messageId, emoji, active, before) {
                const key = keyOf(messageId, emoji);
                let entry = entries.get(key);
                if (!entry) {
                    entry = { messageId, emoji, sent: before, wanted: active, busy: false, timer: null };
                    entries.set(key, entry);
                }
                entry.wanted = active;
                if (!entry.busy) schedule(key, SEND_DELAY_MS);
            },
            // 送信待ち・送信中（この間に届く自分のリアクションの配信は、表示に反映しない）
            pending(messageId, emoji) { return entries.has(keyOf(messageId, emoji)); },
        };
    }

    // --- 数字（増えたら上へ、減ったら下へ入れ替わる。初めて描くときは動かさない） ---
    function setCount(el, count) {
        if (!el) return;
        const next = String(count);
        const prev = el.dataset.count;
        if (prev === next) return;
        el.dataset.count = next;
        el.classList.add('bi-react-count');
        if (prev == null || prev === '' || !el.isConnected || reducedMotion()) {
            el.textContent = next;
            return;
        }
        const up = Number(next) > Number(prev);
        el.classList.remove('is-up', 'is-down');
        el.innerHTML = '';
        const incoming = document.createElement('span');
        incoming.className = 'bi-react-count__in';
        incoming.textContent = next;
        const outgoing = document.createElement('span');
        outgoing.className = 'bi-react-count__out';
        outgoing.setAttribute('aria-hidden', 'true');
        outgoing.textContent = prev;
        el.append(incoming, outgoing);
        void el.offsetWidth; // 続けて変わったときも、アニメーションを最初からやり直す
        el.classList.add(up ? 'is-up' : 'is-down');
        outgoing.addEventListener('animationend', () => outgoing.remove(), { once: true });
    }
    function reducedMotion() {
        return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    }
    // 表示中の一覧に、あとから加わった絵文字だけ弾むように出す（最初に描いたときは動かさない）
    function chipMounted(el) {
        const container = el.parentElement;
        if (!container || container.dataset.reactReady !== '1' || reducedMotion()) return;
        el.classList.add('is-new');
        el.addEventListener('animationend', () => el.classList.remove('is-new'), { once: true });
    }

    // --- 入りきらない絵文字を「他」にまとめる ---
    // 子要素: data-react-chip data-emoji data-count（絵文字ごと）と、data-react-chip data-more（「他」。中の [data-more-count] に数）
    const fitWidths = new WeakMap();
    let fitObserver = null;
    function fit(container, { observe = false } = {}) {
        if (!container) return;
        const children = [...container.children];
        const chips = children.filter((el) => el.dataset.emoji != null);
        const more = children.find((el) => el.dataset.more != null);
        chips.forEach((el) => el.classList.remove('is-hidden'));
        if (more) more.classList.add('is-hidden');
        container.dataset.reactReady = '1';
        if (observe) observeFit(container);
        const avail = container.getBoundingClientRect().width;
        if (!more || !chips.length || !avail) return rememberWidth(container);
        const gap = parseFloat(getComputedStyle(container).columnGap) || 0;
        const widths = chips.map(naturalWidth);
        const total = widths.reduce((sum, w) => sum + w, 0) + gap * (chips.length - 1);
        if (total <= avail + 1) return rememberWidth(container);
        const counts = chips.map((el) => Number(el.dataset.count) || 0);
        const moreCount = more.querySelector('[data-more-count]');
        const setMore = (n) => { if (moreCount) moreCount.textContent = String(n); };
        more.classList.remove('is-hidden');
        setMore(counts.reduce((sum, n) => sum + n, 0)); // いちばん大きい数で幅を測る
        const moreWidth = naturalWidth(more);
        let used = 0;
        let visible = 0;
        while (visible < chips.length && used + widths[visible] + gap + moreWidth <= avail + 1) {
            used += widths[visible] + gap;
            visible += 1;
        }
        let hidden = 0;
        chips.forEach((el, i) => {
            if (i < visible) return;
            el.classList.add('is-hidden');
            hidden += counts[i];
        });
        setMore(hidden);
        return rememberWidth(container);
    }
    // 小数まで測る（offsetWidth は丸められて、ちょうど収まるときに溢れたことになる）。
    // 弾んで出ている途中・押している間は拡大・縮小して見えるので、その倍率を戻した幅
    function naturalWidth(el) {
        const width = el.getBoundingClientRect().width;
        const transform = getComputedStyle(el).transform;
        if (!transform || transform === 'none' || typeof DOMMatrixReadOnly === 'undefined') return width;
        const scale = new DOMMatrixReadOnly(transform).a;
        return scale > 0 ? width / scale : el.offsetWidth;
    }
    function rememberWidth(container) {
        fitWidths.set(container, container.clientWidth);
    }
    // 幅が変わったら（画面の回転・隠れていたタブを開いたときなど）まとめ直す
    function observeFit(container) {
        if (typeof ResizeObserver === 'undefined') return;
        if (!fitObserver) {
            fitObserver = new ResizeObserver((entries) => {
                entries.forEach((entry) => {
                    const el = entry.target;
                    if (!el.isConnected) {
                        fitObserver.unobserve(el);
                        return;
                    }
                    if (fitWidths.get(el) !== el.clientWidth) fit(el);
                });
            });
        }
        if (container.dataset.reactObserved) return;
        container.dataset.reactObserved = '1';
        fitObserver.observe(container);
    }

    // --- 掲示板のチャット用: 集計を描く（絵文字ごとの要素は使い回し、数だけを入れ替える） ---
    // classes: { chip, emoji, count, more, moreLabel }
    function renderChips(container, items, classes) {
        const existing = new Map();
        let more = null;
        [...container.children].forEach((el) => {
            if (el.dataset.emoji != null) existing.set(el.dataset.emoji, el);
            else if (el.dataset.more != null) more = el;
            else el.remove();
        });
        const ready = container.dataset.reactReady === '1';
        items.forEach((item, i) => {
            let chip = existing.get(item.emoji);
            if (chip) {
                existing.delete(item.emoji);
            } else {
                chip = document.createElement('span');
                chip.className = `${classes.chip} bi-react-chip`;
                chip.setAttribute('data-react-chip', '');
                chip.dataset.emoji = item.emoji;
                const emojiEl = document.createElement('span');
                emojiEl.className = classes.emoji;
                emojiEl.textContent = item.emoji;
                const countEl = document.createElement('span');
                countEl.className = `${classes.count} bi-react-count`;
                chip.append(emojiEl, countEl);
                if (ready && !reducedMotion()) {
                    chip.classList.add('is-new');
                    chip.addEventListener('animationend', () => chip.classList.remove('is-new'), { once: true });
                }
            }
            chip.dataset.count = String(item.count);
            chip.classList.toggle('is-mine', !!item.mine);
            setCount(chip.querySelector('.bi-react-count'), item.count);
            if (container.children[i] !== chip) container.insertBefore(chip, container.children[i] || null);
        });
        existing.forEach((el) => el.remove());
        if (!more) {
            more = document.createElement('span');
            more.className = `${classes.chip} ${classes.more} bi-react-chip is-hidden`;
            more.setAttribute('data-react-chip', '');
            more.dataset.more = '';
            more.innerHTML = `<span class="${classes.moreLabel}">${TEXTS.more}</span><span class="${classes.count}" data-more-count></span>`;
        }
        container.appendChild(more);
    }

    // --- 押したとき（tap）・長押し/右クリック（hold）。「他」は押しても hold ---
    function bindPress(root, { tap, hold }) {
        let timer = null;
        let start = null;
        let heldThisPress = false;
        let suppressClick = false;
        const chipOf = (event) => {
            const el = event.target && event.target.closest ? event.target.closest(CHIP_SELECTOR) : null;
            return el && root.contains(el) ? el : null;
        };
        const clear = () => {
            clearTimeout(timer);
            timer = null;
        };
        root.addEventListener('pointerdown', (event) => {
            heldThisPress = false;
            suppressClick = false;
            clear();
            if (event.button !== 0) return;
            const chip = chipOf(event);
            if (!chip) return;
            start = { x: event.clientX, y: event.clientY };
            timer = setTimeout(() => {
                timer = null;
                heldThisPress = true;
                suppressClick = true;
                hold(chip, event);
            }, HOLD_MS);
        });
        root.addEventListener('pointermove', (event) => {
            if (!timer || !start) return;
            if (Math.abs(event.clientX - start.x) > HOLD_MOVE_PX || Math.abs(event.clientY - start.y) > HOLD_MOVE_PX) clear();
        }, { passive: true });
        root.addEventListener('pointerup', clear, { passive: true });
        root.addEventListener('pointercancel', clear, { passive: true });
        root.addEventListener('click', (event) => {
            const chip = chipOf(event);
            if (!chip) return;
            event.preventDefault();
            if (suppressClick) {
                suppressClick = false; // 長押しのあとに届くクリック
                return;
            }
            if (chip.dataset.more != null) hold(chip, event);
            else tap(chip, event);
        });
        root.addEventListener('contextmenu', (event) => {
            const chip = chipOf(event);
            if (!chip) return;
            event.preventDefault();
            if (heldThisPress) return; // 長押しで開いたあとに届く（Android）
            clear();
            hold(chip, event);
        });
    }

    // --- 全絵文字のピッカー（スマホは下から出るシート、PCは画面の中央） ---
    let pickerModule = null;
    let ui = null;
    const vendorUrl = (path) => new URL(`vendor/emoji-picker-element/${path}`, scriptBase).href;
    const isDark = () => document.documentElement.classList.contains('dark');
    function loadPicker() {
        if (!pickerModule) {
            pickerModule = Promise.all([
                import(vendorUrl('picker.js')),
                ja ? import(vendorUrl('i18n/ja.js')) : Promise.resolve(null),
            ]).then(([mod, i18n]) => ({ Picker: mod.default, i18n: i18n ? i18n.default : undefined }))
                .catch((e) => {
                    pickerModule = null;
                    throw e;
                });
        }
        return pickerModule;
    }
    function ensureUI() {
        if (ui) return ui;
        const overlay = document.createElement('div');
        overlay.className = 'bi-react-picker';
        overlay.setAttribute('data-back-closable', '');
        overlay.innerHTML = `
            <div class="bi-react-picker__sheet" role="dialog" aria-modal="true" aria-label="${TEXTS.pickerTitle}">
                <div class="bi-react-picker__head">
                    <span class="bi-react-picker__title">${TEXTS.pickerTitle}</span>
                    <button type="button" class="bi-react-picker__close" data-back-close aria-label="${TEXTS.close}">${CLOSE_ICON}</button>
                </div>
                <div class="bi-react-picker__body"><p class="bi-react-picker__status">${TEXTS.loading}</p></div>
            </div>`;
        document.body.appendChild(overlay);
        ui = { overlay, body: overlay.querySelector('.bi-react-picker__body'), picker: null, onPick: null, open: false };
        overlay.addEventListener('click', (event) => { if (event.target === overlay) closePicker(); });
        overlay.querySelector('[data-back-close]').addEventListener('click', closePicker);
        document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && ui.open) closePicker(); });
        if (window.BrawlInsightsModalBack) window.BrawlInsightsModalBack.register(overlay);
        return ui;
    }
    function openPicker(onPick) {
        const view = ensureUI();
        view.onPick = onPick;
        view.open = true;
        view.overlay.classList.add('active');
        document.body.classList.add('modal-open'); // アプリ版のバナー広告を隠す（ほかのモーダルと同じ）
        loadPicker().then(({ Picker, i18n }) => {
            if (!view.picker) {
                const options = {
                    locale: lang,
                    dataSource: new URL(`../reaction_emojis/${lang}.json?v=${encodeURIComponent(dataVersion)}`, scriptBase).href,
                };
                if (i18n) options.i18n = i18n; // 英語はライブラリの既定の文言（undefined を渡すと既定が消える）
                const picker = new Picker(options);
                picker.addEventListener('emoji-click', (event) => {
                    const emoji = event.detail && event.detail.unicode;
                    const callback = view.onPick;
                    closePicker();
                    if (emoji && callback) callback(emoji);
                });
                view.body.replaceChildren(picker);
                // 肌の色は選べないようにし、ライブラリ独自の「よく使う」は出さない（メニューの「最近使った」と重なるため）。
                // 国旗はデータに入れていないので、国旗のタブも出さない（タブは最後なので、数を1つ減らせば下線の位置もずれない）
                const style = document.createElement('style');
                style.textContent = '.skintone-button-wrapper,.favorites,.nav-button[data-group-id="9"]{display:none!important}'
                    + '.picker{--num-groups:8!important}.nav{grid-template-columns:repeat(8,1fr)!important}'
                    + '.search-row{padding-inline-end:var(--emoji-padding)}';
                if (picker.shadowRoot) picker.shadowRoot.appendChild(style);
                view.picker = picker;
            }
            view.picker.classList.toggle('dark', isDark());
            view.picker.classList.toggle('light', !isDark());
        }).catch(() => {
            const status = view.body.querySelector('.bi-react-picker__status');
            if (status) status.textContent = TEXTS.loadFailed;
        });
    }
    function closePicker() {
        if (!ui || !ui.open) return;
        ui.open = false;
        ui.onPick = null;
        ui.overlay.classList.remove('active');
        document.body.classList.remove('modal-open');
    }

    window.BIReactions = {
        PRESETS,
        MAX_KINDS,
        TEXTS,
        MORE_ICON,
        palette,
        remember,
        summarize,
        reactors,
        hasMine,
        canAdd,
        withMine,
        tempId,
        errorText,
        createSender,
        setCount,
        chipMounted,
        fit,
        renderChips,
        bindPress,
        openPicker,
    };
})();
