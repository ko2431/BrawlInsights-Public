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

    function applyAppendHtml(contentRoot, html, lang, updatePostAgoTexts) {
        const { chunk, loadMore } = parseAppendResponse(html);
        const postsContainer = contentRoot.querySelector(POSTS_CONTAINER_SELECTOR);
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

                applyAppendHtml(contentRoot, html, lang, updatePostAgoTexts);
                this.page = nextPage;
            } catch (error) {
                console.error('Board load more error:', error);
                const msg = lang === 'ja'
                    ? '投稿の読み込みに失敗しました。時間をおいて再試行してください。'
                    : 'Failed to load more posts. Please try again later.';
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

    window.BoardFragmentPagination = {
        POSTS_CONTAINER_SELECTOR,
        LOAD_MORE_SELECTOR,
        APPEND_CHUNK_SELECTOR,
        buildQueryString,
        applyAppendHtml,
        bindLoadMoreDelegation,
        enhanceBoardFragmentLoader,
        applyNotificationBadgesFromRoot,
    };
})();
