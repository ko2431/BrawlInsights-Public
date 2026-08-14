/**
 * なんでも掲示板 Shell + Fragment 制御（Phase 2）
 */
(function () {
    'use strict';

    const RELOAD_BUTTON_COOLDOWN_MS = 1500;
    const AGO_UPDATE_INTERVAL_MS = 60000;
    const DEFAULT_FILTER = 'all';
    const FILTER_INCLUDE_ALL = 'all';
    const FILTER_EXCLUDE_OFFTOPIC = 'all_except_offtopic';
    const STORAGE_KEY_TAB = 'generalBoardTab';
    const STORAGE_KEY_FILTER = 'generalBoardFilter';
    const VALID_TABS = new Set(['latest', 'trending', 'own', 'participated', 'liked']);
    const VALID_FILTERS = new Set([
        'all', 'all_except_offtopic', 'chat', 'question', 'offtopic',
        'brawl_info', 'x', 'discord', 'youtube', 'tiktok',
    ]);

    let fabCooldownTimer = null;
    let postDelegationBound = false;

    function readStoredPref(key, validValues) {
        try {
            const value = localStorage.getItem(key);
            if (value && validValues.has(value)) return value;
        } catch {
            /* ignore */
        }
        return null;
    }

    function writeStoredPrefs(tab, filter) {
        try {
            if (VALID_TABS.has(tab)) localStorage.setItem(STORAGE_KEY_TAB, tab);
            if (VALID_FILTERS.has(filter)) localStorage.setItem(STORAGE_KEY_FILTER, filter);
        } catch {
            /* ignore */
        }
    }

    function parseUtcDatetime(str) {
        if (!str) return null;
        const normalized = str.includes('T') ? str : str.replace(' ', 'T');
        const date = new Date(normalized);
        return Number.isNaN(date.getTime()) ? null : date;
    }

    function formatPostAgoText(createdAt, lang) {
        const created = typeof createdAt === 'string' ? parseUtcDatetime(createdAt) : createdAt;
        if (!created) return '';

        const seconds = Math.floor((Date.now() - created.getTime()) / 1000);
        const days = Math.floor(seconds / 86400);
        const hours = Math.floor(seconds / 3600);
        const minutes = Math.floor(seconds / 60);

        if (lang === 'ja') {
            if (days >= 10) return `${days}日前`;
            if (days) return `${days}日 ${hours - days * 24}時間前`;
            if (hours >= 10) return `${hours}時間前`;
            if (hours) return `${hours}時間 ${minutes - hours * 60}分前`;
            if (minutes) return `${minutes}分前`;
            return 'たった今';
        }

        if (days >= 10) return `${days}d ago`;
        if (days) return `${days}d ${hours - days * 24}h ago`;
        if (hours >= 10) return `${hours}h ago`;
        if (hours) return `${hours}h ${minutes - hours * 60}m ago`;
        if (minutes) return `${minutes}m ago`;
        return 'Just Now';
    }

    function updatePostAgoTexts(root, lang) {
        if (!root) return;
        root.querySelectorAll('.post-card__ago-text[data-created-at]').forEach((el) => {
            const text = formatPostAgoText(el.dataset.createdAt, lang);
            if (text) el.textContent = text;
        });
    }

    function scheduleFabCooldownRelease(fab, seconds) {
        if (!fab || seconds <= 0) return;
        fab.classList.add('disabled');
        fab.dataset.cooldown = String(seconds);
        if (fabCooldownTimer) clearTimeout(fabCooldownTimer);
        fabCooldownTimer = setTimeout(() => {
            fab.classList.remove('disabled');
            fab.dataset.cooldown = '0';
            fabCooldownTimer = null;
        }, seconds * 1000);
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

    function reloadPostsFromFragment() {
        if (window.generalBoardFragment) {
            return window.generalBoardFragment.reloadPosts();
        }
        return Promise.resolve();
    }

    window.generalBoardFragmentLoader = function generalBoardFragmentLoader(config) {
        const {
            fragmentBaseUrl,
            lang,
            tab: initialTab,
            filter: initialFilter,
            limit,
            region,
            eliminateDuplicates,
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
            updatePostAgoTexts(container, lang);
            agoIntervalId = setInterval(() => updatePostAgoTexts(container, lang), AGO_UPDATE_INTERVAL_MS);
        };

        const loader = {
            isLoading: true,
            hasError: false,
            tab: initialTab,
            filter: initialFilter,
            limit,
            region,
            eliminateDuplicates,

            getQueryParams() {
                return {
                    tab: this.tab,
                    filter: this.filter,
                    limit: this.limit,
                    region: this.region,
                    eliminate_duplicates: String(this.eliminateDuplicates).toLowerCase(),
                };
            },

            buildFragmentUrl() {
                return `${fragmentBaseUrl}?${buildQueryString(this.getQueryParams())}`;
            },

            buildShellUrl() {
                return `${window.location.pathname}?${buildQueryString(this.getQueryParams())}`;
            },

            syncShellUi() {
                document.querySelectorAll('[data-board-tab]').forEach((link) => {
                    link.classList.toggle('sub-tab-nav__link--active', link.dataset.boardTab === this.tab);
                });

                const filterSelect = document.getElementById('filterSelect');
                const tabInput = document.querySelector('#filterForm input[name="tab"]');
                if (filterSelect) filterSelect.value = this.filter;
                if (tabInput) tabInput.value = this.tab;
            },

            applyStateFromUrl() {
                const params = new URLSearchParams(window.location.search);
                this.tab = params.get('tab') || initialTab || 'latest';
                this.filter = params.get('filter') || initialFilter || DEFAULT_FILTER;
            },

            persistPrefs() {
                writeStoredPrefs(this.tab, this.filter);
            },

            async load({ updateHistory = false } = {}) {
                if (abortController) abortController.abort();
                abortController = new AbortController();
                const signal = abortController.signal;
                const currentLoadId = ++loadId;

                this.isLoading = true;
                this.hasError = false;
                this.persistPrefs();

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
                    this.syncShellUi();

                    if (updateHistory) {
                        history.pushState({ generalBoard: this.getQueryParams() }, '', this.buildShellUrl());
                    }
                } catch (error) {
                    if (error.name === 'AbortError') return;
                    console.error('Board fragment load error:', error);
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

            setTab(newTab) {
                if (newTab === this.tab) {
                    return this.reloadPosts();
                }
                this.tab = newTab;
                return this.load({ updateHistory: true });
            },

            setFilter(newFilter) {
                if (newFilter === this.filter) return Promise.resolve();
                this.filter = newFilter;
                return this.load({ updateHistory: true });
            },

            navigateAfterPost(postedCategory) {
                if (this.tab !== 'own') {
                    this.tab = 'latest';
                }
                if (this.filter === FILTER_INCLUDE_ALL) {
                    // オフトピック含む表示中はカテゴリに関わらずそのまま
                } else if (this.filter === FILTER_EXCLUDE_OFFTOPIC) {
                    if (postedCategory === 'offtopic') {
                        this.filter = FILTER_INCLUDE_ALL;
                    }
                } else if (this.filter !== postedCategory) {
                    // 個別フィルターと不一致: 「オフトピック含む」に切り替え
                    this.filter = FILTER_INCLUDE_ALL;
                }
                return this.load({ updateHistory: true });
            },

            init() {
                window.generalBoardFragment = this;

                const params = new URLSearchParams(window.location.search);
                if (!params.has('tab')) {
                    const storedTab = readStoredPref(STORAGE_KEY_TAB, VALID_TABS);
                    if (storedTab) this.tab = storedTab;
                }
                if (!params.has('filter')) {
                    const storedFilter = readStoredPref(STORAGE_KEY_FILTER, VALID_FILTERS);
                    if (storedFilter) this.filter = storedFilter;
                }

                this.persistPrefs();
                this.syncShellUi();
                history.replaceState({ generalBoard: this.getQueryParams() }, '', this.buildShellUrl());
                return this.load({ updateHistory: false });
            },
        };

        if (window.BoardFragmentPagination) {
            return window.BoardFragmentPagination.enhanceBoardFragmentLoader(loader, {
                fragmentBaseUrl,
                lang,
                updatePostAgoTexts,
                getContentRoot: () => document.querySelector('#general-board-posts-root [x-ref="content"]'),
            });
        }
        return loader;
    };

    function bindPostActionDelegation(config) {
        if (postDelegationBound) return;
        const root = document.getElementById('general-board-posts-root');
        if (!root) return;

        const { lang, blockUserUrl, unblockUserUrl } = config;
        postDelegationBound = true;

        root.addEventListener('click', async (event) => {
            const deleteBtn = event.target.closest('.post-card__delete-button');
            if (deleteBtn) {
                const postId = deleteBtn.dataset.postId;
                const msg = lang === 'ja' ? 'この投稿を削除しますか？' : 'Delete this post?';
                if (!confirm(msg)) return;
                try {
                    const response = await fetch(`/${lang}/boards/posts/${postId}`, { method: 'DELETE' });
                    if (response.ok) await reloadPostsFromFragment();
                    else alert(lang === 'ja' ? '削除に失敗しました' : 'Failed to delete');
                } catch {
                    alert(lang === 'ja' ? 'エラーが発生しました' : 'An error occurred');
                }
                return;
            }

            const reportBtn = event.target.closest('.post-card__report-button');
            if (reportBtn) {
                const reportModalOverlay = document.getElementById('report-modal-overlay');
                const reportModalSubmitBtn = document.getElementById('report-modal-submit-btn');
                const reportCategorySelect = document.getElementById('report-category');
                const reportTextForm = document.getElementById('report-text-form');
                if (!reportModalOverlay) return;
                reportModalOverlay.dataset.postId = reportBtn.dataset.postId;
                if (reportCategorySelect) reportCategorySelect.value = '';
                if (reportTextForm) reportTextForm.value = '';
                if (reportModalSubmitBtn) reportModalSubmitBtn.disabled = true;
                document.body.classList.add('modal-open');
                reportModalOverlay.classList.add('active');
                return;
            }

            const blockBtn = event.target.closest('.post-card__block-button');
            if (blockBtn) {
                const msg = lang === 'ja'
                    ? 'このユーザーをブロックしますか？\nブロックすると、このユーザーの投稿が非表示になります。'
                    : 'Block this user?\nTheir posts will be hidden.';
                if (!confirm(msg)) return;
                try {
                    const response = await fetch(blockUserUrl, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            blocked_user_id: blockBtn.dataset.blockedUserId || null,
                            blocked_anonymous_id: blockBtn.dataset.blockedAnonymousId || null,
                        }),
                    });
                    if (response.ok) await reloadPostsFromFragment();
                    else alert(lang === 'ja' ? 'ブロックに失敗しました' : 'Failed to block');
                } catch {
                    alert(lang === 'ja' ? 'エラーが発生しました' : 'An error occurred');
                }
                return;
            }

            const showBtn = event.target.closest('.post-card__show-button');
            if (showBtn) {
                const wrapper = showBtn.closest('.post-card-wrapper');
                if (!wrapper) return;
                try {
                    const response = await fetch(unblockUserUrl, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            blocked_user_id: wrapper.dataset.blockedUserId || null,
                            blocked_anonymous_id: wrapper.dataset.blockedAnonymousId || null,
                        }),
                    });
                    if (response.ok) await reloadPostsFromFragment();
                } catch {
                    /* ignore */
                }
                return;
            }

            const goodBtn = event.target.closest('.post-card__good-button[data-post-id]');
            if (goodBtn) {
                if (goodBtn.classList.contains('disabled') || goodBtn.dataset.processing === '1') return;
                const postId = goodBtn.dataset.postId;
                const countEl = goodBtn.querySelector('.post-card__good-button-count');
                if (!postId || !countEl) return;

                goodBtn.dataset.processing = '1';
                try {
                    const response = await fetch(`/${lang}/boards/posts/${postId}/good`, { method: 'POST' });
                    if (!response.ok) {
                        const data = await response.json().catch(() => ({}));
                        if (response.status === 401) {
                            alert(lang === 'ja'
                                ? 'この機能を利用するにはログインが必要です。アカウントタブより、メールアドレス不要でログインできます。'
                                : 'You need to log in to use this feature. You can log in from the Account tab without an email address.');
                        } else {
                            alert(data.detail || (lang === 'ja' ? 'グッドの更新に失敗しました' : 'Failed to update good'));
                        }
                        return;
                    }

                    const data = await response.json();
                    countEl.textContent = String(data.up_vote_count ?? 0);
                    goodBtn.classList.toggle('active', Boolean(data.is_up_voted_by_current_user));
                } catch {
                    alert(lang === 'ja' ? 'エラーが発生しました' : 'An error occurred');
                } finally {
                    goodBtn.dataset.processing = '0';
                }
            }
        });
    }

    function bindShellControls(config) {
        const { checkPostPermissionUrl } = config;

        document.querySelectorAll('[data-board-tab]').forEach((link) => {
            link.addEventListener('click', (event) => {
                event.preventDefault();
                const tab = link.dataset.boardTab;
                if (window.generalBoardFragment && tab) {
                    window.generalBoardFragment.setTab(tab);
                }
            });
        });

        const filterSelect = document.getElementById('filterSelect');
        const filterForm = document.getElementById('filterForm');
        if (filterSelect) {
            filterSelect.addEventListener('change', () => {
                if (window.generalBoardFragment) {
                    window.generalBoardFragment.setFilter(filterSelect.value);
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
                if (!window.generalBoardFragment) return;

                reloadButton.classList.add('reload-button__container--disabled');
                try {
                    await window.generalBoardFragment.reloadPosts();
                } finally {
                    setTimeout(() => {
                        reloadButton.classList.remove('reload-button__container--disabled');
                    }, RELOAD_BUTTON_COOLDOWN_MS);
                }
            });
        }

        window.addEventListener('popstate', (event) => {
            if (!window.generalBoardFragment) return;
            if (event.state?.generalBoard) {
                const state = event.state.generalBoard;
                window.generalBoardFragment.tab = state.tab || 'latest';
                window.generalBoardFragment.filter = state.filter || DEFAULT_FILTER;
            } else {
                window.generalBoardFragment.applyStateFromUrl();
            }
            window.generalBoardFragment.load({ updateHistory: false });
        });

        const fab = document.getElementById('fab');
        if (fab) {
            const cooldown = parseInt(fab.dataset.cooldown, 10);
            if (cooldown > 0) scheduleFabCooldownRelease(fab, cooldown);

            if (!fab.classList.contains('fab--grayed-out')) {
                fab.addEventListener('click', async () => {
                    try {
                        const response = await fetch(checkPostPermissionUrl);
                        const data = await response.json();
                        if (data.is_permitted) {
                            document.dispatchEvent(new CustomEvent('general-board:open-post-modal'));
                        } else if (data.cooldown > 0) {
                            alert(config.lang === 'ja'
                                ? `現在クールタイム中です。次の投稿まであと${data.cooldown}秒お待ちください。`
                                : `Please wait ${data.cooldown}s for the next post.`);
                        } else {
                            document.dispatchEvent(new CustomEvent('general-board:post-restricted'));
                        }
                    } catch {
                        alert(config.lang === 'ja' ? 'エラーが発生しました' : 'An error occurred');
                    }
                });
            }
        }
    }

    window.initGeneralBoardShell = function initGeneralBoardShell(config) {
        bindPostActionDelegation(config);
        bindShellControls(config);
    };

    window.scheduleGeneralBoardFabCooldown = scheduleFabCooldownRelease;

    window.navigateGeneralBoardAfterPost = function navigateGeneralBoardAfterPost(postedCategory) {
        if (window.generalBoardFragment) {
            return window.generalBoardFragment.navigateAfterPost(postedCategory);
        }
        return Promise.resolve();
    };

    window.fetchGeneralBoardCooldown = async function fetchGeneralBoardCooldown(checkPostPermissionUrl) {
        try {
            const response = await fetch(checkPostPermissionUrl);
            const data = await response.json();
            return data.cooldown > 0 ? data.cooldown : 0;
        } catch {
            return 0;
        }
    };
})();
