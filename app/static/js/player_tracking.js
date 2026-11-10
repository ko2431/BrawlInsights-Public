// プレイヤーページの「自動追跡・保存期間」カード（player/includes/tracking_card.html の playerTrackingCard）
// ゲージは目盛りの区間ごとに伸縮させる（数分〜365日、4ヶ月〜10年を1本のゲージで見せるため）
// 延長はカード内のパネルで期間と支払い方法を選び、ページを読み込み直さずにその場で反映する
(function () {
    const TRACK_TICKS = [0, 24, 96, 240, 720, 2160, 8760]; // 時間（0・1日・4日・10日・1ヶ月・3ヶ月・1年）
    const RET_TICKS = [0, 4, 12, 36, 60, 120]; // 月（0・4ヶ月・1年・3年・5年・10年）

    // 目盛りの区間ごとに直線で割り当てた位置（0〜100）
    function scalePct(value, ticks) {
        if (!(value > 0)) return 0;
        const last = ticks.length - 1;
        for (let i = 0; i < last; i++) {
            if (value <= ticks[i + 1]) {
                return ((i + (value - ticks[i]) / (ticks[i + 1] - ticks[i])) / last) * 100;
            }
        }
        return 100;
    }

    function clampInt(value, min, max) {
        const n = Math.floor(Number(value));
        if (!Number.isFinite(n)) return min;
        return Math.min(Math.max(n, min), max);
    }

    // 広告による無料延長（アプリだけ）。公開版では使えない扱いになる
    let trackingAd = {
        left: () => 0,
        run: async () => ({ error: '' }),
    };
    // [この部分は公開用リポジトリでは非公開にされています]

    window.playerTrackingCard = function (cfg) {
        const P = cfg.pricing;
        const ja = cfg.lang === 'ja';
        const newSection = (custom) => ({ open: false, preset: '1', custom, pay: null, busy: false, error: '', flash: '', flashTimer: null });
        return {
            cfg,
            tokens: cfg.tokens,
            elixirs: cfg.elixirs,
            expiresAt: Date.now() + (cfg.remaining || 0) * 1000,
            now: Date.now(),
            retentionMonths: cfg.retentionMonths,
            adLeft: 0,
            track: newSection(30),
            ret: newSection(12),
            timer: null,

            init() {
                this.adLeft = trackingAd.left(cfg);
                // 残り時間は30秒ごとに進める（画面内の計算だけ）
                this.timer = setInterval(() => { this.now = Date.now(); }, 30000);
            },
            destroy() {
                clearInterval(this.timer);
                clearTimeout(this.track.flashTimer);
                clearTimeout(this.ret.flashTimer);
            },

            // --- 表示の整形 ---
            num(n) {
                return Number(n || 0).toLocaleString('en-US');
            },
            formatRemaining(sec) {
                if (!(sec > 0)) return '—';
                // 分単位で切り上げる（延長した直後に「あと11日23時間」のように1秒差で表示しないため）
                const total = Math.ceil(sec / 60);
                const d = Math.floor(total / 1440);
                const h = Math.floor((total % 1440) / 60);
                const m = total % 60;
                if (ja) {
                    if (d > 0) return `あと${d}日${h}時間`;
                    if (h > 0) return `あと${h}時間${m}分`;
                    return `あと${m}分`;
                }
                if (d > 0) return `${d}d ${h}h left`;
                if (h > 0) return `${h}h ${m}m left`;
                return `${m}m left`;
            },
            formatMonths(months) {
                const y = Math.floor(months / 12);
                const m = months % 12;
                if (ja) return `${y ? `${y}年` : ''}${m ? `${m}ヶ月` : ''}`;
                const parts = [];
                if (y) parts.push(`${y} year${y > 1 ? 's' : ''}`);
                if (m) parts.push(`${m} month${m > 1 ? 's' : ''}`);
                return parts.join(' ');
            },
            // 保存期間を過ぎて消える境目の日付（サーバーの削除処理と同じく「今から◯ヶ月前」）
            cutoffText(months) {
                const d = new Date();
                d.setMonth(d.getMonth() - months);
                if (ja) return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日より前のバトルは順次削除されます。`;
                return `Battles before ${d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })} are removed over time.`;
            },
            loginUrl() {
                return `/${cfg.lang}/login?next=${encodeURIComponent(location.pathname + location.search)}`;
            },

            // --- 自動追跡 ---
            get remainingSec() {
                return Math.max(0, (this.expiresAt - this.now) / 1000);
            },
            get active() {
                return this.remainingSec > 0;
            },
            get remainingText() {
                return this.formatRemaining(this.remainingSec);
            },
            get trackPct() {
                return scalePct(this.remainingSec / 3600, TRACK_TICKS);
            },
            get autoPct() {
                return scalePct(cfg.autoHours, TRACK_TICKS);
            },
            // 閲覧すれば延びる範囲（今の残り時間より閲覧で延びる時間の方が長いとき）
            get autoRegion() {
                return cfg.autoHours > 0 && cfg.autoHours * 3600 > this.remainingSec;
            },
            get autoRegionStyle() {
                return `left: ${this.trackPct}%; width: ${Math.max(0, this.autoPct - this.trackPct)}%;`;
            },
            get trackMaxDays() {
                return Math.max(0, Math.ceil((P.trackingMaxDays * 86400 - this.remainingSec) / 86400));
            },
            get trackDays() {
                if (this.track.preset === '1') return 1;
                if (this.track.preset === '10') return 10;
                return clampInt(this.track.custom, 1, Math.max(1, this.trackMaxDays));
            },
            get trackCost() {
                const bundles = Math.floor(this.trackDays / P.trackingBundleDays);
                const rest = this.trackDays % P.trackingBundleDays;
                return {
                    tokens: bundles * P.trackingBundleTokens + rest * P.trackingDayTokens,
                    elixirs: bundles * P.trackingBundleElixirs + rest * P.trackingDayElixirs,
                };
            },
            get trackAfterSec() {
                return this.remainingSec + this.trackDays * 86400;
            },
            get trackPreviewStyle() {
                const to = scalePct(this.trackAfterSec / 3600, TRACK_TICKS);
                if (to <= this.trackPct) return '';
                return `left: ${this.trackPct}%; width: ${to - this.trackPct}%;`;
            },

            // --- 保存期間 ---
            get retPct() {
                return scalePct(this.retentionMonths, RET_TICKS);
            },
            get retMaxMonths() {
                return Math.max(0, P.retentionMaxMonths - this.retentionMonths);
            },
            get retMonths() {
                if (this.ret.preset === '1') return 1;
                return clampInt(this.ret.custom, 1, Math.max(1, this.retMaxMonths));
            },
            get retCost() {
                return { tokens: this.retMonths * P.retentionMonthTokens, elixirs: this.retMonths * P.retentionMonthElixirs };
            },
            get retPreviewStyle() {
                const to = scalePct(this.retentionMonths + this.retMonths, RET_TICKS);
                if (to <= this.retPct) return '';
                return `left: ${this.retPct}%; width: ${to - this.retPct}%;`;
            },

            // --- パネルの操作 ---
            togglePanel(kind) {
                const s = this[kind];
                s.open = !s.open;
                s.error = '';
                if (s.open) {
                    this.adLeft = trackingAd.left(cfg);
                    // 上限が近く10日を選べないときは1日に戻す
                    if (kind === 'track' && s.preset === '10' && this.trackMaxDays < 10) s.preset = '1';
                    this.clampCustom(kind);
                }
            },
            clampCustom(kind) {
                const max = kind === 'track' ? this.trackMaxDays : this.retMaxMonths;
                this[kind].custom = clampInt(this[kind].custom, 1, Math.max(1, max));
            },
            step(kind, delta) {
                const max = Math.max(1, kind === 'track' ? this.trackMaxDays : this.retMaxMonths);
                let next = clampInt(this[kind].custom, 1, max) + delta;
                // 自動追跡は10日ごとにまとめ買いの料金になり、末尾が9の日数（9・19・29日…）は次の10日分と同じ料金のため、−／＋では飛ばす。
                // 飛ばした先が上限を超えるときは、末尾が9の日数のままにする（欄に直接入力した値はそのまま使う）
                if (kind === 'track' && next % P.trackingBundleDays === P.trackingBundleDays - 1 && next + delta >= 1 && next + delta <= max) {
                    next += delta;
                }
                this[kind].custom = clampInt(next, 1, max);
            },

            // --- 支払い ---
            costOf(kind) {
                return kind === 'track' ? this.trackCost : this.retCost;
            },
            payState(kind, key) {
                if (key === 'ad') {
                    const available = kind === 'track' && this.trackDays === 1 && this.adLeft > 0;
                    return { available, usable: available };
                }
                if (!cfg.loggedIn) return { available: true, usable: false };
                const cost = this.costOf(kind);
                const usable = key === 'elixir' ? (this.elixirs || 0) >= cost.elixirs : (this.tokens || 0) >= cost.tokens;
                return { available: true, usable };
            },
            // 選んだ支払い方法が使えなければ、広告→エリクサー→トークンの順に使えるものにする
            pay(kind) {
                const chosen = this[kind].pay;
                if (chosen && this.payState(kind, chosen).usable) return chosen;
                return ['ad', 'elixir', 'token'].find((key) => this.payState(kind, key).usable) || null;
            },
            balanceText(key) {
                if (!cfg.loggedIn) return ja ? 'ログインが必要' : 'Log in required';
                const balance = key === 'elixir' ? this.elixirs : this.tokens;
                return ja ? `所持 ${this.num(balance)}` : `You have ${this.num(balance)}`;
            },
            balanceChangeText(kind) {
                const pay = this.pay(kind);
                const cost = this.costOf(kind);
                if (pay === 'elixir') return `🧪 ${this.num(this.elixirs)} → ${this.num(this.elixirs - cost.elixirs)}`;
                if (pay === 'token') return `${ja ? 'トークン' : 'Tokens'} ${this.num(this.tokens)} → ${this.num(this.tokens - cost.tokens)}`;
                return '';
            },
            submitLabel(kind) {
                const pay = this.pay(kind);
                const cost = this.costOf(kind);
                const period = kind === 'track'
                    ? (ja ? `${this.trackDays}日` : `${this.trackDays} day${this.trackDays > 1 ? 's' : ''}`)
                    : (ja ? `${this.retMonths}ヶ月` : `${this.retMonths} month${this.retMonths > 1 ? 's' : ''}`);
                if (pay === 'ad') return ja ? '広告を見て1日延長する' : 'Watch an ad to extend 1 day';
                if (pay === 'elixir') return ja ? `🧪${this.num(cost.elixirs)}で${period}延長する` : `Extend ${period} for 🧪${this.num(cost.elixirs)}`;
                return ja ? `${this.num(cost.tokens)}トークンで${period}延長する` : `Extend ${period} for ${this.num(cost.tokens)} tokens`;
            },

            // --- 延長 ---
            async submit(kind) {
                const s = this[kind];
                const pay = this.pay(kind);
                if (s.busy || !pay) return;
                s.busy = true;
                s.error = '';
                try {
                    if (pay === 'ad') {
                        const result = await trackingAd.run(cfg, ja);
                        this.adLeft = trackingAd.left(cfg);
                        if (result.error) s.error = result.error;
                        else this.applyResult('track', result.data);
                    } else {
                        await this.extendWithCurrency(kind, pay);
                    }
                } finally {
                    s.busy = false;
                }
            },
            async extendWithCurrency(kind, pay) {
                const s = this[kind];
                const tag = cfg.tag.replace('#', '');
                const url = kind === 'track' ? `/${cfg.lang}/player/extend-tracking/${tag}` : `/${cfg.lang}/player/extend-retention/${tag}`;
                const body = kind === 'track' ? { days: this.trackDays, pay } : { months: this.retMonths, pay };
                let res;
                let data = {};
                try {
                    res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
                    data = await res.json().catch(() => ({}));
                } catch (error) {
                    console.error('Error extending tracking/retention:', error);
                    s.error = ja ? '通信中にエラーが発生しました。' : 'A network error occurred.';
                    return;
                }
                if (res.status === 401) {
                    s.error = ja ? 'ログインの有効期限が切れています。ログインし直してください。' : 'Your session has expired. Please log in again.';
                    return;
                }
                if (!res.ok || !data.success) {
                    s.error = data.message || (ja ? '延長できませんでした。' : 'Could not extend.');
                    return;
                }
                this.applyResult(kind, data);
            },
            applyResult(kind, data) {
                const s = this[kind];
                if (typeof data.tokens === 'number') this.tokens = data.tokens;
                if (typeof data.elixirs === 'number') this.elixirs = data.elixirs;
                if (kind === 'track' && typeof data.remaining_seconds === 'number') {
                    this.now = Date.now();
                    this.expiresAt = this.now + data.remaining_seconds * 1000;
                }
                if (kind === 'ret' && typeof data.retention_months === 'number') {
                    this.retentionMonths = data.retention_months;
                }
                s.open = false;
                s.pay = null;
                s.flash = data.message || '';
                clearTimeout(s.flashTimer);
                s.flashTimer = setTimeout(() => { s.flash = ''; }, 4000);
            },
        };
    };
})();
