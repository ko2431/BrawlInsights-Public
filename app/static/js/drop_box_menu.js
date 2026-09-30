/* ボックス確率表・シミュレーター・期待値計算機で共通のボックス種類メニュー。掲載するボックスの定義はここだけで行う。 */
(function () {
    // 「表示を増やす」を押したときの並び順
    const BOX_ORDER_EXPANDED = [
        'starrdrop', 'chaosdrop', 'rankeddrop', 'coffinbox', 'nanodrop', 'smoothiedrop', 'angelicdrop', 'demonicdrop',
        'novadrop', 'present', 'sushi', 'megabox', 'deadbox', 'halloweenbox', 'mechabox', 'lovebox', 'boombox',
        'minabox', 'ziggybox', 'gigibox', 'piercebox', 'glowbertbox',
        'siriusbox', 'najiabox', 'damianbox', 'starrnovabox', 'boltbox', 'noribox', 'wendybox', 'cosmobox', 'vincebox',
        'smalltrophybox', 'bigtrophybox', 'megatrophybox', 'omegatrophybox', 'ultratrophybox',
    ];
    // 「表示を増やす」を押したときのみ表示するボックス
    const EXPANDED_ONLY_KEYS = [
        'nanodrop', 'smoothiedrop', 'novadrop', 'present', 'deadbox', 'halloweenbox', 'lovebox',
        'minabox', 'ziggybox', 'gigibox', 'piercebox', 'glowbertbox',
        'siriusbox', 'najiabox', 'damianbox', 'starrnovabox', 'boltbox', 'noribox', 'wendybox',
    ];
    const BOX_ORDER_COMPACT = BOX_ORDER_EXPANDED.filter((key) => !EXPANDED_ONLY_KEYS.includes(key));

    const isEditableElement = (el) => !!el && (el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName));

    /**
     * 各ページのAlpineデータにボックス種類メニューの状態・処理を合成する。
     * ページ側では boxes (getter)・selectedBoxKey・selectBox(boxKey) を定義しておくこと。
     * getterを保つため、スプレッド構文ではなくプロパティ定義ごとコピーする。
     */
    window.withDropBoxMenu = function (app) {
        const menu = {
            boxOrderCompact: BOX_ORDER_COMPACT,
            boxOrderExpanded: BOX_ORDER_EXPANDED,
            showExpandedBoxList: false,

            get activeBoxOrder() {
                return this.showExpandedBoxList ? this.boxOrderExpanded : this.boxOrderCompact;
            },

            get hasHiddenBoxes() {
                return this.boxOrderExpanded.some((key) => !this.boxOrderCompact.includes(key) && this.boxes[key]);
            },

            // 初期表示のボックスが折りたたみ時に表示されない場合は、最初から展開しておく
            expandBoxListFor(boxKey) {
                if (boxKey && !this.boxOrderCompact.includes(boxKey)) {
                    this.showExpandedBoxList = true;
                }
            },

            toggleBoxListExpanded() {
                this.showExpandedBoxList = !this.showExpandedBoxList;
                if (!this.showExpandedBoxList && !this.boxOrderCompact.includes(this.selectedBoxKey)) {
                    const fallback = this.boxOrderCompact.find((key) => this.boxes[key]);
                    if (fallback) this.selectBox(fallback);
                }
            },

            // 左右矢印キーで、メニューに表示中のボックスを順に切り替える(端まで行くとループ)
            handleBoxMenuKeydown(e) {
                if (e.metaKey || e.ctrlKey || e.altKey) return;
                if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
                if (isEditableElement(document.activeElement)) return;
                const keys = this.activeBoxOrder.filter((key) => this.boxes[key]);
                if (keys.length === 0) return;
                e.preventDefault();
                const index = keys.indexOf(this.selectedBoxKey);
                let nextIndex;
                if (index < 0) {
                    nextIndex = e.key === 'ArrowRight' ? 0 : keys.length - 1;
                } else {
                    nextIndex = e.key === 'ArrowRight'
                        ? (index + 1) % keys.length
                        : (index - 1 + keys.length) % keys.length;
                }
                this.selectBox(keys[nextIndex]);
            },

            // サブタブで別ページへ移動しても、選択中のボックスを引き継ぐ
            getBoxPageHref(baseUrl) {
                if (!this.selectedBoxKey) return baseUrl;
                return `${baseUrl}?box=${encodeURIComponent(this.selectedBoxKey)}`;
            },
        };

        Object.defineProperties(app, Object.getOwnPropertyDescriptors(menu));
        return app;
    };

    window.isDropBoxMenuEditableElement = isEditableElement;
})();
