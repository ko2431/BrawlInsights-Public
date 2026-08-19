/**
 * チーム募集掲示板 Shell + Fragment 制御（Phase 2）
 */
(function () {
    'use strict';

    const RELOAD_BUTTON_COOLDOWN_MS = 1500;
    const AGO_UPDATE_INTERVAL_MS = 60000;
    const STORAGE_KEY_CATEGORY = 'teamBoardCategory';
    const STORAGE_KEY_FILTER = 'teamBoardFilter';
    const STORAGE_KEY_ONLY_JOINABLE = 'teamBoardOnlyJoinable';
    const VALID_CATEGORIES = new Set(['all', 'trophy', 'ranked', 'friendly', 'event']);
    const VALID_FILTERS = new Set([
        'all',
        'only_instant_recruitment',
        'only_later_recruitment',
        'only_participated_threads',
        'only_own_posts',
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

    function readStoredBool(key) {
        try {
            const value = localStorage.getItem(key);
            if (value === 'true') return true;
            if (value === 'false') return false;
        } catch {
            /* ignore */
        }
        return null;
    }

    function writeStoredPrefs(category, filter, onlyJoinable) {
        try {
            if (VALID_CATEGORIES.has(category)) localStorage.setItem(STORAGE_KEY_CATEGORY, category);
            if (VALID_FILTERS.has(filter)) localStorage.setItem(STORAGE_KEY_FILTER, filter);
            localStorage.setItem(STORAGE_KEY_ONLY_JOINABLE, String(Boolean(onlyJoinable)));
        } catch {
            /* ignore */
        }
    }

    function normalizeTeamFilter(filter, onlyJoinable) {
        if (filter === 'only_can_participate') {
            return { filter: 'all', onlyJoinable: true };
        }
        if (!VALID_FILTERS.has(filter)) {
            return { filter: 'all', onlyJoinable };
        }
        return { filter, onlyJoinable };
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

    function parseCreatedAtMs(createdAt) {
        if (!createdAt) return null;
        // '2025-01-01 23:59:00Z' → Date.parse 可能な ISO 形式へ
        const normalized = createdAt.includes('T')
            ? createdAt
            : createdAt.replace(' ', 'T');
        const ms = Date.parse(normalized);
        return Number.isNaN(ms) ? null : ms;
    }

    function getCloseCooldownRemainingSeconds(btn) {
        const createdMs = parseCreatedAtMs(btn.dataset.createdAt);
        if (createdMs == null) return 0;
        const cooldownSeconds = parseInt(btn.dataset.closeCooldownSeconds || '120', 10);
        const elapsed = (Date.now() - createdMs) / 1000;
        return Math.max(0, Math.ceil(cooldownSeconds - elapsed));
    }

    function syncCloseButtonCooldownState(btn) {
        if (!btn || !btn.classList.contains('post-card__close-button')) return;
        const remaining = getCloseCooldownRemainingSeconds(btn);
        if (remaining > 0) {
            btn.classList.add('disabled');
            if (!btn._closeCooldownTimer) {
                btn._closeCooldownTimer = setTimeout(() => {
                    btn._closeCooldownTimer = null;
                    syncCloseButtonCooldownState(btn);
                }, remaining * 1000);
            }
        } else {
            btn.classList.remove('disabled');
            if (btn._closeCooldownTimer) {
                clearTimeout(btn._closeCooldownTimer);
                btn._closeCooldownTimer = null;
            }
        }
    }

    function scheduleCloseButtonCooldowns(root) {
        if (!root) return;
        root.querySelectorAll('.post-card__close-button[data-created-at]').forEach((btn) => {
            syncCloseButtonCooldownState(btn);
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
        if (window.teamBoardFragment) {
            return window.teamBoardFragment.reloadPosts();
        }
        return Promise.resolve();
    }

    window.teamBoardFragmentLoader = function teamBoardFragmentLoader(config) {
        const {
            fragmentBaseUrl,
            lang,
            category: initialCategory,
            mode: initialMode,
            filter: initialFilter,
            limit,
            region,
            eliminateDuplicates,
            onlyJoinable: initialOnlyJoinable,
        } = config;

        const normalizedInitial = normalizeTeamFilter(initialFilter || 'all', Boolean(initialOnlyJoinable));

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
            scheduleCloseButtonCooldowns(container);
            agoIntervalId = setInterval(() => updatePostAgoTexts(container, lang), AGO_UPDATE_INTERVAL_MS);
        };

        const loader = {
            isLoading: true,
            hasError: false,
            category: initialCategory,
            mode: initialMode,
            filter: normalizedInitial.filter,
            limit,
            region,
            eliminateDuplicates,
            onlyJoinable: normalizedInitial.onlyJoinable,

            getQueryParams() {
                return {
                    category: this.category,
                    mode: this.mode,
                    filter: this.filter,
                    limit: this.limit,
                    region: this.region,
                    eliminate_duplicates: String(this.eliminateDuplicates).toLowerCase(),
                    only_joinable: String(this.onlyJoinable).toLowerCase(),
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
                    link.classList.toggle('sub-tab-nav__link--active', link.dataset.boardTab === this.category);
                });

                const filterSelect = document.getElementById('filterSelect');
                const categoryInput = document.querySelector('#filterForm input[name="category"]');
                const modeInput = document.querySelector('#filterForm input[name="mode"]');
                const onlyJoinableInput = document.getElementById('onlyJoinableInput');
                const onlyJoinableCheckbox = document.getElementById('onlyJoinableCheckbox');
                if (filterSelect) filterSelect.value = this.filter;
                if (categoryInput) categoryInput.value = this.category;
                if (modeInput) modeInput.value = this.mode;
                if (onlyJoinableInput) onlyJoinableInput.value = String(this.onlyJoinable);
                if (onlyJoinableCheckbox) onlyJoinableCheckbox.checked = this.onlyJoinable;
            },

            applyStateFromUrl() {
                const params = new URLSearchParams(window.location.search);
                this.category = params.get('category') || initialCategory || 'all';
                this.mode = params.get('mode') || initialMode || 'all';
                const oj = params.get('only_joinable');
                let onlyJoinable = oj === 'true' || oj === '1';
                const normalized = normalizeTeamFilter(params.get('filter') || initialFilter || 'all', onlyJoinable);
                this.filter = normalized.filter;
                this.onlyJoinable = normalized.onlyJoinable;
                const ed = params.get('eliminate_duplicates');
                if (ed !== null) {
                    this.eliminateDuplicates = ed === 'true' || ed === '1';
                }
            },

            persistPrefs() {
                writeStoredPrefs(this.category, this.filter, this.onlyJoinable);
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
                        history.pushState({ teamBoard: this.getQueryParams() }, '', this.buildShellUrl());
                    }
                } catch (error) {
                    if (error.name === 'AbortError') return;
                    console.error('Team board fragment load error:', error);
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

            setCategory(newCategory) {
                if (newCategory === this.category) {
                    return this.reloadPosts();
                }
                this.category = newCategory;
                return this.load({ updateHistory: true });
            },

            setFilter(newFilter) {
                const normalized = normalizeTeamFilter(newFilter, this.onlyJoinable);
                if (normalized.filter === this.filter && normalized.onlyJoinable === this.onlyJoinable) {
                    return Promise.resolve();
                }
                this.filter = normalized.filter;
                this.onlyJoinable = normalized.onlyJoinable;
                return this.load({ updateHistory: true });
            },

            setOnlyJoinable(enabled) {
                const next = Boolean(enabled);
                if (next === this.onlyJoinable) return Promise.resolve();
                this.onlyJoinable = next;
                return this.load({ updateHistory: true });
            },

            navigateAfterPost(postedCategory) {
                if (this.category !== 'all' && this.category !== postedCategory) {
                    this.category = 'all';
                }
                return this.load({ updateHistory: true });
            },

            init() {
                window.teamBoardFragment = this;

                const params = new URLSearchParams(window.location.search);
                if (!params.has('category')) {
                    const storedCategory = readStoredPref(STORAGE_KEY_CATEGORY, VALID_CATEGORIES);
                    if (storedCategory) this.category = storedCategory;
                }
                if (!params.has('filter')) {
                    let storedFilter = null;
                    try {
                        storedFilter = localStorage.getItem(STORAGE_KEY_FILTER);
                    } catch {
                        /* ignore */
                    }
                    if (storedFilter === 'only_can_participate') {
                        this.filter = 'all';
                        if (!params.has('only_joinable')) this.onlyJoinable = true;
                    } else {
                        const validStored = readStoredPref(STORAGE_KEY_FILTER, VALID_FILTERS);
                        if (validStored) this.filter = validStored;
                    }
                }
                if (!params.has('only_joinable') && params.get('filter') !== 'only_can_participate') {
                    const storedJoinable = readStoredBool(STORAGE_KEY_ONLY_JOINABLE);
                    if (storedJoinable !== null) this.onlyJoinable = storedJoinable;
                }

                this.persistPrefs();
                this.syncShellUi();
                history.replaceState({ teamBoard: this.getQueryParams() }, '', this.buildShellUrl());
                return this.load({ updateHistory: false }).then(() => {
                    return window.BoardFragmentPagination?.restoreAfterChat?.(this);
                });
            },
        };

        if (window.BoardFragmentPagination) {
            return window.BoardFragmentPagination.enhanceBoardFragmentLoader(loader, {
                fragmentBaseUrl,
                lang,
                updatePostAgoTexts: (root, langArg) => {
                    updatePostAgoTexts(root, langArg);
                    scheduleCloseButtonCooldowns(root);
                },
                getContentRoot: () => document.querySelector('#team-board-posts-root [x-ref="content"]'),
            });
        }
        return loader;
    };

    function bindPostActionDelegation(config) {
        if (postDelegationBound) return;
        const root = document.getElementById('team-board-posts-root');
        if (!root) return;

        const { lang, blockUserUrl } = config;
        postDelegationBound = true;

        root.addEventListener('click', async (event) => {
            const deleteBtn = event.target.closest('.post-card__delete-button');
            if (deleteBtn) {
                const postId = deleteBtn.dataset.postId;
                const msg = lang === 'ja'
                    ? 'この投稿を削除しますか？\nこの操作は取り消せません。'
                    : 'Are you sure you want to delete this post?\nThis action cannot be undone.';
                if (!confirm(msg)) return;
                try {
                    const response = await fetch(`/${lang}/boards/posts/${postId}`, { method: 'DELETE' });
                    if (response.ok) await reloadPostsFromFragment();
                    else {
                        const errorData = await response.json().catch(() => ({}));
                        alert(lang === 'ja'
                            ? `削除に失敗しました: ${errorData.detail || ''}`
                            : `Failed to delete: ${errorData.detail || ''}`);
                    }
                } catch {
                    alert(lang === 'ja' ? '削除中にエラーが発生しました。' : 'An error occurred while deleting.');
                }
                return;
            }

            const closeBtn = event.target.closest('.post-card__close-button');
            if (closeBtn) {
                const remaining = getCloseCooldownRemainingSeconds(closeBtn);
                if (remaining > 0) {
                    alert(lang === 'ja'
                        ? '投稿直後は募集を終了できません。しばらくお待ちください。'
                        : "You can't close a post right after posting. Please wait.");
                    return;
                }
                const postId = closeBtn.dataset.postId;
                const msg = lang === 'ja'
                    ? 'この募集を終了しますか？'
                    : 'End this recruitment?';
                if (!confirm(msg)) return;
                try {
                    const response = await fetch(`/${lang}/boards/posts/${postId}/close`, { method: 'POST' });
                    if (response.ok) await reloadPostsFromFragment();
                    else {
                        const errorData = await response.json().catch(() => ({}));
                        alert(lang === 'ja'
                            ? `募集終了に失敗しました: ${errorData.detail || ''}`
                            : `Failed to close: ${errorData.detail || ''}`);
                    }
                } catch {
                    alert(lang === 'ja' ? '募集終了中にエラーが発生しました。' : 'An error occurred while closing.');
                }
                return;
            }

            const reopenBtn = event.target.closest('.post-card__reopen-button');
            if (reopenBtn) {
                const postId = reopenBtn.dataset.postId;
                const msg = lang === 'ja'
                    ? 'この募集を再開しますか？'
                    : 'Reopen this recruitment?';
                if (!confirm(msg)) return;
                try {
                    const response = await fetch(`/${lang}/boards/posts/${postId}/reopen`, { method: 'POST' });
                    if (response.ok) await reloadPostsFromFragment();
                    else {
                        const errorData = await response.json().catch(() => ({}));
                        alert(lang === 'ja'
                            ? `募集再開に失敗しました: ${errorData.detail || ''}`
                            : `Failed to reopen: ${errorData.detail || ''}`);
                    }
                } catch {
                    alert(lang === 'ja' ? '募集再開中にエラーが発生しました。' : 'An error occurred while reopening.');
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
                    ? 'この投稿者を本当にブロックしますか？'
                    : 'Are you sure you want to block this poster?';
                if (!confirm(msg)) return;
                try {
                    const response = await fetch(blockUserUrl, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            blocked_user_id: blockBtn.dataset.blockedUserId
                                ? parseInt(blockBtn.dataset.blockedUserId, 10)
                                : null,
                            blocked_anonymous_id: blockBtn.dataset.blockedAnonymousId || null,
                        }),
                    });
                    if (response.ok) {
                        await reloadPostsFromFragment();
                    } else {
                        const errorData = await response.json().catch(() => ({}));
                        if (errorData.success === false) {
                            alert(lang === 'ja'
                                ? 'このユーザーはすでにブロックされています。'
                                : 'This user is already blocked.');
                            await reloadPostsFromFragment();
                        } else {
                            alert(lang === 'ja' ? 'ユーザーのブロックに失敗しました。' : 'Failed to block user.');
                        }
                    }
                } catch {
                    alert(lang === 'ja' ? 'ユーザーのブロックに失敗しました。' : 'Failed to block user.');
                }
            }
        });
    }

    function bindShellControls(config) {
        const { checkPostPermissionUrl, lang } = config;

        document.querySelectorAll('[data-board-tab]').forEach((link) => {
            link.addEventListener('click', (event) => {
                event.preventDefault();
                const category = link.dataset.boardTab;
                if (window.teamBoardFragment && category) {
                    window.teamBoardFragment.setCategory(category);
                }
            });
        });

        const filterSelect = document.getElementById('filterSelect');
        const filterForm = document.getElementById('filterForm');
        if (filterSelect) {
            filterSelect.addEventListener('change', () => {
                if (window.teamBoardFragment) {
                    window.teamBoardFragment.setFilter(filterSelect.value);
                }
            });
        }
        if (filterForm) {
            filterForm.addEventListener('submit', (event) => {
                event.preventDefault();
            });
        }

        const onlyJoinableCheckbox = document.getElementById('onlyJoinableCheckbox');
        if (onlyJoinableCheckbox) {
            onlyJoinableCheckbox.addEventListener('change', () => {
                if (window.teamBoardFragment) {
                    window.teamBoardFragment.setOnlyJoinable(onlyJoinableCheckbox.checked);
                }
            });
        }

        const reloadButton = document.querySelector('.reload-button__container');
        if (reloadButton) {
            reloadButton.addEventListener('click', async () => {
                if (reloadButton.classList.contains('reload-button__container--disabled')) return;
                if (!window.teamBoardFragment) return;

                reloadButton.classList.add('reload-button__container--disabled');
                try {
                    await window.teamBoardFragment.reloadPosts();
                } finally {
                    setTimeout(() => {
                        reloadButton.classList.remove('reload-button__container--disabled');
                    }, RELOAD_BUTTON_COOLDOWN_MS);
                }
            });
        }

        window.addEventListener('popstate', (event) => {
            if (!window.teamBoardFragment) return;
            if (event.state?.teamBoard) {
                const state = event.state.teamBoard;
                window.teamBoardFragment.category = state.category || 'all';
                window.teamBoardFragment.mode = state.mode || 'all';
                const normalized = normalizeTeamFilter(
                    state.filter || 'all',
                    state.only_joinable === 'true' || state.only_joinable === true,
                );
                window.teamBoardFragment.filter = normalized.filter;
                window.teamBoardFragment.onlyJoinable = normalized.onlyJoinable;
                window.teamBoardFragment.eliminateDuplicates = state.eliminate_duplicates === 'true'
                    || state.eliminate_duplicates === true;
            } else {
                window.teamBoardFragment.applyStateFromUrl();
            }
            window.teamBoardFragment.load({ updateHistory: false });
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
                            document.dispatchEvent(new CustomEvent('team-board:open-post-modal'));
                        } else if (data.cooldown > 0) {
                            const minutes = Math.floor(data.cooldown / 60);
                            const seconds = data.cooldown % 60;
                            const alertMessage = minutes > 0
                                ? (lang === 'ja'
                                    ? `現在クールタイム中です。次の投稿まであと${minutes}分${seconds}秒お待ちください。`
                                    : `Please wait ${minutes}m ${seconds}s for the next post.`)
                                : (lang === 'ja'
                                    ? `現在クールタイム中です。次の投稿まであと${seconds}秒お待ちください。`
                                    : `Please wait ${seconds}s for the next post.`);
                            alert(alertMessage);
                        } else {
                            document.dispatchEvent(new CustomEvent('team-board:post-restricted'));
                        }
                    } catch {
                        alert(lang === 'ja' ? 'エラーが発生しました。時間をおいて再試行してください。' : 'An error occurred. Please try again later.');
                    }
                });
            }
        }
    }

    window.initTeamBoardShell = function initTeamBoardShell(config) {
        bindPostActionDelegation(config);
        bindShellControls(config);
    };

    window.scheduleTeamBoardFabCooldown = scheduleFabCooldownRelease;

    window.navigateTeamBoardAfterPost = function navigateTeamBoardAfterPost(postedCategory) {
        if (window.teamBoardFragment) {
            return window.teamBoardFragment.navigateAfterPost(postedCategory);
        }
        return Promise.resolve();
    };

    window.fetchTeamBoardCooldown = async function fetchTeamBoardCooldown(checkPostPermissionUrl) {
        try {
            const response = await fetch(checkPostPermissionUrl);
            const data = await response.json();
            return data.cooldown > 0 ? data.cooldown : 0;
        } catch {
            return 0;
        }
    };
})();
