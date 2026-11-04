/* /app/static/js/shell_fragment_loader.js */
// プレイヤー・クラブのシェルページで、フラグメントを読み込んで差し込む。
// ヘッダーやタブはシェル側にあり、読み込み中も表示したままにする。
// フラグメント内の [data-shell-slot="名前"] の中身は、シェルの [data-shell-slot-target="名前"] へ移す（ヘッダーの最新化、フィルターの配置など）。
(function () {
    'use strict';

    // シェルが defer で読むライブラリ(Chart.js など。script[data-shell-lib])を待つ。フラグメントのscriptが使うため、差し込みはこのあとにする。
    // このファイルは <body> の末尾で同期に読むため、この時点ではまだ実行されていない(defer は解析の完了後に実行される)
    const shellLibrariesReady = document.readyState === 'loading'
        ? Promise.all([...document.querySelectorAll('script[data-shell-lib]')].map((script) => new Promise((resolve) => {
            script.addEventListener('load', resolve, { once: true });
            script.addEventListener('error', resolve, { once: true });
        })))
        : Promise.resolve();

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

    // 移した欄の名前を返す
    function moveSlots(container, root) {
        const filled = new Set();
        container.querySelectorAll('[data-shell-slot]').forEach((source) => {
            const target = root.querySelector(`[data-shell-slot-target="${source.dataset.shellSlot}"]`);
            if (!target) return;
            target.replaceChildren(...source.childNodes);
            source.remove();
            filled.add(source.dataset.shellSlot);
        });
        return filled;
    }

    // [data-shell-slot-clear] の欄のうち、今回の応答に含まれていなかったもの(エラーの応答など)を空にする。
    // 前のサブタブのフィルターが残り、そのまま適用されないようにするため
    function clearUnfilledSlots(root, filled) {
        root.querySelectorAll('[data-shell-slot-target][data-shell-slot-clear]').forEach((target) => {
            if (!filled.has(target.dataset.shellSlotTarget)) target.replaceChildren();
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

    // シェルの <head> で始めたフラグメントの取得(components/shell_prefetch.html)を、URLが同じ場合に1回だけ受け取る
    function takeShellPrefetch(url) {
        const prefetch = window.__biShellPrefetch;
        if (!prefetch || prefetch.url !== url) return null;
        window.__biShellPrefetch = null;
        return prefetch;
    }

    // [この部分は公開用リポジトリでは非公開にされています]

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
            // 直近の読み込みが失敗したか(専用のエラー表示が出た場合も含む)。選択中のサブタブを押して再試行できるようにする
            loadFailed: false,
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
                const controller = new AbortController();
                abortController = controller;
                const currentLoadId = ++loadId;
                this.currentUrl = fragmentUrl;
                const fetchFragment = async () => {
                    const res = await fetch(fragmentUrl, { credentials: 'same-origin', signal: controller.signal });
                    return { res, html: await res.text() };
                };
                // 先に始めた取得があれば使う。表示は、これまでどおり計測スクリプトの確認のあと
                const prefetch = takeShellPrefetch(fragmentUrl);
                if (prefetch) controller.signal.addEventListener('abort', () => prefetch.controller.abort());

                // 切り替え中に高さが縮んで、スクロール位置が飛ばないようにする
                const body = this.$refs.body;
                if (switching && body && !this.isLoading) body.style.minHeight = `${body.offsetHeight}px`;
                this.isLoading = true;
                this.hasError = false;
                this.loadFailed = false;
                this.headHidden = false;
                this.isSwitching = switching;
                let filledSlots = new Set();

                try {
                    if (window.__biApplyIntegrityBlock && await window.__biApplyIntegrityBlock(this.$refs.content, kind)) {
                        controller.abort();
                        return;
                    }
                    let { res, html } = await (prefetch ? prefetch.promise : fetchFragment());
                    // [この部分は公開用リポジトリでは非公開にされています]
                    await shellLibrariesReady;
                    if (currentLoadId !== loadId) return;
                    if (!html || !html.trim()) throw new Error('Empty response');

                    const container = this.$refs.content;
                    destroyCharts(container);
                    container.innerHTML = html;
                    filledSlots = moveSlots(container, this.$root);
                    runFragmentScripts(container);
                    this.syncIdentity();

                    // 4xx/5xxでもフラグメント内の専用エラーUIが返るため、UIがある場合は表示を優先
                    if (!res.ok) {
                        this.loadFailed = true;
                        if (!container.querySelector(errorSelector)) throw new Error('HTTP ' + res.status);
                    }
                } catch (e) {
                    if (e.name === 'AbortError') return;
                    console.error('Fragment load error:', e);
                    this.hasError = true;
                    this.loadFailed = true;
                } finally {
                    if (currentLoadId === loadId) {
                        clearUnfilledSlots(this.$root, filledSlots);
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
                        if (link.getAttribute('aria-current') === 'page' && !this.loadFailed) return;
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
                // 失敗したタブを押し直して再試行する場合は、同じ履歴を重ねない
                if (push && target.pathname + target.search !== window.location.pathname + window.location.search) {
                    history.pushState({ shellFragment: true }, '', target.pathname + target.search);
                }
                this.loadedSearch = target.search;
                this.pendingScrollTarget = null;
                return this.load(subTabs.fragmentBaseUrl + target.search, { switching: true });
            },
        };
    };
})();
