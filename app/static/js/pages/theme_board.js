/**
 * テーマ掲示板 Shell + Fragment 制御
 */
(function () {
    'use strict';

    const RELOAD_BUTTON_COOLDOWN_MS = 1500;
    const AGO_UPDATE_INTERVAL_MS = 60000;
    const STORAGE_KEY_TAB = 'themeBoardTab';
    const VALID_TABS = new Set(['brawlers', 'participated', 'liked']);

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

    function writeStoredTab(tab) {
        try {
            if (VALID_TABS.has(tab)) localStorage.setItem(STORAGE_KEY_TAB, tab);
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

    function hiraToKana(str) {
        return str.replace(/[\u3041-\u3096]/g, (match) =>
            String.fromCharCode(match.charCodeAt(0) + 0x60)
        );
    }

    function normalizeText(str) {
        return hiraToKana((str || '').toLowerCase());
    }

    function applyThemeBoardSearchFilter() {
        const searchInput = document.getElementById('themeSearchInput');
        const cards = document.querySelectorAll('.theme-board-card');
        const noResults = document.getElementById('themeBoardNoResults');
        if (!searchInput || cards.length === 0) return;

        const query = normalizeText(searchInput.value.trim());
        let visibleCount = 0;

        cards.forEach((card) => {
            const nameEn = normalizeText(card.dataset.nameEn || '');
            let namesJa = [];
            try {
                namesJa = JSON.parse(card.dataset.namesJa || '[]');
            } catch {
                namesJa = [];
            }
            const isMatchedEn = nameEn.includes(query);
            const isMatchedJa = namesJa.some((name) => normalizeText(name).includes(query));
            const isVisible = query === '' || isMatchedEn || isMatchedJa;
            card.style.display = isVisible ? '' : 'none';
            if (isVisible) visibleCount += 1;
        });

        if (noResults) noResults.hidden = visibleCount !== 0;
    }

    function placeholderForTab(tab, lang) {
        if (tab === 'brawlers') {
            return lang === 'ja' ? 'キャラクター名で検索...' : 'Search by brawler name...';
        }
        return lang === 'ja' ? '掲示板名で検索...' : 'Search by board name...';
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

    window.themeBoardFragmentLoader = function themeBoardFragmentLoader(config) {
        const {
            fragmentBaseUrl,
            lang,
            tab: initialTab,
        } = config;

        let abortController = null;
        let agoIntervalId = null;

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

            getQueryParams() {
                return { tab: this.tab };
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
                const tabInput = document.querySelector('#themeSearchForm input[name="tab"]');
                if (tabInput) tabInput.value = this.tab;
                const searchInput = document.getElementById('themeSearchInput');
                if (searchInput) searchInput.placeholder = placeholderForTab(this.tab, lang);
            },

            applyStateFromUrl() {
                const params = new URLSearchParams(window.location.search);
                this.tab = params.get('tab') || initialTab || 'brawlers';
                if (!VALID_TABS.has(this.tab)) this.tab = 'brawlers';
            },

            persistPrefs() {
                writeStoredTab(this.tab);
            },

            async load({ updateHistory = false } = {}) {
                if (abortController) abortController.abort();
                abortController = new AbortController();
                const signal = abortController.signal;

                this.isLoading = true;
                this.hasError = false;
                this.persistPrefs();
                this.syncShellUi();

                if (updateHistory) {
                    history.pushState({ themeBoard: this.getQueryParams() }, '', this.buildShellUrl());
                }

                const contentRoot = this.$refs?.content;
                try {
                    const response = await fetch(this.buildFragmentUrl(), { signal });
                    if (!response.ok) throw new Error('fragment fetch failed');
                    const html = await response.text();
                    if (contentRoot) {
                        contentRoot.innerHTML = html;
                        injectFragmentScripts(contentRoot);
                        startAgoUpdater(contentRoot);
                    }
                    applyThemeBoardSearchFilter();
                    this.hasError = false;
                } catch (error) {
                    if (error?.name === 'AbortError') return;
                    this.hasError = true;
                    if (contentRoot) contentRoot.innerHTML = '';
                    stopAgoUpdater();
                } finally {
                    this.isLoading = false;
                }
            },

            reloadPosts() {
                return this.load({ updateHistory: false });
            },

            setTab(newTab) {
                if (!VALID_TABS.has(newTab)) return Promise.resolve();
                if (newTab === this.tab) {
                    return this.reloadPosts();
                }
                this.tab = newTab;
                const searchInput = document.getElementById('themeSearchInput');
                if (searchInput) searchInput.value = '';
                return this.load({ updateHistory: true });
            },

            init() {
                window.themeBoardFragment = this;

                const params = new URLSearchParams(window.location.search);
                if (!params.has('tab')) {
                    const storedTab = readStoredPref(STORAGE_KEY_TAB, VALID_TABS);
                    if (storedTab) this.tab = storedTab;
                }
                if (!VALID_TABS.has(this.tab)) this.tab = 'brawlers';

                this.persistPrefs();
                this.syncShellUi();
                history.replaceState({ themeBoard: this.getQueryParams() }, '', this.buildShellUrl());
                return this.load({ updateHistory: false });
            },
        };

        return loader;
    };

    function bindPostActionDelegation(config) {
        if (postDelegationBound) return;
        const root = document.getElementById('theme-board-posts-root');
        if (!root) return;

        const { lang } = config;
        postDelegationBound = true;

        root.addEventListener('click', async (event) => {
            const goodBtn = event.target.closest('.post-card__good-button[data-post-id]');
            if (!goodBtn) return;
            if (goodBtn.classList.contains('disabled') || goodBtn.dataset.processing === '1') return;
            const postId = goodBtn.dataset.postId;
            const countEl = goodBtn.querySelector('.post-card__good-button-count');
            if (!postId || !countEl) return;

            goodBtn.dataset.processing = '1';
            try {
                const response = await fetch(`/${lang}/boards/posts/${postId}/good`, { method: 'POST' });
                if (!response.ok) {
                    if (response.status === 401) {
                        alert(lang === 'ja'
                            ? 'この機能を利用するにはログインが必要です。アカウントタブより、メールアドレス不要でログインできます。'
                            : 'You need to log in to use this feature. You can log in from the Account tab without an email address.');
                    } else {
                        alert(lang === 'ja'
                            ? '操作に失敗しました。投稿がすでに削除された可能性があります。'
                            : 'Failed to update good. The post may have been deleted.');
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
        });
    }

    function bindShellControls() {
        document.querySelectorAll('[data-board-tab]').forEach((link) => {
            link.addEventListener('click', (event) => {
                event.preventDefault();
                const tab = link.dataset.boardTab;
                if (window.themeBoardFragment && tab) {
                    window.themeBoardFragment.setTab(tab);
                }
            });
        });

        const searchInput = document.getElementById('themeSearchInput');
        if (searchInput) {
            searchInput.addEventListener('input', applyThemeBoardSearchFilter);
        }

        const reloadButton = document.querySelector('.reload-button__container');
        if (reloadButton) {
            reloadButton.addEventListener('click', async () => {
                if (reloadButton.classList.contains('reload-button__container--disabled')) return;
                if (!window.themeBoardFragment) return;

                reloadButton.classList.add('reload-button__container--disabled');
                try {
                    await window.themeBoardFragment.reloadPosts();
                } finally {
                    setTimeout(() => {
                        reloadButton.classList.remove('reload-button__container--disabled');
                    }, RELOAD_BUTTON_COOLDOWN_MS);
                }
            });
        }

        window.addEventListener('popstate', (event) => {
            if (!window.themeBoardFragment) return;
            if (event.state?.themeBoard) {
                window.themeBoardFragment.tab = event.state.themeBoard.tab || 'brawlers';
            } else {
                window.themeBoardFragment.applyStateFromUrl();
            }
            window.themeBoardFragment.load({ updateHistory: false });
        });
    }

    window.initThemeBoardShell = function initThemeBoardShell(config) {
        bindPostActionDelegation(config);
        bindShellControls();
    };
})();
