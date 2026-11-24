// /app/static/js/glass_tabs.js
// リキッドグラスのサブタブ（css/components/glass_tabs.css）の動き。統計データ・ツール・アカウントタブで共通。
// - 選ばれたタブ（.gtab.is-active）に白い札を動かす。どのタブを選ぶかは各画面（Alpine）が is-active で決め、
//   ここではクラスの変化を見て札を追わせるだけ（各画面の状態の持ち方には手を入れない）
// - 上に貼り付いたか（直前の .gtabs-anchor が貼り付く位置より上に出たか）を見て、.gtabs と .gtabs-edge に印を付ける
// - ぼかす範囲（.gtabs-edge の高さ）を、貼り付いたサブタブの下端より少し下までにする
// 画面からは window.biGlassTabs.scrollToTop(nav)（そのサブタブの先頭へ戻す）・stickyBottom(nav)（貼り付いたサブタブの下端）を使う
(function () {
    'use strict';
    if (window.biGlassTabs) return;

    const EDGE_BELOW = 18; // 貼り付いたサブタブの下端から、ぼかしを薄めていく長さ (px)
    const items = [];

    function prevWithClass(el, className) {
        let node = el.previousElementSibling;
        while (node && !node.classList.contains(className)) node = node.previousElementSibling;
        return node;
    }

    function stickyTop(item) {
        return parseFloat(getComputedStyle(item.nav).top) || 0;
    }

    function movePill(item) {
        const active = item.nav.querySelector('.gtab.is-active');
        if (!item.pill || !active || !active.offsetWidth) return;
        item.pill.style.transform = `translate(${active.offsetLeft}px, ${active.offsetTop}px)`;
        item.pill.style.width = `${active.offsetWidth}px`;
        item.pill.style.height = `${active.offsetHeight}px`;
        // 最初の位置は動かさずに置き、その後の切り替えから滑らせる
        if (!item.pill.classList.contains('is-ready')) requestAnimationFrame(() => item.pill.classList.add('is-ready'));
    }

    function updateStuck(item) {
        if (!item.anchor || !item.nav.offsetParent) return;
        const stuck = item.anchor.getBoundingClientRect().top < item.top - 0.5;
        if (stuck === item.stuck) return;
        item.stuck = stuck;
        item.nav.classList.toggle('is-stuck', stuck);
        if (item.edge) item.edge.classList.toggle('is-on', stuck);
    }

    function measure(item) {
        item.top = stickyTop(item);
        if (item.edge) item.edge.style.setProperty('--gtabs-edge-h', `${Math.round(item.top + item.nav.offsetHeight + EDGE_BELOW)}px`);
        movePill(item);
        updateStuck(item);
    }

    function setup(nav) {
        if (nav.__glassTabs) return;
        const item = {
            nav,
            pill: nav.querySelector('.gtabs__pill'),
            edge: prevWithClass(nav, 'gtabs-edge'),
            anchor: prevWithClass(nav, 'gtabs-anchor'),
            stuck: false,
            top: 0,
        };
        nav.__glassTabs = item;
        items.push(item);
        // is-active の付け替え（Alpine の :class）を見て札を動かす。札の style の変化は class ではないので拾わない
        let queued = false;
        new MutationObserver(() => {
            if (queued) return;
            queued = true;
            requestAnimationFrame(() => { queued = false; movePill(item); });
        }).observe(nav, { subtree: true, attributes: true, attributeFilter: ['class'] });
        if ('ResizeObserver' in window) new ResizeObserver(() => measure(item)).observe(nav);
        measure(item);
    }

    function scan() {
        document.querySelectorAll('[data-glass-tabs]').forEach(setup);
    }

    let scrollQueued = false;
    window.addEventListener('scroll', () => {
        if (scrollQueued) return;
        scrollQueued = true;
        requestAnimationFrame(() => { scrollQueued = false; items.forEach(updateStuck); });
    }, { passive: true });
    window.addEventListener('resize', () => items.forEach(measure));

    // Alpine が is-active を付けたあとにも合わせ直す（隠れていたサブタブが表示されたときなど）
    document.addEventListener('DOMContentLoaded', scan);
    document.addEventListener('alpine:initialized', () => { scan(); items.forEach(measure); });
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => items.forEach(measure));

    function itemOf(nav) {
        const el = nav || document.querySelector('[data-glass-tabs]');
        if (!el) return null;
        setup(el);
        return el.__glassTabs;
    }

    window.biGlassTabs = {
        // スクロールしていれば、そのサブタブの先頭（貼り付いたサブタブのすぐ下）へ戻す（アニメーションなし）
        scrollToTop(nav) {
            const item = itemOf(nav);
            if (!item || !item.anchor) return;
            const target = item.anchor.getBoundingClientRect().top + window.scrollY - stickyTop(item);
            if (window.scrollY > target + 1) window.scrollTo({ top: target, behavior: 'auto' });
        },
        // 貼り付いたサブタブの下端（画面の上端から）。ページ内の見出しへ飛ぶときに、サブタブの裏に隠れないよう使う
        stickyBottom(nav) {
            const item = itemOf(nav);
            return item ? stickyTop(item) + item.nav.offsetHeight : 0;
        },
        refresh() {
            scan();
            items.forEach(measure);
        },
    };
})();
