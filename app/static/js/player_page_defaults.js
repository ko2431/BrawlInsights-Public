/**
 * プレイヤーページのデフォルトタブ／バトル履歴サブタブ（localStorage）を
 * 汎用の「このプレイヤーを開く」導線に適用する。
 */
(function () {
    'use strict';

    const PAGE_TABS = new Set(['profile', 'battles', 'stats']);
    const BATTLE_SUBTABS = new Set(['all', 'trophy', 'powerleague', 'friendly']);

    function getDefaults() {
        let pageTab = 'profile';
        let battleSubtab = 'all';
        try {
            const storedPage = localStorage.getItem('bi_default_player_tab');
            const storedBattle = localStorage.getItem('bi_default_battle_subtab');
            if (PAGE_TABS.has(storedPage)) pageTab = storedPage;
            if (BATTLE_SUBTABS.has(storedBattle)) battleSubtab = storedBattle;
        } catch (e) {
            // localStorage が使えない場合はデフォルトのまま
        }
        return { pageTab, battleSubtab };
    }

    function parsePlayerPath(pathname) {
        const parts = pathname.split('/').filter(Boolean);
        if (parts.length < 4 || parts[1] !== 'player') return null;
        const kind = parts[2];
        if (kind !== 'profile' && kind !== 'battles' && kind !== 'stats') return null;
        const tag = parts.slice(3).join('/');
        if (!tag) return null;
        return { lang: parts[0], kind, tag };
    }

    function buildPlayerLandingUrl(lang, tag, defaults) {
        const t = String(tag).replace(/^#/, '');
        const { pageTab, battleSubtab } = defaults || getDefaults();
        if (pageTab === 'battles') {
            const q = battleSubtab && battleSubtab !== 'all' ? ('?tab=' + encodeURIComponent(battleSubtab)) : '';
            return '/' + lang + '/player/battles/' + t + q;
        }
        if (pageTab === 'stats') {
            return '/' + lang + '/player/stats/' + t;
        }
        return '/' + lang + '/player/profile/' + t;
    }

    function rewritePlayerUrl(href) {
        let url;
        try {
            url = new URL(href, window.location.origin);
        } catch (e) {
            return null;
        }
        if (url.origin !== window.location.origin) return null;
        const parsed = parsePlayerPath(url.pathname);
        if (!parsed) return null;

        const defaults = getDefaults();

        if (parsed.kind === 'profile' && ![...url.searchParams.keys()].length && !url.hash) {
            const next = buildPlayerLandingUrl(parsed.lang, parsed.tag, defaults);
            if (next === url.pathname) return null;
            return next;
        }

        if (parsed.kind === 'battles' && !url.searchParams.has('tab')) {
            if (!defaults.battleSubtab || defaults.battleSubtab === 'all') return null;
            url.searchParams.set('tab', defaults.battleSubtab);
            return url.pathname + url.search + url.hash;
        }

        if (
            parsed.kind === 'battles'
            && url.searchParams.has('brawler_id')
            && url.searchParams.get('tab') === 'all'
        ) {
            if (!defaults.battleSubtab || defaults.battleSubtab === 'all') return null;
            url.searchParams.set('tab', defaults.battleSubtab);
            return url.pathname + url.search + url.hash;
        }

        return null;
    }

    function shouldSkipAnchor(anchor) {
        if (!anchor || anchor.hasAttribute('data-player-tab-fixed')) return true;
        const rawHref = (anchor.getAttribute('href') || '').trim();
        if (!rawHref || rawHref === '#' || rawHref.toLowerCase().startsWith('javascript:')) return true;
        if (!anchor.classList.contains('tab-nav__link')) return false;
        try {
            const url = new URL(anchor.href, window.location.origin);
            const parsed = parsePlayerPath(url.pathname);
            if (parsed && (parsed.kind === 'profile' || parsed.kind === 'stats')) return true;
        } catch (e) {
            return true;
        }
        return false;
    }

    function isBlankTarget(anchor) {
        const target = (anchor.getAttribute('target') || '').toLowerCase();
        return target && target !== '_self';
    }

    document.addEventListener('click', function (event) {
        if (event.defaultPrevented) return;
        if (event.button !== 0) return;
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        const anchor = event.target.closest && event.target.closest('a[href]');
        if (!anchor || shouldSkipAnchor(anchor)) return;
        const rewritten = rewritePlayerUrl(anchor.href);
        if (!rewritten) return;
        if (isBlankTarget(anchor)) {
            anchor.href = rewritten;
            return;
        }
        event.preventDefault();
        window.location.href = rewritten;
    });

    window.biBuildPlayerLandingUrl = function (lang, tag) {
        return buildPlayerLandingUrl(lang, tag, getDefaults());
    };
})();
