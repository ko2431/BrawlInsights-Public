// 編集中のリストを、右端のつまみ（≡）のドラッグで並び替える（つまみは components/drag_handle.html）。
// ホームのブックマーク（プレイヤー・クラブ）と、アカウント設定のプレイヤーアカウントで使う。
// Alpine の x-for（:key あり）で描いたリストの配列を、つかんだ行が隣の行の中央を越えるたびにその場で入れ替え、
// 行の移動は Alpine に任せる（メイン・サブの表示なども動かしている間に追従する）。
// 行には data-sort-group を付け、同じ値の行どうしだけで入れ替える。
//
// 止まらないようにするための注意:
// - 入れ替えの結果が DOM に出るのを待つのに Alpine.nextTick は使わない。ほかのコンポーネントの nextTick が失敗すると
//   後ろの呼び出しが実行されないまま残り、つかんだ行が浮いたまま止まるため。Alpine の再描画はマイクロタスクで終わるので、
//   次の描画フレーム（requestAnimationFrame）で DOM を読み直し、実際の並びから判断する
// - 行が DOM 上で移動するとポインターのキャプチャが外れることがあるため、move/up は window で受ける。
//   画面の外で離したなどで pointerup が届かなかったときに備え、ボタンが離れた move・フォーカスが外れたときにも終える
(function () {
    if (window.biDragSort) return;

    const EDGE = 64;      // 画面の上下の端からこの範囲に入ったら、自動でスクロールする
    const MAX_SPEED = 12; // 自動スクロールの最大の速さ (px/フレーム)
    const SLIDE_MS = 160;

    let endActive = null; // ドラッグ中なら、それを終える関数（同時に2つはつかませない）

    function groupRows(row) {
        const parent = row.parentElement;
        if (!parent) return [];
        const group = row.dataset.sortGroup;
        return Array.from(parent.children).filter((el) => el.dataset && el.dataset.sortGroup === group);
    }

    // 位置が変わった行を、元の位置から滑らかに動かす（FLIP）。offsetTop は transform の影響を受けない
    function slide(el, fromTop) {
        const dy = fromTop - el.offsetTop;
        if (!dy) return;
        el.style.transition = 'none';
        el.style.transform = `translateY(${dy}px)`;
        void el.offsetHeight;
        el.style.transition = `transform ${SLIDE_MS}ms ease`;
        el.style.transform = '';
    }

    function start(event, list, onChange) {
        if (event.button !== 0) return;
        const handle = event.currentTarget;
        const row = handle.closest('[data-sort-group]');
        if (!row || list.length < 2) return;
        event.preventDefault();
        if (endActive) endActive();

        const root = document.documentElement;
        const pointerId = event.pointerId;
        const isMouse = event.pointerType === 'mouse';
        const startPageY = event.clientY + window.scrollY;
        const startTop = row.offsetTop;
        let clientY = event.clientY;
        let moved = false;
        let pending = null; // 入れ替えたあと DOM に出るまでの間: { neighbor, neighborTop, expected }
        let ended = false;
        let rafId = 0;

        row.classList.add('is-sorting');
        root.classList.add('is-drag-sorting');
        row.style.transition = 'none';

        function place() {
            const dy = clientY + window.scrollY - startPageY;
            row.style.transform = `translateY(${startTop + dy - row.offsetTop}px)`;
            return dy;
        }

        // つかんだ行の位置を指に合わせ、隣の行の中央を越えていれば配列を入れ替える
        function update() {
            const dy = place();
            if (pending || ended) return;
            const rows = groupRows(row);
            const index = rows.indexOf(row);
            if (index < 0 || rows.length !== list.length) return;
            const center = startTop + dy + row.offsetHeight / 2;
            const prev = rows[index - 1];
            const next = rows[index + 1];
            let target = index;
            if (prev && center < prev.offsetTop + prev.offsetHeight / 2) target = index - 1;
            else if (next && center > next.offsetTop + next.offsetHeight / 2) target = index + 1;
            if (target === index) return;
            const neighbor = rows[target];
            pending = { neighbor, neighborTop: neighbor.offsetTop, expected: target, frames: 0 };
            const [item] = list.splice(index, 1);
            list.splice(target, 0, item);
            if (onChange) onChange();
        }

        // 毎フレーム: 入れ替えが DOM に出たかを確かめてから次へ進め、端に近ければ自動でスクロールする
        function frame() {
            if (pending) {
                const { neighbor, neighborTop, expected } = pending;
                if (groupRows(row).indexOf(row) === expected) {
                    pending = null;
                    slide(neighbor, neighborTop);
                } else if (++pending.frames > 5) {
                    pending = null; // 出なければ待つのをやめ、今の DOM の並びから続ける
                }
            }
            let speed = 0;
            if (moved) {
                if (clientY < EDGE) speed = -MAX_SPEED * Math.min(1, (EDGE - clientY) / EDGE);
                else if (clientY > window.innerHeight - EDGE) speed = MAX_SPEED * Math.min(1, (clientY - window.innerHeight + EDGE) / EDGE);
            }
            if (speed) window.scrollBy(0, speed);
            update();
            rafId = requestAnimationFrame(frame);
        }

        function onMove(e) {
            if (e.pointerId !== pointerId) return;
            if (isMouse && e.buttons === 0) { end(); return; } // 離したことが pointerup で届かなかった
            e.preventDefault();
            clientY = e.clientY;
            if (!moved && Math.abs(clientY + window.scrollY - startPageY) > 3) moved = true;
            update();
        }

        function onUp(e) {
            if (e.pointerId === pointerId) end();
        }

        function end() {
            if (ended) return;
            ended = true;
            endActive = null;
            cancelAnimationFrame(rafId);
            window.removeEventListener('pointermove', onMove);
            window.removeEventListener('pointerup', onUp);
            window.removeEventListener('pointercancel', onUp);
            window.removeEventListener('blur', end);
            root.classList.remove('is-drag-sorting');
            // 最後の入れ替えが DOM に出てから、つかんだ行を元の位置に収める
            requestAnimationFrame(() => {
                if (pending) {
                    if (groupRows(row).indexOf(row) === pending.expected) slide(pending.neighbor, pending.neighborTop);
                    pending = null;
                }
                place();
                void row.offsetHeight;
                row.style.transition = `transform ${SLIDE_MS}ms ease`;
                row.style.transform = '';
                setTimeout(() => {
                    row.classList.remove('is-sorting');
                    row.style.transition = '';
                }, SLIDE_MS);
            });
        }

        endActive = end;
        window.addEventListener('pointermove', onMove, { passive: false });
        window.addEventListener('pointerup', onUp);
        window.addEventListener('pointercancel', onUp);
        window.addEventListener('blur', end);
        rafId = requestAnimationFrame(frame);
    }

    window.biDragSort = { start };
})();
