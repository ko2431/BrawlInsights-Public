/**
 * テーマ掲示板 Shell + Fragment 制御
 */
(function () {
    'use strict';

    const RELOAD_BUTTON_COOLDOWN_MS = 1500;
    const AGO_UPDATE_INTERVAL_MS = 60000;
    const STORAGE_KEY_TAB = 'themeBoardTab';
    const DEFAULT_TAB = 'latest';
    const VALID_TABS = new Set(['latest', 'brawlers', 'maps', 'participated', 'liked']);
    // 索引から検索するタブ（カードに無いキャラ・マップも検索でヒットさせる）
    const INDEX_SEARCH_TABS = new Set(['latest', 'maps']);
    // 検索時に、カードに無い掲示板を索引から表示する最大件数
    const INDEX_RESULT_LIMIT = 30;

    let postDelegationBound = false;
    let indexSearchConfig = null;
    let themeIndexPromise = null;
    let indexSearchSeq = 0;

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

    function currentTab() {
        return window.themeBoardFragment ? window.themeBoardFragment.tab : null;
    }

    /**
     * テーマ掲示板の検索索引（全マップ・全キャラ）を取得する。最初の検索時に1回だけ読み込み、以降は使い回す。
     * 形: { modes: { id: {ja, en, icons, c1, c2} }, maps: [[id, ja, en, modeId, off], ...],
     *       brawlers: [[id, en, [ja名...], c1, c2], ...] }
     */
    function loadThemeIndex() {
        if (!themeIndexPromise) {
            themeIndexPromise = fetch(indexSearchConfig.indexUrl)
                .then((response) => {
                    if (!response.ok) throw new Error('theme board index fetch failed');
                    return response.json();
                })
                .then((data) => {
                    const modes = data.modes || {};
                    const maps = (data.maps || []).map(([id, ja, en, modeId, off]) => {
                        const mode = modes[String(modeId)] || {};
                        return {
                            id,
                            ja: ja || en || '',
                            en: en || ja || '',
                            mode,
                            off: Boolean(off),
                            nameKey: normalizeText(`${ja || ''}\n${en || ''}`),
                            modeKey: normalizeText(`${mode.ja || ''}\n${mode.en || ''}`),
                        };
                    });
                    const brawlers = (data.brawlers || []).map(([id, en, namesJa, c1, c2]) => ({
                        id,
                        en: en || '',
                        ja: (namesJa && namesJa[0]) || en || '',
                        c1,
                        c2,
                        nameKey: normalizeText([en || '', ...(namesJa || [])].join('\n')),
                    }));
                    return { maps, brawlers };
                })
                .catch((error) => {
                    themeIndexPromise = null;
                    throw error;
                });
        }
        return themeIndexPromise;
    }

    // 名前の先頭一致（改行区切りの各名前の先頭）なら 0、部分一致なら 1、一致しなければ -1
    function nameMatchRank(nameKey, query) {
        const index = nameKey.indexOf(query);
        if (index === -1) return -1;
        return index === 0 || nameKey.includes(`\n${query}`) ? 0 : 1;
    }

    function searchMapIndex(maps, query, excludedIds) {
        const hits = [];
        maps.forEach((map, order) => {
            if (excludedIds.has(String(map.id))) return;
            const nameRank = nameMatchRank(map.nameKey, query);
            if (nameRank === -1 && !map.modeKey.includes(query)) return;
            // 名前の先頭一致 → 名前の部分一致 → モード名一致の順。同順位なら現行マップ → 索引順
            const rank = nameRank === -1 ? 2 : nameRank;
            hits.push({ map, rank, order });
        });
        hits.sort((a, b) => a.rank - b.rank || Number(a.map.off) - Number(b.map.off) || a.order - b.order);
        return hits.map((hit) => hit.map);
    }

    function searchBrawlerIndex(brawlers, query, excludedIds) {
        const hits = [];
        brawlers.forEach((brawler, order) => {
            if (excludedIds.has(String(brawler.id))) return;
            const rank = nameMatchRank(brawler.nameKey, query);
            if (rank !== -1) hits.push({ brawler, rank, order });
        });
        hits.sort((a, b) => a.rank - b.rank || a.order - b.order);
        return hits.map((hit) => hit.brawler);
    }

    /** 索引からヒットした掲示板の簡易カード（アイコン・名前・「掲示板へ」ボタンのみ） */
    function buildIndexCard({ name, subtitle, icons, iconClass, c1, c2, href }) {
        const wrapper = document.createElement('div');
        wrapper.className = 'post-card-wrapper theme-board-index-card';

        const card = document.createElement('div');
        card.className = 'card post-card post-card--theme post-card--tinted';
        card.style.setProperty('--board-c1', c1 || '#e8e8e8');
        card.style.setProperty('--board-c2', c2 || '#dcdcdc');

        const header = document.createElement('div');
        header.className = 'post-card__header';
        const icon = document.createElement('img');
        icon.className = `post-card__player-icon ${iconClass}`;
        icon.alt = name;
        icon.setAttribute('data-icon-fallbacks', JSON.stringify(icons.slice(1)));
        icon.setAttribute('onerror', 'handleModeIconError(this)');
        icon.src = indexSearchConfig.staticPrefix + icons[0];
        const title = document.createElement('p');
        title.className = 'post-card__title';
        title.textContent = name;
        header.append(icon, title);
        if (subtitle) {
            const sub = document.createElement('span');
            sub.className = 'theme-board-index-card__mode';
            sub.textContent = subtitle;
            header.appendChild(sub);
        }

        const linkContainer = document.createElement('div');
        linkContainer.className = 'post-card__link-container';
        const link = document.createElement('a');
        link.href = href;
        link.rel = 'nofollow';
        link.style.display = 'flex';
        link.style.flex = '1';
        const button = document.createElement('div');
        button.className = 'button button--primary post-card__link-button';
        button.textContent = indexSearchConfig.lang === 'ja' ? '掲示板へ' : 'Go to Board';
        link.appendChild(button);
        linkContainer.appendChild(link);

        card.append(header, linkContainer);
        wrapper.appendChild(card);
        return wrapper;
    }

    function buildMapIndexCard(map) {
        const isJa = indexSearchConfig.lang === 'ja';
        return buildIndexCard({
            name: isJa ? map.ja : map.en,
            subtitle: (isJa ? map.mode.ja : map.mode.en) || '',
            icons: map.mode.icons && map.mode.icons.length ? map.mode.icons : ['/images/ui/mystery.png'],
            iconClass: 'post-card__player-icon--mode',
            c1: map.mode.c1,
            c2: map.mode.c2,
            href: `${indexSearchConfig.mapBoardBaseUrl}${map.id}`,
        });
    }

    function buildBrawlerIndexCard(brawler) {
        return buildIndexCard({
            name: indexSearchConfig.lang === 'ja' ? brawler.ja : brawler.en,
            subtitle: '',
            icons: [`/images/brawler_pins/${brawler.id}.png`, '/images/ui/mystery.png'],
            iconClass: 'post-card__player-icon--brawler',
            c1: brawler.c1,
            c2: brawler.c2,
            href: `${indexSearchConfig.brawlerBoardBaseUrl}${brawler.id}`,
        });
    }

    /** 最新・マップタブ: カードに無い掲示板を索引から検索して簡易カードで表示する */
    function renderIndexResults(tab, query, shown, visibleCardCount) {
        const container = document.getElementById('themeBoardIndexResults');
        const emptyText = document.getElementById('themeBoardEmptyText');
        const noResults = document.getElementById('themeBoardNoResults');
        const seq = ++indexSearchSeq;
        if (!container || !indexSearchConfig) return;

        if (query === '') {
            container.hidden = true;
            container.replaceChildren();
            if (emptyText) emptyText.hidden = false;
            if (noResults) noResults.hidden = true;
            return;
        }
        if (emptyText) emptyText.hidden = true;

        loadThemeIndex()
            .then(({ maps, brawlers }) => {
                if (seq !== indexSearchSeq) return;
                // 最新タブはキャラ → マップの順（キャラは数が少なく、名前での検索が多いため）
                const cards = [];
                if (tab === 'latest') {
                    searchBrawlerIndex(brawlers, query, shown.brawlerIds)
                        .forEach((brawler) => cards.push(buildBrawlerIndexCard(brawler)));
                }
                searchMapIndex(maps, query, shown.mapIds)
                    .slice(0, Math.max(0, INDEX_RESULT_LIMIT - cards.length))
                    .forEach((map) => cards.push(buildMapIndexCard(map)));
                const limited = cards.slice(0, INDEX_RESULT_LIMIT);
                container.replaceChildren(...limited);
                container.hidden = limited.length === 0;
                if (noResults) noResults.hidden = visibleCardCount + limited.length > 0;
            })
            .catch(() => {
                if (seq !== indexSearchSeq) return;
                container.hidden = true;
                if (noResults) noResults.hidden = visibleCardCount > 0;
            });
    }

    function applyThemeBoardSearchFilter() {
        const searchInput = document.getElementById('themeSearchInput');
        const postsContainer = document.getElementById('themeBoardPosts');
        const cards = document.querySelectorAll('.theme-board-card');
        const noResults = document.getElementById('themeBoardNoResults');
        if (!searchInput) return;

        const query = normalizeText(searchInput.value.trim());
        let visibleCount = 0;
        const shown = { mapIds: new Set(), brawlerIds: new Set() };

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
            if (isVisible) {
                visibleCount += 1;
                if (card.dataset.mapId) shown.mapIds.add(card.dataset.mapId);
                if (card.dataset.brawlerId) shown.brawlerIds.add(card.dataset.brawlerId);
            }
        });

        const tab = currentTab();
        if (INDEX_SEARCH_TABS.has(tab)) {
            // 表示するカードが無いときはコンテナごと隠し、余白が二重にならないようにする
            if (postsContainer) postsContainer.hidden = visibleCount === 0;
            renderIndexResults(tab, query, shown, visibleCount);
            return;
        }
        if (noResults) noResults.hidden = cards.length === 0 || visibleCount !== 0;
    }

    function placeholderForTab(tab, lang) {
        if (tab === 'brawlers') {
            return lang === 'ja' ? 'キャラクター名で検索...' : 'Search by brawler name...';
        }
        if (tab === 'maps') {
            return lang === 'ja' ? 'マップ名・モード名で検索...' : 'Search by map or mode name...';
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

    function waitForPaint() {
        return new Promise((resolve) => {
            requestAnimationFrame(() => {
                requestAnimationFrame(resolve);
            });
        });
    }

    window.themeBoardFragmentLoader = function themeBoardFragmentLoader(config) {
        const {
            fragmentBaseUrl,
            indexUrl,
            mapBoardBaseUrl,
            brawlerBoardBaseUrl,
            staticPrefix,
            lang,
            tab: initialTab,
        } = config;

        indexSearchConfig = {
            indexUrl,
            mapBoardBaseUrl,
            brawlerBoardBaseUrl,
            staticPrefix: (staticPrefix || '/static').replace(/\/+$/, ''),
            lang,
        };

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
                this.tab = params.get('tab') || initialTab || DEFAULT_TAB;
                if (!VALID_TABS.has(this.tab)) this.tab = DEFAULT_TAB;
            },

            persistPrefs() {
                writeStoredTab(this.tab);
            },

            async load({ updateHistory = false } = {}) {
                if (abortController) abortController.abort();
                abortController = new AbortController();
                const signal = abortController.signal;
                const currentLoadId = ++loadId;

                this.isLoading = true;
                this.hasError = false;
                this.persistPrefs();
                this.syncShellUi();

                if (updateHistory) {
                    history.pushState({ themeBoard: this.getQueryParams() }, '', this.buildShellUrl());
                }

                // Alpine の x-init は x-show / x-if より先に走る。テーマ掲示板は fragment が速く、
                // 待たないと初回ペイント時点で isLoading が false になりインジケーターが出ない。
                await waitForPaint();
                if (currentLoadId !== loadId) return;

                const contentRoot = this.$refs?.content;
                try {
                    const response = await fetch(this.buildFragmentUrl(), { signal });
                    if (!response.ok) throw new Error('fragment fetch failed');
                    const html = await response.text();
                    if (currentLoadId !== loadId) return;
                    if (contentRoot) {
                        contentRoot.innerHTML = html;
                        injectFragmentScripts(contentRoot);
                        startAgoUpdater(contentRoot);
                    }
                    applyThemeBoardSearchFilter();
                    this.hasError = false;
                } catch (error) {
                    if (error?.name === 'AbortError' || currentLoadId !== loadId) return;
                    this.hasError = true;
                    if (contentRoot) contentRoot.innerHTML = '';
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
                if (!VALID_TABS.has(this.tab)) this.tab = DEFAULT_TAB;

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
            // モーダルを戻る操作で閉じた場合は一覧を読み込み直さない
            if (window.BrawlInsightsModalBack?.consumePopState(event)) return;
            if (!window.themeBoardFragment) return;
            if (event.state?.themeBoard) {
                window.themeBoardFragment.tab = event.state.themeBoard.tab || DEFAULT_TAB;
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
