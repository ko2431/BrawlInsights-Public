// 用語の横の「?」で説明の吹き出しを出す（helpTip。テンプレートは components/help_tip.html）。マウスはホバー、タッチはタップで開く
// 大会開催ツールの tnHelp から共通の部品にした（board_campaign_plan BD28）
// 吹き出しは画面からはみ出さないよう、開くたびに「?」の位置から計算して固定配置する
// 開いている吹き出し（吹き出しの要素 → 閉じる関数）。別の「?」で開いた吹き出しと重なるものだけ閉じる（重ならなければ両方出したまま）。
// Alpine の this は呼び出しごとに別の Proxy になることがあるので、同じ吹き出しかどうかは要素で見分ける
var helpTipOpen = window.__helpTipOpen || (window.__helpTipOpen = new Map()); // 2回読み込まれても同じものを使う
function helpTip() {
    const canHover = window.matchMedia('(hover: hover)').matches;
    return {
        open: false,
        hiding: false, // 消えるときは少し縮みながら消す
        hideTimer: null,
        style: '',
        show() {
            clearTimeout(this.hideTimer);
            this.hiding = false;
            if (this.open) return;
            this.open = true;
            helpTipOpen.set(this.$refs.bubble, () => this.hide());
            this.$nextTick(() => {
                const btn = this.$refs.btn.getBoundingClientRect();
                const bubble = this.$refs.bubble;
                const width = Math.min(280, window.innerWidth - 24);
                const left = Math.min(Math.max(12, btn.left + btn.width / 2 - width / 2), window.innerWidth - width - 12);
                this.style = `width: ${width}px; left: ${left}px; top: ${btn.bottom + 6}px;`;
                // 下に入りきらなければ上に出す
                this.$nextTick(() => {
                    if (btn.bottom + 6 + bubble.offsetHeight > window.innerHeight - 8) {
                        this.style = `width: ${width}px; left: ${left}px; top: ${Math.max(8, btn.top - 6 - bubble.offsetHeight)}px;`;
                    }
                    this.$nextTick(() => this.hideOverlapped());
                });
            });
        },
        hideOverlapped() {
            const mine = this.$refs.bubble;
            if (!this.open || this.hiding) return;
            const box = mine.getBoundingClientRect();
            helpTipOpen.forEach((hide, other) => {
                if (other === mine) return;
                if (!other.isConnected) { helpTipOpen.delete(other); return; }
                if (other.classList.contains('is-hiding') || getComputedStyle(other).display === 'none') return;
                const rect = other.getBoundingClientRect();
                if (rect.left < box.right && box.left < rect.right && rect.top < box.bottom && box.top < rect.bottom) hide();
            });
        },
        hide() {
            if (!this.open || this.hiding) return;
            this.hiding = true;
            const bubble = this.$refs.bubble;
            this.hideTimer = setTimeout(() => { this.open = false; this.hiding = false; helpTipOpen.delete(bubble); }, 200);
        },
        destroy() {
            clearTimeout(this.hideTimer);
            helpTipOpen.delete(this.$refs.bubble);
        },
        // マウスで乗せた時点で開いているので、PC ではクリックで閉じない（閉じるのは離したとき・外を押したとき・Esc）
        toggle() { if (!canHover && this.open && !this.hiding) this.hide(); else this.show(); },
        enter() { if (canHover) this.show(); },
        leave() { if (canHover) this.hide(); },
    };
}
