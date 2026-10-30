// /app/static/js/pages/announcements.js
// お知らせの一覧（長い本文の折りたたみ・「もっと見る」・古いリンクの転送）と個別ページ（共有）。
// 本文の画像は、縮めて出しているものだけタップで拡大する（image_viewer.js）。
(function () {
    const CLAMP_SLACK_PX = 8;

    function setupClamp(root) {
        root.querySelectorAll('.ann-item__body.is-clamped:not([data-clamp-ready])').forEach((body) => {
            body.dataset.clampReady = '1';
            const button = body.parentElement.querySelector('.ann-item__expand');
            if (body.scrollHeight <= body.clientHeight + CLAMP_SLACK_PX) {
                body.classList.remove('is-clamped');
                return;
            }
            if (button) button.hidden = false;
        });
    }

    function expand(item) {
        const body = item.querySelector('.ann-item__body');
        const button = item.querySelector('.ann-item__expand');
        if (!body) return;
        body.classList.remove('is-clamped');
        body.classList.add('is-expanding');
        window.setTimeout(() => body.classList.remove('is-expanding'), 320);
        if (button) button.hidden = true;
    }

    function itemCount(list) {
        return list.querySelectorAll('.ann-item').length;
    }

    function rememberCount(list, pageSize) {
        // 「もっと見る」で増やした件数を URL に残し、個別ページから戻ったときに同じ件数で開く
        try {
            const url = new URL(window.location.href);
            const n = itemCount(list);
            if (n > pageSize) url.searchParams.set('n', String(n));
            else url.searchParams.delete('n');
            history.replaceState(history.state, '', url.toString());
        } catch {
            /* ignore */
        }
    }

    function decodeHash() {
        const raw = window.location.hash.slice(1);
        if (!raw) return '';
        try {
            return decodeURIComponent(raw);
        } catch {
            return raw;
        }
    }

    function findItemByKey(list, key) {
        const byId = document.getElementById(key);
        if (byId && byId.classList.contains('ann-item')) return byId;
        try {
            return list.querySelector(`.ann-item[data-legacy="${CSS.escape(key)}"]`);
        } catch {
            return null;
        }
    }

    function handleHash(page, list) {
        // 旧形式のリンク（/help/announcements#243・#G91・#web1）。ページにあればそこへ、なければ個別ページへ
        const key = decodeHash();
        if (!key) return;
        const item = findItemByKey(list, key);
        const base = page.dataset.detailBase.replace(/\/$/, '');
        if (item) {
            expand(item);
            item.scrollIntoView({ block: 'start' });
            return;
        }
        const target = /^\d+$/.test(key) ? `${base}/${key}` : `${base}/legacy/${encodeURIComponent(key)}`;
        window.location.replace(target);
    }

    function initList() {
        const page = document.getElementById('announcementsPage');
        const list = document.getElementById('announcementList');
        if (!page || !list) return;
        const pageSize = Number(page.dataset.pageSize) || 20;

        setupClamp(list);
        list.addEventListener('click', (event) => {
            const button = event.target.closest('.ann-item__expand');
            if (button) expand(button.closest('.ann-item'));
        });
        document.body.addEventListener('htmx:afterSwap', (event) => {
            if (!list.contains(event.target) && event.target !== list) return;
            setupClamp(list);
            rememberCount(list, pageSize);
        });
        document.body.addEventListener('htmx:responseError', (event) => {
            const button = event.target.closest?.('.ann-more__button');
            if (button) button.classList.add('is-error');
        });
        window.addEventListener('hashchange', () => handleHash(page, list));
        handleHash(page, list);
    }

    function copyText(text) {
        // http の開発環境などでは navigator.clipboard がないので、選択してコピーする方法も使う
        if (navigator.clipboard && window.isSecureContext) {
            return navigator.clipboard.writeText(text).then(() => true, () => false);
        }
        const area = document.createElement('textarea');
        area.value = text;
        area.setAttribute('readonly', '');
        area.style.position = 'fixed';
        area.style.opacity = '0';
        document.body.appendChild(area);
        area.select();
        area.setSelectionRange(0, text.length);
        let ok = false;
        try {
            ok = document.execCommand('copy');
        } catch {
            ok = false;
        }
        area.remove();
        return Promise.resolve(ok);
    }

    function isShareCanceled(error) {
        return error && (error.name === 'AbortError' || /cancel/i.test(String(error.message || error)));
    }

    function initShare() {
        document.addEventListener('click', async (event) => {
            const button = event.target.closest('.ann-detail__share');
            if (!button) return;
            const url = window.location.origin + window.location.pathname;
            const title = document.title;
            const nativeShare = window.Capacitor?.isNativePlatform?.() && window.Capacitor.Plugins?.Share;
            if (nativeShare) {
                try {
                    await window.Capacitor.Plugins.Share.share({ title, url });
                } catch (error) {
                    if (!isShareCanceled(error)) console.error(error);
                }
                return;
            }
            if (navigator.share && window.matchMedia('(pointer: coarse)').matches) {
                try {
                    await navigator.share({ url, title });
                    return;
                } catch (error) {
                    if (isShareCanceled(error)) return;
                }
            }
            if (!(await copyText(url))) return;
            const label = button.querySelector('.ann-detail__share-label');
            if (!label || button.classList.contains('is-copied')) return;
            const original = label.textContent;
            label.textContent = button.dataset.copiedLabel || original;
            button.classList.add('is-copied');
            window.setTimeout(() => {
                label.textContent = original;
                button.classList.remove('is-copied');
            }, 1600);
        });
    }

    // --- 画像の拡大 --------------------------------------------------------------------------
    const ZOOM_SELECTOR = '.ann-body figure.bi-figure img';

    function isShrunk(img) {
        // 元の大きさより縮めて出している（小さいアイコンなどは拡大しない）
        return img.naturalWidth > img.clientWidth * 1.15 || img.naturalHeight > img.clientHeight * 1.15;
    }

    function markZoomable(root) {
        (root || document).querySelectorAll(ZOOM_SELECTOR).forEach((img) => {
            if (img.complete && img.naturalWidth) img.classList.toggle('is-zoomable', isShrunk(img));
        });
    }

    function initZoom() {
        const viewer = window.BrawlInsightsImageViewer;
        if (!viewer) return;
        document.addEventListener('load', (event) => {
            const img = event.target;
            if (img.matches && img.matches(ZOOM_SELECTOR)) img.classList.toggle('is-zoomable', isShrunk(img));
        }, true);
        markZoomable();
        let timer = 0;
        window.addEventListener('resize', () => {
            window.clearTimeout(timer);
            timer = window.setTimeout(() => markZoomable(), 200);
        });
        document.body.addEventListener('htmx:afterSwap', (event) => markZoomable(event.target));
        document.addEventListener('click', (event) => {
            const img = event.target.closest && event.target.closest(ZOOM_SELECTOR);
            if (!img || !img.classList.contains('is-zoomable')) return;
            event.preventDefault();
            viewer.open(img.currentSrc || img.src, { alt: img.alt });
        });
    }

    function init() {
        initList();
        initShare();
        initZoom();
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
