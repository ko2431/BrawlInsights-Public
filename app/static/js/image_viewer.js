// /app/static/js/image_viewer.js
// 画像の拡大表示（共通の部品。検討メモ notes_private/push_image_plan_2026-10-27.md の 6.4）。
// 使い方: BrawlInsightsImageViewer.open(src, { alt, name })
// 全画面で出し、ダブルクリック・ダブルタップ・ピンチ・ホイールで拡大、拡大中はドラッグで動かす。
// 下へのスワイプ・Esc・背景のタップ・戻る操作（modal_back.js）で閉じる。
(function () {
    'use strict';

    if (window.BrawlInsightsImageViewer) return;

    const MAX_SCALE = 4;
    const DOUBLE_SCALE = 2.5;
    const DOUBLE_TAP_MS = 300;
    const SWIPE_CLOSE_PX = 110;
    const ja = (document.documentElement.lang || 'ja').toLowerCase().startsWith('ja');
    const TEXT = ja
        ? { close: '閉じる', save: '保存', saved: '保存しました' }
        : { close: 'Close', save: 'Save', saved: 'Saved' };

    let root = null;
    let stage = null;
    let img = null;
    let saveButton = null;
    let current = { name: '' };
    // 拡大・移動の状態
    let scale = 1;
    let x = 0;
    let y = 0;
    let swipeY = 0;
    const pointers = new Map();
    let gesture = null;
    let lastTap = { x: 0, y: 0 };
    let tapTimer = 0;
    let moved = false;
    let lastPointerType = '';
    let downOnImage = false; // 押し始めた所（ポインターを捕まえると、離したときの target は stage になる）

    function build() {
        root = document.createElement('div');
        root.className = 'bi-viewer';
        root.setAttribute('data-back-closable', '');
        root.setAttribute('role', 'dialog');
        root.setAttribute('aria-modal', 'true');
        root.tabIndex = -1;
        root.hidden = true;
        root.innerHTML = `
            <div class="bi-viewer__stage"><img class="bi-viewer__img" alt="" draggable="false"></div>
            <div class="bi-viewer__bar">
                <button type="button" class="bi-viewer__button bi-viewer__save"></button>
                <button type="button" class="bi-viewer__button bi-viewer__close" data-back-close></button>
            </div>`;
        document.body.appendChild(root);
        stage = root.querySelector('.bi-viewer__stage');
        img = root.querySelector('.bi-viewer__img');
        saveButton = root.querySelector('.bi-viewer__save');
        saveButton.textContent = TEXT.save;
        const closeButton = root.querySelector('.bi-viewer__close');
        closeButton.textContent = '×';
        closeButton.setAttribute('aria-label', TEXT.close);
        closeButton.addEventListener('click', close);
        saveButton.addEventListener('click', save);
        stage.addEventListener('pointerdown', onPointerDown);
        stage.addEventListener('pointermove', onPointerMove);
        stage.addEventListener('pointerup', onPointerUp);
        stage.addEventListener('pointercancel', onPointerUp);
        stage.addEventListener('dblclick', (event) => {
            // ダブルタップでも dblclick が来るので、タッチのときは pointerup の側だけで切り替える
            if (lastPointerType === 'touch') return;
            toggleZoom(event.clientX, event.clientY);
        });
        stage.addEventListener('wheel', onWheel, { passive: false });
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && isOpen()) {
                event.preventDefault();
                close();
            }
        });
        if (window.BrawlInsightsModalBack) window.BrawlInsightsModalBack.register(root);
    }

    function isOpen() {
        return !!root && root.classList.contains('active');
    }

    function apply(animate) {
        img.classList.toggle('is-animating', !!animate);
        img.style.transform = `translate(${x}px, ${y + swipeY}px) scale(${scale})`;
        root.style.setProperty('--bi-viewer-dim', String(Math.max(0.35, 1 - Math.abs(swipeY) / 400)));
        root.classList.toggle('is-zoomed', scale > 1.01);
    }

    function clampPan() {
        const w = img.offsetWidth * scale;
        const h = img.offsetHeight * scale;
        const maxX = Math.max(0, (w - stage.clientWidth) / 2);
        const maxY = Math.max(0, (h - stage.clientHeight) / 2);
        x = Math.min(maxX, Math.max(-maxX, x));
        y = Math.min(maxY, Math.max(-maxY, y));
    }

    function stagePoint(clientX, clientY) {
        const rect = stage.getBoundingClientRect();
        return { px: clientX - rect.left - rect.width / 2, py: clientY - rect.top - rect.height / 2 };
    }

    function zoomTo(next, clientX, clientY, animate) {
        // 指・カーソルの下の点が動かないように拡大する
        const target = Math.min(MAX_SCALE, Math.max(1, next));
        const { px, py } = stagePoint(clientX, clientY);
        x = px - (target / scale) * (px - x);
        y = py - (target / scale) * (py - y);
        scale = target;
        if (scale <= 1.01) {
            scale = 1;
            x = 0;
            y = 0;
        }
        clampPan();
        apply(animate);
    }

    function toggleZoom(clientX, clientY) {
        zoomTo(scale > 1.01 ? 1 : DOUBLE_SCALE, clientX, clientY, true);
    }

    function onWheel(event) {
        event.preventDefault();
        const factor = Math.exp(-event.deltaY * (event.ctrlKey ? 0.01 : 0.002));
        zoomTo(scale * factor, event.clientX, event.clientY, false);
    }

    function distance(a, b) {
        return Math.hypot(a.x - b.x, a.y - b.y);
    }

    function onPointerDown(event) {
        if (event.button !== undefined && event.button > 0) return;
        lastPointerType = event.pointerType;
        if (pointers.size === 0) downOnImage = event.target === img;
        stage.setPointerCapture(event.pointerId);
        pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
        moved = false;
        if (pointers.size === 2) {
            const [a, b] = [...pointers.values()];
            gesture = { type: 'pinch', dist: distance(a, b), scale, swipe: false };
            swipeY = 0;
        } else if (pointers.size === 1) {
            gesture = { type: scale > 1.01 ? 'pan' : 'swipe', startX: event.clientX, startY: event.clientY, x, y };
        }
    }

    function onPointerMove(event) {
        if (!pointers.has(event.pointerId) || !gesture) return;
        pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
        if (gesture.type === 'pinch' && pointers.size >= 2) {
            const [a, b] = [...pointers.values()];
            const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
            zoomTo(gesture.scale * (distance(a, b) / gesture.dist), mid.x, mid.y, false);
            moved = true;
            return;
        }
        const dx = event.clientX - gesture.startX;
        const dy = event.clientY - gesture.startY;
        if (Math.abs(dx) + Math.abs(dy) > 6) moved = true;
        if (gesture.type === 'pan') {
            x = gesture.x + dx;
            y = gesture.y + dy;
            clampPan();
            apply(false);
        } else if (gesture.type === 'swipe' && event.pointerType !== 'mouse') {
            swipeY = dy;
            apply(false);
        }
    }

    function onPointerUp(event) {
        if (!pointers.has(event.pointerId)) return;
        pointers.delete(event.pointerId);
        const wasGesture = gesture;
        if (pointers.size === 1 && wasGesture && wasGesture.type === 'pinch') {
            // ピンチの後に1本の指が残ったら、そのまま動かせるようにする
            const [rest] = [...pointers.values()];
            gesture = { type: scale > 1.01 ? 'pan' : 'swipe', startX: rest.x, startY: rest.y, x, y };
            return;
        }
        if (pointers.size > 0) return;
        gesture = null;
        if (wasGesture && wasGesture.type === 'swipe' && Math.abs(swipeY) > SWIPE_CLOSE_PX) {
            close();
            return;
        }
        if (swipeY) {
            swipeY = 0;
            apply(true);
        }
        if (moved) return;
        const onBackground = !downOnImage;
        if (event.pointerType === 'touch') {
            // 2回目のタップが続けば拡大・縮小、続かなければ1回のタップ（背景なら閉じる）
            if (tapTimer && Math.hypot(event.clientX - lastTap.x, event.clientY - lastTap.y) < 30) {
                window.clearTimeout(tapTimer);
                tapTimer = 0;
                toggleZoom(event.clientX, event.clientY);
                return;
            }
            lastTap = { x: event.clientX, y: event.clientY };
            window.clearTimeout(tapTimer);
            tapTimer = window.setTimeout(() => {
                tapTimer = 0;
                if (onBackground && scale <= 1.01) close();
            }, DOUBLE_TAP_MS);
            return;
        }
        // マウス: 画像の外（背景）をクリックしたら閉じる
        if (onBackground && scale <= 1.01) close();
    }

    function reset() {
        scale = 1;
        x = 0;
        y = 0;
        swipeY = 0;
        pointers.clear();
        gesture = null;
        window.clearTimeout(tapTimer);
        tapTimer = 0;
        apply(false);
    }

    function open(src, options = {}) {
        if (!src) return;
        if (!root) build();
        current = { name: options.name || decodeURIComponent(src.split('?')[0].split('/').pop() || 'image') };
        img.src = src;
        img.alt = options.alt || '';
        reset();
        root.hidden = false;
        document.body.classList.add('modal-open');
        // 次の描画で active を付けて、フェードで出す（modal_back.js が履歴を1つ積む）
        requestAnimationFrame(() => root.classList.add('active'));
        saveButton.disabled = false;
        // 閉じるボタンではなく全体にフォーカスする（開いた直後にボタンの枠が出ないように。Esc はどこでも効く）
        root.focus({ preventScroll: true });
    }

    function close() {
        if (!isOpen()) return;
        root.classList.remove('active');
        if (!document.querySelector('[data-back-closable].active')) document.body.classList.remove('modal-open');
        window.setTimeout(() => {
            if (!isOpen()) {
                root.hidden = true;
                img.removeAttribute('src');
            }
        }, 180);
    }

    async function save() {
        const src = new URL(img.currentSrc || img.src, window.location.href).href;
        const name = current.name;
        // Android のアプリ: ネイティブの共有シートで画像のファイルを渡す（プロフィール画像のツールと同じ）
        if (window.BrawlInsightsNative && window.BrawlInsightsNative.shareImageFromUrl) {
            window.BrawlInsightsNative.shareImageFromUrl(src, name, TEXT.save);
            return;
        }
        const native = window.Capacitor && window.Capacitor.isNativePlatform && window.Capacitor.isNativePlatform();
        const touch = window.matchMedia('(pointer: coarse)').matches;
        if ((native || touch) && navigator.canShare && navigator.share) {
            // iPhone・iPad: 共有シートの「画像を保存」で写真に保存できる
            try {
                saveButton.disabled = true;
                const blob = await (await fetch(src)).blob();
                const file = new File([blob], name, { type: blob.type || 'image/webp' });
                if (navigator.canShare({ files: [file] })) {
                    await navigator.share({ files: [file] });
                    return;
                }
            } catch (error) {
                if (error && error.name === 'AbortError') return;
            } finally {
                saveButton.disabled = false;
            }
        }
        const link = document.createElement('a');
        link.href = src;
        link.download = name;
        document.body.appendChild(link);
        link.click();
        link.remove();
    }

    window.BrawlInsightsImageViewer = { open, close, isOpen };
})();
