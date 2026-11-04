/* /app/static/js/shell_fragment_loader.js */
// プレイヤー・クラブのシェルページで、フラグメントを読み込んで差し込む。
// ヘッダーやタブはシェル側にあり、読み込み中も表示したままにする。
// フラグメント内の [data-shell-slot="名前"] の中身は、シェルの [data-shell-slot-target="名前"] へ移す（ヘッダーの最新化、フィルターの配置など）。
(function () {
    'use strict';

    // インラインscriptだけ実行し直す。外部script(src属性あり)はシェル側で読み込み済みのため除く
    function runFragmentScripts(container) {
        container.querySelectorAll('script').forEach((oldScript) => {
            if (oldScript.src) {
                oldScript.remove();
                return;
            }
            const newScript = document.createElement('script');
            [...oldScript.attributes].forEach((attr) => newScript.setAttribute(attr.name, attr.value));
            newScript.textContent = oldScript.textContent;
            oldScript.parentNode.replaceChild(newScript, oldScript);
        });
    }

    function moveSlots(container, root) {
        container.querySelectorAll('[data-shell-slot]').forEach((source) => {
            const target = root.querySelector(`[data-shell-slot-target="${source.dataset.shellSlot}"]`);
            if (!target) return;
            target.replaceChildren(...source.childNodes);
            source.remove();
        });
    }

    // 差し替える前に、中のグラフを破棄する（Chart.js が外したキャンバスを持ち続けないように）
    function destroyCharts(container) {
        const instances = window.Chart && window.Chart.instances;
        if (!instances) return;
        Object.values(instances).forEach((chart) => {
            if (chart && chart.canvas && container.contains(chart.canvas)) chart.destroy();
        });
    }

    function consumeSessionValue(key) {
        if (!key) return null;
        try {
            const value = sessionStorage.getItem(key);
            if (value) sessionStorage.removeItem(key);
            return value;
        } catch (e) {
            return null;
        }
    }

    /**
     * @param {object} config
     * @param {string} config.url 最初に読み込むフラグメントのURL
     * @param {string} config.pageLabel タイトルに付ける画面名（「プロフィール」など）
     * @param {'player'|'club'} config.kind
     * @param {string} [config.reloadAnchorKey] 再読み込み後にスクロールする位置を入れた sessionStorage のキー
     * @param {{fragmentBaseUrl: string}} [config.subTabs] サブタブ([data-shell-sub-tab])を同じページ内で切り替える場合
     */
    window.shellFragmentLoader = function shellFragmentLoader(config) {
        const { url, pageLabel, kind, reloadAnchorKey = null, subTabs = null } = config;
        const errorSelector = kind === 'club' ? '.club-error-msg' : '.player-error-msg';
        let abortController = null;
        let loadId = 0;

        return {
            isLoading: true,
            hasError: false,
            // サブタブの切り替え中（フィルターを薄くして操作できなくする）
            isSwitching: false,
            // DBにまだなく、読み込みにも失敗した場合はヘッダーごと隠す
            headHidden: false,
            currentUrl: url,
            loadedSearch: window.location.search,
            pendingScrollTarget: null,
            pendingScrollInstant: false,

            start() {
                if (subTabs) this.setupSubTabs();
                const reloadAnchor = consumeSessionValue(reloadAnchorKey);
                if (reloadAnchor) {
                    this.pendingScrollTarget = reloadAnchor;
                    this.pendingScrollInstant = true;
                } else if (window.location.hash) {
                    this.pendingScrollTarget = window.location.hash.substring(1);
                }
                return this.load();
            },

            identity() {
                return this.$root.querySelector('[data-shell-identity]');
            },

            // 差し替えたヘッダーに合わせて、タイトルとブックマークの名前をそろえる
            syncIdentity() {
                const identity = this.identity();
                if (!identity || identity.hasAttribute('data-shell-pending')) return;
                const nameEl = identity.querySelector('[data-shell-name]');
                const tagEl = identity.querySelector('[data-shell-tag]');
                const name = nameEl ? nameEl.textContent.trim() : '';
                const tag = tagEl ? tagEl.textContent.trim() : '';
                if (name && tag) {
                    document.title = `${name} (${tag}) ${pageLabel} - Brawl Insights`;
                }
                const rawName = identity.dataset.shellNameRaw;
                if (!rawName || !window.Alpine) return;
                this.$root.querySelectorAll('[data-shell-bookmark]').forEach((el) => {
                    const data = window.Alpine.$data(el);
                    if (!data) return;
                    if ('viewedPlayerName' in data) data.viewedPlayerName = rawName;
                    if ('viewedClubName' in data) data.viewedClubName = rawName;
                });
            },

            applyScroll() {
                const target = this.pendingScrollTarget;
                const instant = this.pendingScrollInstant;
                this.pendingScrollTarget = null;
                this.pendingScrollInstant = false;
                if (!target) return;
                const el = document.getElementById(target.startsWith('#') ? target.substring(1) : target);
                if (el) {
                    el.scrollIntoView({ behavior: instant ? 'auto' : 'smooth', block: 'start' });
                }
            },

            async load(fragmentUrl = this.currentUrl, { switching = false } = {}) {
                if (abortController) abortController.abort();
                abortController = new AbortController();
                const signal = abortController.signal;
                const currentLoadId = ++loadId;
                this.currentUrl = fragmentUrl;

                // 切り替え中に高さが縮んで、スクロール位置が飛ばないようにする
                const body = this.$refs.body;
                if (switching && body && !this.isLoading) body.style.minHeight = `${body.offsetHeight}px`;
                this.isLoading = true;
                this.hasError = false;
                this.headHidden = false;
                this.isSwitching = switching;

                try {
                    if (window.__biApplyIntegrityBlock && await window.__biApplyIntegrityBlock(this.$refs.content, kind)) {
                        return;
                    }
                    const res = await fetch(fragmentUrl, { credentials: 'same-origin', signal });
                    const html = await res.text();
                    if (currentLoadId !== loadId) return;
                    if (!html || !html.trim()) throw new Error('Empty response');

                    const container = this.$refs.content;
                    destroyCharts(container);
                    container.innerHTML = html;
                    moveSlots(container, this.$root);
                    runFragmentScripts(container);
                    this.syncIdentity();

                    // 4xx/5xxでもフラグメント内の専用エラーUIが返るため、UIがある場合は表示を優先
                    if (!res.ok && !container.querySelector(errorSelector)) {
                        throw new Error('HTTP ' + res.status);
                    }
                } catch (e) {
                    if (e.name === 'AbortError') return;
                    console.error('Fragment load error:', e);
                    this.hasError = true;
                } finally {
                    if (currentLoadId === loadId) {
                        this.isLoading = false;
                        this.isSwitching = false;
                        const identity = this.identity();
                        this.headHidden = Boolean(identity && identity.hasAttribute('data-shell-pending'));
                        if (body) body.style.minHeight = '';
                        this.$nextTick(() => {
                            requestAnimationFrame(() => this.applyScroll());
                        });
                    }
                }
            },

            setupSubTabs() {
                history.replaceState(Object.assign({}, history.state, { shellFragment: true }), '');
                this.$root.querySelectorAll('[data-shell-sub-tab]').forEach((link) => {
                    link.addEventListener('click', (event) => {
                        // 新しいタブで開く操作などはブラウザに任せる
                        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                        event.preventDefault();
                        if (link.getAttribute('aria-current') === 'page' && !this.hasError) return;
                        this.switchSubTab(link.href, { push: true });
                    });
                });
                window.addEventListener('popstate', (event) => {
                    // モーダルを戻る操作で閉じた場合は読み込み直さない
                    if (window.BrawlInsightsModalBack?.consumePopState(event)) return;
                    if (!event.state || !event.state.shellFragment) return;
                    if (window.location.search === this.loadedSearch) return;
                    this.switchSubTab(window.location.href, { push: false });
                });
            },

            setActiveSubTab(tab) {
                this.$root.querySelectorAll('[data-shell-sub-tab]').forEach((link) => {
                    const active = link.dataset.shellSubTab === tab;
                    link.classList.toggle('sub-tab-nav__link--active', active);
                    if (active) {
                        link.setAttribute('aria-current', 'page');
                    } else {
                        link.removeAttribute('aria-current');
                    }
                });
            },

            switchSubTab(href, { push }) {
                const target = new URL(href, window.location.href);
                this.setActiveSubTab(target.searchParams.get('tab') || 'all');
                if (push) {
                    history.pushState({ shellFragment: true }, '', target.pathname + target.search);
                }
                this.loadedSearch = target.search;
                this.pendingScrollTarget = null;
                return this.load(subTabs.fragmentBaseUrl + target.search, { switching: true });
            },
        };
    };
})();
