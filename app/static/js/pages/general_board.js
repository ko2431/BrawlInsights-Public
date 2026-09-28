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

    // 検索（サーバー側 app/core/text_search.py と同じ規則で正規化する）
    const SEARCH_MAX_LENGTH = 50;
    const SEARCH_MAX_TERMS = 5;
    const MOBILE_MAX_WIDTH_QUERY = '(max-width: 539px)';
    const SEARCH_HIT_CLASS = 'general-board-search-hit';
    const HIGHLIGHTED_ATTR = 'data-search-highlighted';
    // 半角・全角の濁点／半濁点（直前の文字と合わせて NFKC する）
    const COMBINING_KANA_MARK_RE = /[\u3099\u309A\uFF9E\uFF9F]/;

    let fabCooldownTimer = null;
    let postDelegationBound = false;

    /** NFKC → ASCII 大文字を小文字 → カタカナをひらがな */
    function normalizeSearchText(text) {
        let out = '';
        for (const ch of String(text).normalize('NFKC')) {
            const code = ch.codePointAt(0);
            if (code >= 0x41 && code <= 0x5A) out += String.fromCharCode(code + 0x20);
            else if (code >= 0x30A1 && code <= 0x30F6) out += String.fromCharCode(code - 0x60);
            else out += ch;
        }
        return out;
    }

    function normalizeQuery(q) {
        return String(q || '').trim().slice(0, SEARCH_MAX_LENGTH).trim();
    }

    function isUserSearch(q) {
        return q.startsWith('@') || q.startsWith('＠');
    }

    /** 本文検索の語（正規化済み）。@検索や空の場合は空配列 */
    function getSearchTerms(q) {
        const normalizedQuery = normalizeQuery(q);
        if (!normalizedQuery || isUserSearch(normalizedQuery)) return [];
        const terms = [];
        for (const part of normalizedQuery.replace(/\u3000/g, ' ').split(/\s+/)) {
            const term = normalizeSearchText(part);
            if (!term.trim() || terms.includes(term)) continue;
            terms.push(term);
            if (terms.length >= SEARCH_MAX_TERMS) break;
        }
        return terms;
    }

    /**
     * テキストを正規化し、正規化後の各文字が元テキストのどの範囲に対応するかを返す。
     * 濁点などの結合文字は直前の文字とまとめて正規化する（ｶﾞ → が）。
     */
    function buildNormalizedIndex(text) {
        let normalized = '';
        const starts = [];
        const ends = [];
        const chars = Array.from(text);
        let pos = 0;
        for (let i = 0; i < chars.length; i++) {
            let cluster = chars[i];
            while (i + 1 < chars.length && COMBINING_KANA_MARK_RE.test(chars[i + 1])) {
                cluster += chars[++i];
            }
            const start = pos;
            pos += cluster.length;
            const normalizedCluster = normalizeSearchText(cluster);
            for (let j = 0; j < normalizedCluster.length; j++) {
                starts.push(start);
                ends.push(pos);
            }
            normalized += normalizedCluster;
        }
        return { normalized, starts, ends };
    }

    function findHitRanges(text, terms) {
        const { normalized, starts, ends } = buildNormalizedIndex(text);
        const ranges = [];
        terms.forEach((term) => {
            let from = 0;
            while (term && from <= normalized.length - term.length) {
                const index = normalized.indexOf(term, from);
                if (index < 0) break;
                ranges.push([starts[index], ends[index + term.length - 1]]);
                from = index + term.length;
            }
        });
        ranges.sort((a, b) => a[0] - b[0]);
        const merged = [];
        ranges.forEach(([start, end]) => {
            const last = merged[merged.length - 1];
            if (last && start <= last[1]) last[1] = Math.max(last[1], end);
            else merged.push([start, end]);
        });
        return merged;
    }

    function highlightTextNode(node, terms) {
        const text = node.nodeValue;
        const ranges = findHitRanges(text, terms);
        if (!ranges.length) return null;
        const fragment = document.createDocumentFragment();
        let firstMark = null;
        let cursor = 0;
        ranges.forEach(([start, end]) => {
            if (start > cursor) fragment.appendChild(document.createTextNode(text.slice(cursor, start)));
            const mark = document.createElement('mark');
            mark.className = SEARCH_HIT_CLASS;
            mark.textContent = text.slice(start, end);
            fragment.appendChild(mark);
            if (!firstMark) firstMark = mark;
            cursor = end;
        });
        if (cursor < text.length) fragment.appendChild(document.createTextNode(text.slice(cursor)));
        node.parentNode.replaceChild(fragment, node);
        return firstMark;
    }

    /** 投稿カードのコメント内の検索ヒット箇所を蛍光ペン風にハイライトする（再実行しても二重にならない） */
    function highlightSearchHits(root, q) {
        const terms = getSearchTerms(q);
        if (!root || !terms.length) return;
        root.querySelectorAll(`.post-card__comment-text:not([${HIGHLIGHTED_ATTR}])`).forEach((el) => {
            el.setAttribute(HIGHLIGHTED_ATTR, '1');
            const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
            const textNodes = [];
            while (walker.nextNode()) textNodes.push(walker.currentNode);
            let firstMark = null;
            textNodes.forEach((node) => {
                const mark = highlightTextNode(node, terms);
                if (!firstMark) firstMark = mark;
            });
            // 最初のヒットがスクロール領域外なら、領域内だけをスクロールして見せる
            const scrollArea = el.closest('.post-card__host-info-scroll-area');
            if (firstMark && scrollArea && scrollArea.scrollHeight > scrollArea.clientHeight) {
                const markTop = firstMark.getBoundingClientRect().top - scrollArea.getBoundingClientRect().top + scrollArea.scrollTop;
                if (markTop + firstMark.offsetHeight > scrollArea.clientHeight) {
                    scrollArea.scrollTop = Math.max(0, markTop - 8);
                }
            }
        });
    }

    function isMobileLayout() {
        return window.matchMedia?.(MOBILE_MAX_WIDTH_QUERY).matches ?? false;
    }

    function setSearchRowOpen(open, { focus = false } = {}) {
        const form = document.getElementById('generalSearchForm');
        const toggle = document.getElementById('generalSearchToggle');
        if (form) form.classList.toggle('general-board-search-form--open', open);
        if (toggle) toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (open && focus) {
            document.getElementById('generalSearchInput')?.focus();
        }
    }

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
            q: initialQuery = '',
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
            q: normalizeQuery(initialQuery),
            limit,
            region,
            eliminateDuplicates,

            getQueryParams() {
                return {
                    tab: this.tab,
                    filter: this.filter,
                    q: this.q || undefined,
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
                const filterQueryInput = document.querySelector('#filterForm input[name="q"]');
                if (filterSelect) filterSelect.value = this.filter;
                if (tabInput) tabInput.value = this.tab;
                if (filterQueryInput) filterQueryInput.value = this.q;

                const searchInput = document.getElementById('generalSearchInput');
                const clearButton = document.getElementById('generalSearchClear');
                const toggle = document.getElementById('generalSearchToggle');
                if (searchInput) searchInput.value = this.q;
                if (clearButton) clearButton.hidden = !(this.q || searchInput?.value);
                if (toggle) toggle.classList.toggle('general-board-search-toggle--active', Boolean(this.q));
                if (this.q) setSearchRowOpen(true);
            },

            applyStateFromUrl() {
                const params = new URLSearchParams(window.location.search);
                this.tab = params.get('tab') || initialTab || 'latest';
                this.filter = params.get('filter') || initialFilter || DEFAULT_FILTER;
                this.q = normalizeQuery(params.get('q'));
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
                    highlightSearchHits(container, this.q);
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

            setSearch(newQuery) {
                const q = normalizeQuery(newQuery);
                if (q === this.q) {
                    this.syncShellUi();
                    return Promise.resolve();
                }
                this.q = q;
                return this.load({ updateHistory: true });
            },

            navigateAfterPost(postedCategory) {
                if (this.tab !== 'own') {
                    this.tab = 'latest';
                }
                // 投稿後は自分の投稿が見えるよう検索を解除する
                this.q = '';
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
                // 検索状態は URL のみで保持する（localStorage には保存しない）
                this.q = normalizeQuery(params.get('q'));
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
                return this.load({ updateHistory: false }).then(() => {
                    return window.BoardFragmentPagination?.restoreAfterChat?.(this);
                });
            },
        };

        if (window.BoardFragmentPagination) {
            return window.BoardFragmentPagination.enhanceBoardFragmentLoader(loader, {
                fragmentBaseUrl,
                lang,
                // 「さらに表示」で追加されたカードにも検索ハイライトを付ける
                updatePostAgoTexts: (root, currentLang) => {
                    updatePostAgoTexts(root, currentLang);
                    highlightSearchHits(root, loader.q);
                },
                getContentRoot: () => document.querySelector('#general-board-posts-root [x-ref="content"]'),
            });
        }
        return loader;
    };

    function bindPostActionDelegation(config) {
        if (postDelegationBound) return;
        const root = document.getElementById('general-board-posts-root');
        if (!root) return;

        const { lang, blockUserUrl } = config;
        postDelegationBound = true;

        const searchByAuthor = (authorEl) => {
            const name = authorEl?.dataset.authorName;
            if (!name || !window.generalBoardFragment) return;
            setSearchRowOpen(true);
            window.generalBoardFragment.setSearch(`@${name}`);
            document.querySelector('.board-filters__container--general')?.scrollIntoView({ block: 'nearest' });
        };
        root.addEventListener('keydown', (event) => {
            if (event.key !== 'Enter' && event.key !== ' ') return;
            const authorEl = event.target.closest('.general-post-card__author-name[data-author-name]');
            if (!authorEl) return;
            event.preventDefault();
            searchByAuthor(authorEl);
        });

        root.addEventListener('click', async (event) => {
            const authorEl = event.target.closest('.general-post-card__author-name[data-author-name]');
            if (authorEl) {
                searchByAuthor(authorEl);
                return;
            }

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

        const searchForm = document.getElementById('generalSearchForm');
        const searchInput = document.getElementById('generalSearchInput');
        const searchClear = document.getElementById('generalSearchClear');
        const searchToggle = document.getElementById('generalSearchToggle');
        if (searchForm && searchInput) {
            searchForm.addEventListener('submit', (event) => {
                event.preventDefault();
                if (!window.generalBoardFragment) return;
                window.generalBoardFragment.setSearch(searchInput.value);
                // スマホではキーボードを閉じて結果を見やすくする
                if (isMobileLayout()) searchInput.blur();
            });
            searchInput.addEventListener('input', () => {
                if (searchClear) searchClear.hidden = !(searchInput.value || window.generalBoardFragment?.q);
            });
        }
        if (searchClear && searchInput) {
            searchClear.addEventListener('click', () => {
                searchInput.value = '';
                searchClear.hidden = true;
                if (isMobileLayout()) setSearchRowOpen(false);
                else searchInput.focus();
                window.generalBoardFragment?.setSearch('');
            });
        }
        if (searchToggle && searchForm) {
            searchToggle.addEventListener('click', () => {
                const willOpen = !searchForm.classList.contains('general-board-search-form--open');
                if (willOpen) {
                    setSearchRowOpen(true, { focus: true });
                    return;
                }
                // 閉じるときは検索も解除する（非表示のまま絞り込まれた状態を残さない）
                setSearchRowOpen(false);
                if (searchInput) searchInput.value = '';
                if (searchClear) searchClear.hidden = true;
                window.generalBoardFragment?.setSearch('');
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
                window.generalBoardFragment.q = normalizeQuery(state.q);
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
