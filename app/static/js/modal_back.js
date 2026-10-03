/**
 * モーダルを「戻る」操作(ブラウザの戻る・Androidの戻るボタン・iOSのスワイプ)で閉じられるようにする。
 *
 * 使い方:
 *   - オーバーレイ要素に data-back-closable を付け、開いている間は active クラスを付ける
 *   - 閉じるボタンに data-back-close を付ける (戻る操作時はこのボタンをクリックして閉じる)
 *   - 動的に追加したオーバーレイは BrawlInsightsModalBack.register(el) で登録する
 *   - 独自のpopstate処理を持つページは、先頭で BrawlInsightsModalBack.consumePopState(event) が true なら処理を中断する
 *
 * モーダルを開いた時に同じURLの履歴を1つ積み、戻る操作でその履歴が消費されたらモーダルを閉じる。
 * UIで閉じた場合は積んだ履歴を自分で戻して取り除く。
 */
(function () {
    'use strict';

    if (window.BrawlInsightsModalBack) return;

    const STATE_KEY = 'biModalBack';
    const ACTIVE_CLASS = 'active';

    const watched = new WeakSet();
    // 開いているモーダル (後から開いたものほど末尾)
    const openStack = [];
    // UIで閉じた時に自分で呼んだ history.back() の数
    let pendingSelfBacks = 0;
    let tokenSeq = 0;
    // 同じpopstateイベントを複数のリスナーから判定しても結果が変わらないようにする
    const popResults = new WeakMap();

    function isActive(overlay) {
        return overlay.classList.contains(ACTIVE_CLASS);
    }

    function onOpened(overlay) {
        if (openStack.some((entry) => entry.overlay === overlay)) return;
        const token = `${Date.now()}-${++tokenSeq}`;
        openStack.push({ overlay, token, closingByHistory: false });
        try {
            history.pushState({ ...(history.state || {}), [STATE_KEY]: token }, '', window.location.href);
        } catch (e) {
            // 履歴を積めなくてもモーダル自体は使えるようにする
        }
    }

    function onClosed(overlay) {
        const index = openStack.findIndex((entry) => entry.overlay === overlay);
        if (index === -1) return;
        const [entry] = openStack.splice(index, 1);
        if (entry.closingByHistory) return;
        // UIで閉じた場合、積んだ履歴が現在位置にあれば取り除く
        if (history.state && history.state[STATE_KEY] === entry.token) {
            pendingSelfBacks += 1;
            history.back();
        }
    }

    function closeByHistory(entry) {
        entry.closingByHistory = true;
        const closeButton = entry.overlay.querySelector('[data-back-close]');
        if (closeButton) {
            closeButton.click();
        } else {
            entry.overlay.classList.remove(ACTIVE_CLASS);
            if (!document.querySelector(`[data-back-closable].${ACTIVE_CLASS}`)) {
                document.body.classList.remove('modal-open');
            }
        }
        // 閉じるボタンの処理がactiveを外さなかった場合も、管理対象からは外す
        const index = openStack.indexOf(entry);
        if (index !== -1) openStack.splice(index, 1);
    }

    /**
     * popstateがモーダルの開閉によるものか判定し、必要ならモーダルを閉じる。
     * trueの場合、ページ側のpopstate処理(一覧の再読み込みなど)は行わないこと。
     */
    function consumePopState(event) {
        if (event && popResults.has(event)) return popResults.get(event);

        let handled = false;
        if (pendingSelfBacks > 0) {
            pendingSelfBacks -= 1;
            handled = true;
        } else if (openStack.length > 0) {
            closeByHistory(openStack[openStack.length - 1]);
            handled = true;
        } else if (event && event.state && event.state[STATE_KEY]) {
            // 「進む」で閉じたモーダルの履歴に戻ってきた場合は何もしない
            handled = true;
        }

        if (event) popResults.set(event, handled);
        return handled;
    }

    function register(overlay) {
        if (!overlay || watched.has(overlay)) return;
        watched.add(overlay);
        let wasActive = isActive(overlay);
        if (wasActive) onOpened(overlay);
        const observer = new MutationObserver(() => {
            const nowActive = isActive(overlay);
            if (nowActive === wasActive) return;
            wasActive = nowActive;
            if (nowActive) {
                onOpened(overlay);
            } else {
                onClosed(overlay);
            }
        });
        observer.observe(overlay, { attributes: true, attributeFilter: ['class'] });
    }

    function registerAll(root) {
        (root || document).querySelectorAll('[data-back-closable]').forEach(register);
    }

    window.addEventListener('popstate', consumePopState);

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => registerAll());
    } else {
        registerAll();
    }

    window.BrawlInsightsModalBack = {
        register,
        registerAll,
        consumePopState,
    };
})();
