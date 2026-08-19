/**
 * 掲示板 Fragment の「さらに表示」共通処理（Phase 3）
 */
(function () {
    'use strict';

    const POSTS_CONTAINER_SELECTOR = '.posts__container';
    const LOAD_MORE_SELECTOR = '.board-load-more';
    const APPEND_CHUNK_SELECTOR = '.board-fragment__append-chunk';

    function buildQueryString(params) {
        const searchParams = new URLSearchParams();
        Object.entries(params).forEach(([key, value]) => {
            if (value !== undefined && value !== null) {
                searchParams.set(key, String(value));
            }
        });
        return searchParams.toString();
    }

    function parseAppendResponse(html) {
        const doc = new DOMParser().parseFromString(html, 'text/html');
        return {
            chunk: doc.querySelector(APPEND_CHUNK_SELECTOR),
            loadMore: doc.querySelector(LOAD_MORE_SELECTOR),
        };
    }

    function applyAppendHtml(contentRoot, html, lang, updatePostAgoTexts, itemsContainerSelector) {
        const { chunk, loadMore } = parseAppendResponse(html);
        const itemsSelector = itemsContainerSelector || POSTS_CONTAINER_SELECTOR;
        const postsContainer = contentRoot.querySelector(itemsSelector);
        if (chunk && postsContainer) {
            [...chunk.children].forEach((child) => {
                postsContainer.appendChild(document.importNode(child, true));
            });
        }

        const existingLoadMore = contentRoot.querySelector(LOAD_MORE_SELECTOR);
        if (existingLoadMore) {
            if (loadMore) {
                existingLoadMore.replaceWith(document.importNode(loadMore, true));
            } else {
                existingLoadMore.remove();
            }
        } else if (loadMore) {
            const anchor = postsContainer || contentRoot.querySelector('.board-fragment__content');
            if (anchor) {
                anchor.appendChild(document.importNode(loadMore, true));
            }
        }

        if (updatePostAgoTexts && postsContainer) {
            updatePostAgoTexts(postsContainer, lang);
        }
    }

    function bindLoadMoreDelegation(contentRoot, loader) {
        if (!contentRoot || contentRoot.dataset.loadMoreBound === '1') return;
        contentRoot.dataset.loadMoreBound = '1';

        contentRoot.addEventListener('click', (event) => {
            const button = event.target.closest(LOAD_MORE_SELECTOR);
            if (!button || button.classList.contains('board-load-more--loading')) return;
            if (typeof loader.loadMore === 'function') {
                loader.loadMore();
            }
        });
    }

    function upsertBadgeElement(wrapper, className, show, text) {
        if (!wrapper) return;
        const existing = wrapper.querySelector(`.${className.split(' ')[0]}`);
        if (!show) {
            if (existing) existing.remove();
            return;
        }
        const badge = existing || document.createElement('span');
        if (!existing) {
            badge.className = className;
            wrapper.appendChild(badge);
        }
        badge.textContent = text;
    }

    function applyNotificationBadgesFromRoot(root) {
        if (!root) return;
        const fragment = root.matches?.('.board-fragment__content')
            ? root
            : root.querySelector('.board-fragment__content');
        if (!fragment || !fragment.hasAttribute('data-notification-badge')) return;

        const show = fragment.dataset.showNotificationBadge === 'true';
        const text = fragment.dataset.notificationBadgeText || '';
        const shouldShow = show && Boolean(text);

        document.querySelectorAll('.notifications-icon-wrapper').forEach((wrapper) => {
            upsertBadgeElement(wrapper, 'notifications-badge', shouldShow, text);
        });
        document.querySelectorAll('.footer-mobile-nav__item[data-tab-id="board"] .nav-icon-wrapper').forEach((wrapper) => {
            upsertBadgeElement(wrapper, 'nav-tab-badge', shouldShow, text);
        });
        document.querySelectorAll('.header-pc__nav-link[data-tab-id="board"] .nav-icon-wrapper').forEach((wrapper) => {
            upsertBadgeElement(wrapper, 'nav-tab-badge nav-tab-badge--pc', shouldShow, text);
        });
    }

    /**
     * Fragment loader にページネーション機能を付与する。
     */
    function enhanceBoardFragmentLoader(loader, options) {
        const {
            fragmentBaseUrl,
            lang,
            updatePostAgoTexts,
            getContentRoot,
            itemsContainerSelector,
            loadMoreErrorJa,
            loadMoreErrorEn,
        } = options;

        loader.page = 1;
        loader.isLoadingMore = false;

        const originalGetQueryParams = loader.getQueryParams;
        const originalLoad = loader.load;

        loader.getQueryParams = function getQueryParamsWithPage() {
            return {
                ...originalGetQueryParams.call(this),
                page: this.page,
            };
        };

        loader.load = async function loadWithPagination(opts = {}) {
            this.page = 1;
            this.isLoadingMore = false;
            const result = await originalLoad.call(this, opts);
            const contentRoot = getContentRoot();
            if (contentRoot) {
                bindLoadMoreDelegation(contentRoot, loader);
                applyNotificationBadgesFromRoot(contentRoot);
            }
            return result;
        };

        loader.reloadPosts = function reloadPosts() {
            return this.load({ updateHistory: false });
        };

        loader.loadMore = async function loadMore() {
            if (this.isLoadingMore || this.isLoading) return;

            const contentRoot = getContentRoot();
            const button = contentRoot?.querySelector(LOAD_MORE_SELECTOR);
            if (!button) return;

            const nextPage = parseInt(button.dataset.nextPage, 10);
            if (!nextPage || nextPage < 2) return;

            this.isLoadingMore = true;
            button.classList.add('board-load-more--loading');
            button.setAttribute('aria-busy', 'true');

            try {
                const params = {
                    ...originalGetQueryParams.call(this),
                    page: nextPage,
                };
                const response = await fetch(
                    `${fragmentBaseUrl}?${buildQueryString(params)}`,
                    { credentials: 'same-origin' },
                );
                if (!response.ok) throw new Error(`HTTP ${response.status}`);

                const html = await response.text();
                if (!html || !html.trim()) throw new Error('Empty response');

                applyAppendHtml(contentRoot, html, lang, updatePostAgoTexts, itemsContainerSelector);
                this.page = nextPage;
            } catch (error) {
                console.error('Board load more error:', error);
                const msg = lang === 'ja'
                    ? (loadMoreErrorJa || '投稿の読み込みに失敗しました。時間をおいて再試行してください。')
                    : (loadMoreErrorEn || 'Failed to load more posts. Please try again later.');
                alert(msg);
            } finally {
                this.isLoadingMore = false;
                const updatedButton = contentRoot?.querySelector(LOAD_MORE_SELECTOR);
                if (updatedButton) {
                    updatedButton.classList.remove('board-load-more--loading');
                    updatedButton.removeAttribute('aria-busy');
                }
            }
        };

        return loader;
    }

    const RETURN_STATE_KEY = 'bi-board-return-state';
    const RETURN_STATE_TTL_MS = 30 * 60 * 1000;
    const MAX_RESTORE_LOAD_MORE = 10;
    const CHAT_PATH_RE = /\/boards\/chat\/(\d+)/;

    function normalizePathname(pathname) {
        return (pathname || '/').replace(/\/+$/, '') || '/';
    }

    function extractThreadIdFromHref(href) {
        if (!href) return null;
        try {
            const url = new URL(href, window.location.origin);
            const match = url.pathname.match(CHAT_PATH_RE);
            return match ? match[1] : null;
        } catch {
            const match = String(href).match(CHAT_PATH_RE);
            return match ? match[1] : null;
        }
    }

    function getActiveBoardLoader() {
        const fragments = window.recruitmentBoardFragments || {};
        return window.teamBoardFragment
            || fragments.friend
            || fragments.club
            || window.generalBoardFragment
            || window.notificationsFragment
            || null;
    }

    function getCurrentLoaderPage() {
        const page = Number(getActiveBoardLoader()?.page);
        return Number.isFinite(page) && page >= 1 ? page : 1;
    }

    function clearReturnState() {
        try {
            sessionStorage.removeItem(RETURN_STATE_KEY);
        } catch {
            /* ignore */
        }
    }

    function readReturnState() {
        try {
            const raw = sessionStorage.getItem(RETURN_STATE_KEY);
            if (!raw) return null;
            const state = JSON.parse(raw);
            if (!state || typeof state !== 'object') return null;
            return state;
        } catch {
            return null;
        }
    }

    function isReturnStateFresh(state) {
        if (!state || typeof state !== 'object') return false;
        const age = Date.now() - Number(state.savedAt || 0);
        return Number.isFinite(age) && age >= 0 && age <= RETURN_STATE_TTL_MS;
    }

    function pendingReturnStateForThisPage() {
        const state = readReturnState();
        if (!isReturnStateFresh(state)) return null;
        if (normalizePathname(state.pathname) !== normalizePathname(window.location.pathname)) {
            return null;
        }
        return state;
    }

    function disableScrollAnchoring() {
        document.documentElement.style.overflowAnchor = 'none';
        if (document.body) document.body.style.overflowAnchor = 'none';
        try {
            history.scrollRestoration = 'manual';
        } catch {
            /* ignore */
        }
    }

    function setWindowScrollY(top) {
        const y = Math.max(0, Math.round(top));
        window.scrollTo(0, y);
        document.documentElement.scrollTop = y;
        document.body.scrollTop = y;
    }

    function saveOnChatClick(event) {
        if (event.defaultPrevented) return;
        if (event.type !== 'pointerdown' && event.button !== 0) return;
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;

        const link = event.target.closest?.('a[href]');
        if (!link || link.target === '_blank') return;

        const threadId = extractThreadIdFromHref(link.getAttribute('href') || link.href);
        if (!threadId) return;

        const state = {
            pathname: normalizePathname(window.location.pathname),
            page: getCurrentLoaderPage(),
            threadId,
            scrollY: window.scrollY || window.pageYOffset || 0,
            savedAt: Date.now(),
        };
        try {
            sessionStorage.setItem(RETURN_STATE_KEY, JSON.stringify(state));
        } catch {
            /* ignore quota / private mode */
        }
    }

    function consumeReturnState(currentPathname) {
        const state = readReturnState();
        if (!isReturnStateFresh(state)) {
            clearReturnState();
            return null;
        }
        if (normalizePathname(state.pathname) !== normalizePathname(currentPathname)) {
            return null;
        }
        clearReturnState();
        return state;
    }

    function findThreadAnchor(threadId) {
        if (!threadId) return null;
        const expected = String(threadId);
        const links = document.querySelectorAll('a[href]');
        for (const link of links) {
            if (extractThreadIdFromHref(link.getAttribute('href') || link.href) === expected) {
                return link;
            }
        }
        return null;
    }

    function isElementVisible(el) {
        if (!el || !el.getClientRects) return false;
        if (el.getClientRects().length === 0) return false;
        const style = window.getComputedStyle(el);
        return style.display !== 'none' && style.visibility !== 'hidden';
    }

    function getRestoreTarget(link) {
        return link.closest('.post-card-wrapper')
            || link.closest('.notification')
            || link;
    }

    function scrollToThreadAnchor(link) {
        const target = getRestoreTarget(link);
        if (!isElementVisible(target)) return false;
        const rect = target.getBoundingClientRect();
        const currentY = window.scrollY || window.pageYOffset || 0;
        const top = currentY + rect.top - (window.innerHeight / 2) + (rect.height / 2);
        setWindowScrollY(top);
        return true;
    }

    function sleep(ms) {
        return new Promise((resolve) => {
            setTimeout(resolve, ms);
        });
    }

    async function waitUntilContentVisible(timeoutMs) {
        const started = Date.now();
        while (Date.now() - started < timeoutMs) {
            const root = document.querySelector('.board-shell__posts [x-ref="content"]');
            if (root && isElementVisible(root) && root.children.length > 0) {
                return root;
            }
            await sleep(50);
        }
        return null;
    }

    async function restoreScrollToAnchor(threadId) {
        const tryScroll = () => {
            const anchor = findThreadAnchor(threadId);
            if (!anchor) return false;
            return scrollToThreadAnchor(anchor);
        };
        if (tryScroll()) {
            await sleep(150);
            tryScroll();
            await sleep(350);
            tryScroll();
            return true;
        }
        return false;
    }

    async function restoreAfterChat(loader) {
        if (!loader) return;

        const state = consumeReturnState(window.location.pathname);
        if (!state) return;

        disableScrollAnchoring();
        setWindowScrollY(0);

        await waitUntilContentVisible(3000);

        const threadId = state.threadId ? String(state.threadId) : '';
        if (threadId) {
            for (let extra = 0; ; extra++) {
                if (await restoreScrollToAnchor(threadId)) return;
                if (extra >= MAX_RESTORE_LOAD_MORE || typeof loader.loadMore !== 'function') break;
                const pageBefore = loader.page;
                await loader.loadMore();
                if (loader.page === pageBefore) break;
            }
        } else {
            const targetPage = Number(state.page) || 1;
            while (loader.page < targetPage && loader.page < 1 + MAX_RESTORE_LOAD_MORE) {
                if (typeof loader.loadMore !== 'function') break;
                const pageBefore = loader.page;
                await loader.loadMore();
                if (loader.page === pageBefore) break;
            }
        }

        const fallbackY = Number(state.scrollY);
        setWindowScrollY(Number.isFinite(fallbackY) ? fallbackY : 0);
    }

    if (pendingReturnStateForThisPage()) {
        disableScrollAnchoring();
        setWindowScrollY(0);
    }

    document.addEventListener('click', saveOnChatClick, true);
    document.addEventListener('pointerdown', saveOnChatClick, true);
    window.addEventListener('pageshow', (event) => {
        if (event.persisted) {
            clearReturnState();
            return;
        }
        if (pendingReturnStateForThisPage()) {
            disableScrollAnchoring();
            setWindowScrollY(0);
        }
    });

    window.BoardFragmentPagination = {
        POSTS_CONTAINER_SELECTOR,
        LOAD_MORE_SELECTOR,
        APPEND_CHUNK_SELECTOR,
        buildQueryString,
        applyAppendHtml,
        bindLoadMoreDelegation,
        enhanceBoardFragmentLoader,
        applyNotificationBadgesFromRoot,
        restoreAfterChat,
    };
})();
