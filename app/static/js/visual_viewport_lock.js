/**
 * iPad Split View / Stage Manager などで、別アプリのキーボードが
 * Safari / WKWebView の visual viewport をずらしたまま戻らない不具合への補正。
 * position:fixed の下部タブバーがコンテンツと一緒に動いて見えるのを打ち消す。
 */
(function () {
    'use strict';

    const root = document.documentElement;
    const NON_TEXT_INPUT_TYPES = new Set([
        'button',
        'checkbox',
        'color',
        'file',
        'hidden',
        'image',
        'radio',
        'range',
        'reset',
        'submit',
    ]);

    let rafId = 0;
    let lastOffsetPx = '0px';

    function isDocumentEditing() {
        const el = document.activeElement;
        if (!el || el === document.body || el === root) {
            return false;
        }
        if (el.isContentEditable) {
            return true;
        }
        const tag = el.tagName;
        if (tag === 'TEXTAREA' || tag === 'SELECT') {
            return true;
        }
        if (tag === 'INPUT') {
            return !NON_TEXT_INPUT_TYPES.has((el.type || 'text').toLowerCase());
        }
        return false;
    }

    function setOffset(pxValue) {
        if (pxValue === lastOffsetPx) {
            return;
        }
        lastOffsetPx = pxValue;
        root.style.setProperty('--vv-offset-top', pxValue);
    }

    function syncVisualViewportOffset() {
        rafId = 0;
        const vv = window.visualViewport;
        if (!vv) {
            setOffset('0px');
            return;
        }
        // ピンチズーム中はブラウザ側の座標変換に任せる
        if (Math.abs(vv.scale - 1) > 0.02) {
            setOffset('0px');
            return;
        }
        // このページ内の入力欄は従来どおり OS / WebView に任せる
        if (isDocumentEditing()) {
            setOffset('0px');
            return;
        }
        const offset = Math.round(vv.offsetTop) || 0;
        setOffset(`${offset}px`);
    }

    function scheduleSync() {
        if (rafId) {
            return;
        }
        rafId = window.requestAnimationFrame(syncVisualViewportOffset);
    }

    const vv = window.visualViewport;
    if (vv) {
        vv.addEventListener('scroll', scheduleSync);
        vv.addEventListener('resize', scheduleSync);
    }
    window.addEventListener('scroll', scheduleSync, { passive: true });
    window.addEventListener('resize', scheduleSync);
    window.addEventListener('orientationchange', scheduleSync);
    document.addEventListener('focusin', scheduleSync);
    document.addEventListener('focusout', () => {
        window.setTimeout(scheduleSync, 50);
        window.setTimeout(scheduleSync, 350);
    });
    document.addEventListener('visibilitychange', scheduleSync);
    window.addEventListener('pageshow', scheduleSync);

    syncVisualViewportOffset();
})();
