/**
 * 通知画面 Shell + Fragment 制御
 */
(function () {
    'use strict';

    const RELOAD_BUTTON_COOLDOWN_MS = 1500;
    const AGO_UPDATE_INTERVAL_MS = 60000;
    const VALID_FILTERS = new Set([
        'all',
        'post_like',
        'own_post_message',
        'participated_thread_message',
        'message_reply',
        'message_reaction',
    ]);
    const ITEMS_CONTAINER_SELECTOR = '.notifications__container';

    let userMenuDelegationBound = false;

    function parseUtcDatetime(str) {
        if (!str) return null;
        const normalized = str.includes('T') ? str : str.replace(' ', 'T');
        const date = new Date(normalized);
        return Number.isNaN(date.getTime()) ? null : date;
    }

    function formatNotificationAgoText(createdAt, lang) {
        const created = typeof createdAt === 'string' ? parseUtcDatetime(createdAt) : createdAt;
        if (!created) return '';

        const seconds = Math.floor((Date.now() - created.getTime()) / 1000);
        if (seconds < 60) return lang === 'ja' ? 'たった今' : 'Just Now';
        const minutes = Math.floor(seconds / 60);
        if (minutes < 60) return lang === 'ja' ? `${minutes}分` : `${minutes}m`;
        const hours = Math.floor(minutes / 60);
        if (hours < 24) return lang === 'ja' ? `${hours}時間` : `${hours}h`;
        const days = Math.floor(hours / 24);
        return lang === 'ja' ? `${days}日` : `${days}d`;
    }

    function updateNotificationAgoTexts(root, lang) {
        if (!root) return;
        root.querySelectorAll('.notification-title__text--ago[data-created-at]').forEach((el) => {
            const text = formatNotificationAgoText(el.dataset.createdAt, lang);
            if (text) el.textContent = `・${text}`;
        });
    }

    function injectFragmentScripts(container) {
        container.querySelectorAll('script').forEach((oldScript) => {
            const newScript = document.createElement('script');
            [...oldScript.attributes].forEach((attr) => newScript.setAttribute(attr.name, attr.value));
            if (!oldScript.src) {
                newScript.textContent = oldScript.textContent;
                oldScript.parentNode.replaceChild(newScript, oldScript);
            } else {
                oldScript.remove();
            }
        });
    }

    function buildQueryString(params) {
        const searchParams = new URLSearchParams();
        Object.entries(params).forEach(([key, value]) => {
            if (value !== undefined && value !== null) {
                searchParams.set(key, String(value));
            }
        });
        return searchParams.toString();
    }

    window.notificationsFragmentLoader = function notificationsFragmentLoader(config) {
        const {
            fragmentBaseUrl,
            lang,
            filter: initialFilter,
        } = config;

        let abortController = null;
        let agoIntervalId = null;
        let loadId = 0;

        const stopAgoUpdater = () => {
            if (agoIntervalId) {
                clearInterval(agoIntervalId);
                agoIntervalId = null;
            }
        };

        const startAgoUpdater = (container) => {
            stopAgoUpdater();
            updateNotificationAgoTexts(container, lang);
            agoIntervalId = setInterval(
                () => updateNotificationAgoTexts(container, lang),
                AGO_UPDATE_INTERVAL_MS,
            );
        };

        const loader = {
            isLoading: true,
            hasError: false,
            filter: VALID_FILTERS.has(initialFilter) ? initialFilter : 'all',

            getQueryParams() {
                return { filter: this.filter };
            },

            buildFragmentUrl() {
                return `${fragmentBaseUrl}?${buildQueryString(this.getQueryParams())}`;
            },

            buildShellUrl() {
                return `${window.location.pathname}?${buildQueryString(this.getQueryParams())}`;
            },

            syncShellUi() {
                const filterSelect = document.getElementById('filterSelect');
                if (filterSelect) filterSelect.value = this.filter;
            },

            applyStateFromUrl() {
                const params = new URLSearchParams(window.location.search);
                const nextFilter = params.get('filter') || initialFilter || 'all';
                this.filter = VALID_FILTERS.has(nextFilter) ? nextFilter : 'all';
            },

            async load({ updateHistory = false } = {}) {
                if (abortController) abortController.abort();
                abortController = new AbortController();
                const signal = abortController.signal;
                const currentLoadId = ++loadId;

                this.isLoading = true;
                this.hasError = false;
                this.syncShellUi();

                try {
                    const response = await fetch(this.buildFragmentUrl(), {
                        credentials: 'same-origin',
                        signal,
                    });
                    if (!response.ok) throw new Error(`HTTP ${response.status}`);

                    const html = await response.text();
                    if (!html || !html.trim()) throw new Error('Empty response');

                    const container = this.$refs.content;
                    if (!container) throw new Error('No content container');

                    stopAgoUpdater();
                    container.innerHTML = html;
                    injectFragmentScripts(container);
                    startAgoUpdater(container);

                    if (updateHistory) {
                        history.pushState({ notificationsBoard: this.getQueryParams() }, '', this.buildShellUrl());
                    }
                } catch (error) {
                    if (error.name === 'AbortError') return;
                    console.error('Notifications fragment load error:', error);
                    this.hasError = true;
                    stopAgoUpdater();
                } finally {
                    if (currentLoadId === loadId) {
                        this.isLoading = false;
                    }
                }
            },

            reloadPosts() {
                return this.load({ updateHistory: false });
            },

            setFilter(newFilter) {
                const nextFilter = VALID_FILTERS.has(newFilter) ? newFilter : 'all';
                if (nextFilter === this.filter) return Promise.resolve();
                this.filter = nextFilter;
                return this.load({ updateHistory: true });
            },

            init() {
                window.notificationsFragment = this;
                this.applyStateFromUrl();
                this.syncShellUi();
                history.replaceState({ notificationsBoard: this.getQueryParams() }, '', this.buildShellUrl());
                return this.load({ updateHistory: false });
            },
        };

        if (window.BoardFragmentPagination) {
            return window.BoardFragmentPagination.enhanceBoardFragmentLoader(loader, {
                fragmentBaseUrl,
                lang,
                updatePostAgoTexts: updateNotificationAgoTexts,
                getContentRoot: () => document.querySelector('#notifications-root [x-ref="content"]'),
                itemsContainerSelector: ITEMS_CONTAINER_SELECTOR,
                loadMoreErrorJa: '通知の読み込みに失敗しました。時間をおいて再試行してください。',
                loadMoreErrorEn: 'Failed to load more notifications. Please try again later.',
            });
        }
        return loader;
    };

    function hideUserMenu(userMenu) {
        if (!userMenu || userMenu.style.display !== 'block') return;
        userMenu.classList.add('chat-menu--hiding');
        setTimeout(() => {
            userMenu.style.display = 'none';
            userMenu.classList.remove('chat-menu--hiding');
        }, 200);
    }

    function positionMenu(menuElement, targetElement) {
        menuElement.classList.remove('chat-menu--hiding');

        const screenMargin = 10;
        const availableWidth = window.innerWidth - (screenMargin * 2);
        menuElement.style.maxWidth = `${availableWidth}px`;

        menuElement.style.visibility = 'hidden';
        menuElement.style.display = 'block';

        const menuRect = menuElement.getBoundingClientRect();
        const targetRect = targetElement.getBoundingClientRect();

        let top = (targetRect.top > menuRect.height + 10)
            ? (window.scrollY + targetRect.top - menuRect.height - 5)
            : (window.scrollY + targetRect.bottom + 5);

        let left = targetRect.left;
        if (left < screenMargin) {
            left = screenMargin;
        }
        if (left + menuRect.width > window.innerWidth - screenMargin) {
            left = window.innerWidth - menuRect.width - screenMargin;
        }

        menuElement.style.top = `${top}px`;
        menuElement.style.left = `${left}px`;
        menuElement.style.visibility = 'visible';
    }

    function bindUserMenuDelegation(config) {
        if (userMenuDelegationBound) return;
        const root = document.getElementById('notifications-root');
        const userMenu = document.getElementById('notification-user-menu');
        const userMenuActions = document.getElementById('notification-user-menu-actions');
        if (!root || !userMenu || !userMenuActions) return;

        const { lang, blockUserUrl } = config;
        userMenuDelegationBound = true;

        function showUserMenu(targetElement, actorData) {
            userMenuActions.innerHTML = '';
            let itemCount = 0;

            if (actorData.mainAccountTag && actorData.mainAccountName) {
                itemCount++;
                const profileLink = document.createElement('a');
                const tag = actorData.mainAccountTag.startsWith('#')
                    ? actorData.mainAccountTag.substring(1)
                    : actorData.mainAccountTag;
                profileLink.href = `/${lang}/player/profile/${tag}`;
                const profileButton = document.createElement('button');
                profileButton.type = 'button';
                profileButton.className = 'chat-menu__action-btn';
                profileButton.style.color = '#111';
                profileButton.style.gap = '0';
                profileButton.style.display = 'flex';
                profileButton.style.flexWrap = 'wrap';
                profileButton.style.alignItems = 'center';
                profileButton.innerHTML = lang === 'ja'
                    ? `このユーザーのメインアカウント("<b style="white-space: normal; overflow-wrap: normal; word-break: keep-all;">${actorData.mainAccountName}</b>")の情報を見る`
                    : `View main account ("<b style="white-space: normal; overflow-wrap: normal; word-break: keep-all;">${actorData.mainAccountName}</b>") profile`;
                profileLink.appendChild(profileButton);
                userMenuActions.appendChild(profileLink);
            }

            if (actorData.userId) {
                if (itemCount > 0) {
                    const separator = document.createElement('div');
                    separator.className = 'chat-menu__separator';
                    userMenuActions.appendChild(separator);
                }

                const blockButton = document.createElement('button');
                blockButton.type = 'button';
                blockButton.className = 'chat-menu__action-btn';
                blockButton.style.color = '#c90808';
                blockButton.textContent = lang === 'ja' ? 'このユーザーをブロック' : 'Block this user';
                blockButton.addEventListener('click', async () => {
                    const msg = lang === 'ja'
                        ? 'このユーザーをブロックしますか？\nブロックすると、このユーザーの投稿が非表示になります。'
                        : 'Block this user?\nTheir posts will be hidden.';
                    if (!confirm(msg)) return;
                    try {
                        const res = await fetch(blockUserUrl, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                blocked_user_id: actorData.userId,
                                blocked_anonymous_id: null,
                            }),
                        });
                        if (res.ok) {
                            hideUserMenu(userMenu);
                            if (window.notificationsFragment) {
                                await window.notificationsFragment.reloadPosts();
                            }
                        } else {
                            alert(lang === 'ja' ? 'ブロックに失敗しました' : 'Failed to block');
                        }
                    } catch {
                        alert(lang === 'ja' ? 'エラーが発生しました' : 'An error occurred');
                    }
                });
                userMenuActions.appendChild(blockButton);
            }

            if (itemCount === 0 && !actorData.userId) return;
            positionMenu(userMenu, targetElement);
        }

        root.addEventListener('click', (event) => {
            const icon = event.target.closest('.notification-user-icons__image--clickable');
            if (!icon) return;
            event.preventDefault();
            event.stopPropagation();
            showUserMenu(icon, {
                userId: Number.isFinite(parseInt(icon.dataset.userId, 10))
                    ? parseInt(icon.dataset.userId, 10)
                    : null,
                mainAccountTag: icon.dataset.mainAccountTag,
                mainAccountName: icon.dataset.mainAccountName,
                userName: icon.dataset.userName,
            });
        });

        document.addEventListener('click', (event) => {
            if (!userMenu.contains(event.target) && !event.target.closest('.notification-user-icons__image--clickable')) {
                hideUserMenu(userMenu);
            }
        });
    }

    function bindShellControls() {
        const filterSelect = document.getElementById('filterSelect');
        const filterForm = document.getElementById('filterForm');
        if (filterSelect) {
            filterSelect.addEventListener('change', () => {
                if (window.notificationsFragment) {
                    window.notificationsFragment.setFilter(filterSelect.value);
                }
            });
        }
        if (filterForm) {
            filterForm.addEventListener('submit', (event) => {
                event.preventDefault();
            });
        }

        const reloadButton = document.querySelector('.reload-button__container');
        if (reloadButton) {
            reloadButton.addEventListener('click', async () => {
                if (reloadButton.classList.contains('reload-button__container--disabled')) return;
                if (!window.notificationsFragment) return;

                reloadButton.classList.add('reload-button__container--disabled');
                try {
                    await window.notificationsFragment.reloadPosts();
                } finally {
                    setTimeout(() => {
                        reloadButton.classList.remove('reload-button__container--disabled');
                    }, RELOAD_BUTTON_COOLDOWN_MS);
                }
            });
        }

        window.addEventListener('popstate', (event) => {
            if (!window.notificationsFragment) return;
            if (event.state?.notificationsBoard) {
                const state = event.state.notificationsBoard;
                const nextFilter = state.filter || 'all';
                window.notificationsFragment.filter = VALID_FILTERS.has(nextFilter) ? nextFilter : 'all';
            } else {
                window.notificationsFragment.applyStateFromUrl();
            }
            window.notificationsFragment.load({ updateHistory: false });
        });
    }

    window.initNotificationsShell = function initNotificationsShell(config) {
        bindUserMenuDelegation(config);
        bindShellControls();
    };
})();
