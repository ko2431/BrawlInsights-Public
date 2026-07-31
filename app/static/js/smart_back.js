/**
 * アプリ版「戻る」ボタン用: タブ跨ぎ後はフォールバックURLへ、同一タブ内は history.back()。
 */
(function () {
    'use strict';

    const TAB_PATH_SEGMENT = {
        stats: 'stats',
        tools: 'tools',
        boards: 'board',
        account: 'account',
        player: 'home',
        club: 'home',
        help: 'home',
    };

    function getLangFromPath(pathname) {
        const parts = pathname.split('/').filter(Boolean);
        return parts[0] || document.documentElement.lang || 'ja';
    }

    function getTabIdFromPath(pathname) {
        const parts = pathname.split('/').filter(Boolean);
        if (parts.length <= 1) return 'home';
        return TAB_PATH_SEGMENT[parts[1]] || 'home';
    }

    function getCurrentTabId() {
        const pageId = (document.body && document.body.dataset.pageId) || '';
        if (pageId === 'home' || pageId === 'stats' || pageId === 'tools' || pageId === 'board' || pageId === 'account') {
            return pageId;
        }
        // タブ未設定・非所属ページはホーム扱い
        return 'home';
    }

    function getTabRootUrl(tabId, lang) {
        switch (tabId) {
            case 'stats':
                return `/${lang}/stats`;
            case 'tools':
                return `/${lang}/tools`;
            case 'board': {
                const defaultBoard = localStorage.getItem('bi_default_board') || 'team';
                const board = ['team', 'friend', 'club', 'general'].includes(defaultBoard)
                    ? defaultBoard
                    : 'team';
                return `/${lang}/boards/${board}`;
            }
            case 'account':
                return `/${lang}/account`;
            case 'home':
            default:
                return `/${lang}`;
        }
    }

    function resolveFallbackUrl(button) {
        const explicit =
            (button && button.getAttribute('data-back-fallback')) ||
            (button && button.closest('[data-back-fallback]')?.getAttribute('data-back-fallback')) ||
            document.body?.getAttribute('data-back-fallback');
        if (explicit) return explicit;

        const lang = getLangFromPath(window.location.pathname);
        return getTabRootUrl(getCurrentTabId(), lang);
    }

    function isReferrerOtherTab() {
        const referrer = document.referrer;
        if (!referrer) return false;
        let refUrl;
        try {
            refUrl = new URL(referrer);
        } catch {
            return false;
        }
        if (refUrl.origin !== window.location.origin) return false;

        const referrerTab = getTabIdFromPath(refUrl.pathname);
        const currentTab = getCurrentTabId();
        return referrerTab !== currentTab;
    }

    function smartBack(button) {
        const fallbackUrl = resolveFallbackUrl(button || null);

        if (isReferrerOtherTab()) {
            window.location.href = fallbackUrl;
            return;
        }

        if (window.history.length > 1) {
            history.back();
            return;
        }

        window.location.href = fallbackUrl;
    }

    function onBackButtonClick(event) {
        const button = event.target.closest('.back-button');
        if (!button) return;
        event.preventDefault();
        event.stopPropagation();
        smartBack(button);
    }

    document.addEventListener('click', onBackButtonClick, true);

    window.BrawlInsightsSmartBack = {
        go: smartBack,
        resolveFallbackUrl,
        isReferrerOtherTab,
    };
})();
