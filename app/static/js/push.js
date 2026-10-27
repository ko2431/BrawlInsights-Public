// /app/static/js/push.js
// プッシュ通知（検討メモ notes_private/push_image_plan_2026-10-27.md の 3.7）。ログインしている人のページで読み込む（base.html の window.BI_PUSH）。
// - Web: 通知の許可 → Service Worker（/sw.js）の登録 → 購読 → サーバーに登録（/{lang}/push/devices）。オフ・ログアウトの前は購読をやめて消す
// - ページを開いたとき、購読が残っていれば1日1回サーバーの登録を付け直す。別のアカウントでオンにした購読なら消す
// - アプリ版は次のアプリの更新で対応する（今は案内だけ）
// - 案内（大会のエントリー・募集の投稿の後）: BIPush.suggestLater(kind) を呼ぶと、次に開いたページ（または showPending を呼んだとき）に1回だけ出す。
//   「あとで」なら14日出さない
(function () {
    const cfg = window.BI_PUSH;
    if (!cfg || window.BIPush) return;
    const ja = cfg.lang !== 'en';
    const STORE_KEY = 'bi_push'; // localStorage: { uid, endpoint, syncedAt }
    const SNOOZE_KEY = 'bi_push_suggest_snooze';
    const PENDING_KEY = 'bi_push_suggest'; // sessionStorage
    const SNOOZE_MS = 14 * 24 * 3600 * 1000;
    const SYNC_MS = 24 * 3600 * 1000;
    const LOGOUT_WAIT_MS = 1500;
    const SUGGEST_TEXT = {
        tournament: ['試合の開始やチェックインの開始を、プッシュ通知で受け取れます。', 'Get push notifications when your match or check-in starts.'],
        board: ['この投稿にメッセージが届いたら、プッシュ通知で受け取れます。', 'Get a push notification when someone messages on your post.'],
    };

    function isApp() {
        return !!(window.Capacitor && typeof window.Capacitor.isNativePlatform === 'function' && window.Capacitor.isNativePlatform());
    }
    function isIosBrowser() {
        const ua = navigator.userAgent;
        return /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
    }
    function webSupported() {
        return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window && !!cfg.vapidKey;
    }
    function readStore() {
        try { return JSON.parse(localStorage.getItem(STORE_KEY) || 'null'); } catch (e) { return null; }
    }
    function writeStore(value) {
        try {
            if (value) localStorage.setItem(STORE_KEY, JSON.stringify(value));
            else localStorage.removeItem(STORE_KEY);
        } catch (e) { /* 保存できない環境では、毎回サーバーに付け直す */ }
    }
    function keyBytes(base64) {
        const padded = (base64 + '='.repeat((4 - (base64.length % 4)) % 4)).replace(/-/g, '+').replace(/_/g, '/');
        return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
    }
    function sameKey(sub) {
        const current = sub.options && sub.options.applicationServerKey;
        if (!current) return false;
        const a = new Uint8Array(current);
        const b = keyBytes(cfg.vapidKey);
        return a.length === b.length && a.every((v, i) => v === b[i]);
    }
    async function post(path, body) {
        const res = await fetch(`/${cfg.lang}/push/${path}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
    }
    async function currentSubscription() {
        const reg = await navigator.serviceWorker.getRegistration('/');
        return reg ? reg.pushManager.getSubscription() : null;
    }
    async function register(sub) {
        const json = sub.toJSON();
        await post('devices', { platform: 'web', token: sub.endpoint, p256dh: json.keys.p256dh, auth: json.keys.auth });
        writeStore({ uid: cfg.uid, endpoint: sub.endpoint, syncedAt: Date.now() });
    }

    // この端末の状態: on / off / denied（ブラウザでブロック）/ unsupported / ios_browser / insecure（HTTPS でないページ）/
    // app_pending（アプリは準備中）/ app_update（アプリの更新が要る）
    async function state() {
        if (isApp()) return cfg.appReleased ? 'app_update' : 'app_pending';
        if (isIosBrowser() && !webSupported()) return 'ios_browser';
        // ブラウザは HTTPS（と localhost）のページでしかプッシュ通知を使わせない（開発環境を IP アドレスで開いたときなど）
        if (!window.isSecureContext) return 'insecure';
        if (!webSupported()) return 'unsupported';
        if (Notification.permission === 'denied') return 'denied';
        if (Notification.permission !== 'granted') return 'off';
        const sub = await currentSubscription();
        const store = readStore();
        return sub && store && store.uid === cfg.uid && store.endpoint === sub.endpoint ? 'on' : 'off';
    }

    // ボタンを押したときに呼ぶ（ブラウザは、ユーザーの操作なしに許可を求められない）
    async function enable() {
        if (isApp() || !webSupported()) return state();
        const permission = await Notification.requestPermission();
        if (permission !== 'granted') return permission === 'denied' ? 'denied' : 'off';
        const reg = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
        await navigator.serviceWorker.ready;
        let sub = await reg.pushManager.getSubscription();
        // 前の鍵で作った購読は届かないので作り直す
        if (sub && !sameKey(sub)) {
            await sub.unsubscribe().catch(() => {});
            sub = null;
        }
        if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(cfg.vapidKey) });
        await register(sub);
        return 'on';
    }

    async function disable() {
        if (!isApp() && webSupported()) {
            const sub = await currentSubscription();
            if (sub) {
                await post('devices/remove', { token: sub.endpoint }).catch(() => {});
                await sub.unsubscribe().catch(() => {});
            }
        }
        writeStore(null);
        return state();
    }

    // ログアウトの前（共有の端末で、前の人の通知が届かないように）。待つのは最大1.5秒
    function beforeLogout() {
        if (isApp() || !webSupported() || Notification.permission !== 'granted') return Promise.resolve();
        return Promise.race([disable().catch(() => {}), new Promise((resolve) => setTimeout(resolve, LOGOUT_WAIT_MS))]);
    }

    async function sync() {
        if (isApp() || !webSupported() || Notification.permission !== 'granted') return;
        const sub = await currentSubscription();
        if (!sub) {
            writeStore(null);
            return;
        }
        const store = readStore();
        if (!store || store.uid !== cfg.uid) {
            // この人がオンにした購読ではない（別のアカウントのまま残っていた）ので消す
            await post('devices/remove', { token: sub.endpoint }).catch(() => {});
            await sub.unsubscribe().catch(() => {});
            writeStore(null);
            return;
        }
        if (store.endpoint !== sub.endpoint || Date.now() - (store.syncedAt || 0) > SYNC_MS) await register(sub);
    }

    // --- 案内 ---------------------------------------------------------------------------------
    function snoozed() {
        try { return Date.now() - Number(localStorage.getItem(SNOOZE_KEY) || 0) < SNOOZE_MS; } catch (e) { return false; }
    }
    function snooze() {
        try { localStorage.setItem(SNOOZE_KEY, String(Date.now())); } catch (e) { /* 保存できなければ、次も出す */ }
    }
    function suggestLater(kind) {
        try { sessionStorage.setItem(PENDING_KEY, kind); } catch (e) { /* 出さないだけ */ }
    }

    function hideCard(el, delay) {
        setTimeout(() => {
            el.classList.remove('is-shown');
            setTimeout(() => el.remove(), 250);
        }, delay || 0);
    }

    async function suggest(kind) {
        const text = SUGGEST_TEXT[kind];
        if (!text || snoozed() || document.querySelector('.bi-push-suggest')) return;
        if (isApp() || !webSupported() || Notification.permission !== 'default' || (await state()) !== 'off') return;
        const el = document.createElement('div');
        el.className = 'bi-push-suggest';
        el.setAttribute('role', 'dialog');
        el.setAttribute('aria-live', 'polite');
        el.innerHTML = `
            <img class="bi-push-suggest__icon" src="/static/images/ui/notification_green.png" alt="">
            <div class="bi-push-suggest__body">
                <p class="bi-push-suggest__text"></p>
                <div class="bi-push-suggest__actions">
                    <button type="button" class="bi-push-suggest__btn bi-push-suggest__btn--primary" data-act="on">${ja ? 'オンにする' : 'Turn on'}</button>
                    <button type="button" class="bi-push-suggest__btn" data-act="later">${ja ? 'あとで' : 'Not now'}</button>
                </div>
            </div>`;
        const textEl = el.querySelector('.bi-push-suggest__text');
        const actions = el.querySelector('.bi-push-suggest__actions');
        textEl.textContent = ja ? text[0] : text[1];
        el.addEventListener('click', async (e) => {
            const act = e.target.closest('[data-act]')?.dataset.act;
            if (act === 'later') {
                snooze();
                hideCard(el);
            } else if (act === 'on') {
                actions.querySelectorAll('button').forEach((b) => { b.disabled = true; });
                let result = 'error';
                try { result = await enable(); } catch (err) { result = 'error'; }
                actions.remove();
                if (result === 'on') {
                    textEl.textContent = ja ? 'プッシュ通知をオンにしました。受け取る通知は、アカウントタブのオプションで選べます。'
                        : 'Push notifications are on. Choose what you get in Optional Settings on the Account tab.';
                } else if (result === 'denied') {
                    textEl.textContent = ja ? 'ブラウザで通知がブロックされています。ブラウザの設定で、このサイトの通知を許可してください。'
                        : 'Notifications are blocked in your browser. Allow notifications for this site in your browser settings.';
                } else if (result === 'off') {
                    snooze();
                    hideCard(el);
                    return;
                } else {
                    textEl.textContent = ja ? 'オンにできませんでした。時間をおいてもう一度お試しください。' : "Couldn't turn them on. Please try again later.";
                }
                hideCard(el, 5000);
            }
        });
        document.body.appendChild(el);
        requestAnimationFrame(() => requestAnimationFrame(() => el.classList.add('is-shown')));
    }

    // suggestLater で積んだ案内を出す（ページを開いたとき・投稿の後に一覧だけ読み直したとき）
    function showPending(delay) {
        let pending = null;
        try {
            pending = sessionStorage.getItem(PENDING_KEY);
            if (pending) sessionStorage.removeItem(PENDING_KEY);
        } catch (e) { pending = null; }
        if (pending) setTimeout(() => suggest(pending).catch(() => {}), delay || 0);
    }

    window.BIPush = { state, enable, disable, beforeLogout, suggest, suggestLater, showPending, webSupported };

    // アカウントタブのオプションの「プッシュ通知」（account_settings.html）。種類ごとの設定はアカウントごと、受け取るかどうかは端末ごと
    window.biPushSettings = function (init) {
        // この端末で受け取れないとき・切り替えられないときの説明（スイッチの下に出し、押したときにも出す）
        const NOTE = {
            denied: ['ブラウザで通知がブロックされています。ブラウザの設定で、このサイトの通知を許可すると受け取れます。', 'Notifications are blocked in your browser. Allow notifications for this site in your browser settings to receive them.'],
            unsupported: ['このブラウザでは、プッシュ通知を受け取れません。', "This browser can't receive push notifications."],
            // アプリ版のプッシュ通知（15.3）が出たら、アプリを案内する（cfg.appReleased）
            ios_browser: cfg.appReleased
                ? ['iPhone・iPadのブラウザでは、プッシュ通知を受け取れません。アプリ版なら、プッシュ通知を受け取れます。', "Push notifications aren't available in browsers on iPhone and iPad. Use the app to get push notifications."]
                : ['iPhone・iPadのブラウザでは、プッシュ通知を受け取れません。', "Push notifications aren't available in browsers on iPhone and iPad."],
            app_pending: ['アプリ版のプッシュ通知は、今後のアップデートで対応します。', 'Push notifications in the app are coming in a future update.'],
            app_update: ['アプリを最新版に更新すると、プッシュ通知を受け取れます。', 'Update the app to the latest version to receive push notifications.'],
            insecure: ['プッシュ通知は、HTTPSで開いたページでのみ使えます。', 'Push notifications are only available on pages opened over HTTPS.'],
        };
        const DEVICE_OFF = ['「この端末で受け取る」をオンにすると、受け取る通知を選べます。', 'Turn on "Receive on This Device" to choose which notifications you get.'];
        const SAVE_DELAY_MS = 500; // 続けて押したときは、止まってから最後の状態だけ保存する
        const IN_APP_OFF = ['上の通知設定でこの通知がオフになっているため、プッシュ通知でも届きません。先に通知設定でオンにしてください。',
            'This notification is turned off in the notification settings above, so it is not sent as a push notification either. Turn it on there first.'];
        const ERROR = ['エラーが発生しました。設定を変更できませんでした。', 'An error occurred. Could not change the setting.'];
        const pick = (pair) => (pair ? pair[ja ? 0 : 1] : '');
        return {
            pushState: 'loading',
            pushBusy: false,
            pushSaving: {},
            pushTimers: {},
            pushValues: init.values || {},
            pushSaved: Object.assign({}, init.values || {}),
            // [この部分は公開用リポジトリでは非公開にされています]
            async init() {
                this.pushState = await state().catch(() => 'unsupported');
            },
            pushNote() {
                return pick(NOTE[this.pushState]);
            },
            pushDeviceLocked() {
                return !['on', 'off', 'loading'].includes(this.pushState);
            },
            // 種類ごとの項目: この端末で受け取れないとき（ブラウザでブロックしたとき以外）は出さない。受け取る設定がオフの間は切り替えられない
            pushDetailsShown() {
                return ['on', 'off', 'denied', 'loading'].includes(this.pushState);
            },
            pushDetailsLocked() {
                return this.pushState !== 'on' && this.pushState !== 'loading';
            },
            // この端末で受け取る（オンにするときはブラウザの確認が出る）
            async togglePushDevice() {
                if (this.pushBusy || this.pushState === 'loading') return;
                if (this.pushDeviceLocked()) {
                    alert(this.pushNote());
                    return;
                }
                const turnOn = this.pushState !== 'on';
                this.pushBusy = true;
                try {
                    this.pushState = turnOn ? await enable() : await disable();
                    if (turnOn && this.pushState === 'denied') alert(pick(NOTE.denied));
                } catch (e) {
                    console.error('Push setting error:', e);
                    alert(pick(ERROR));
                    this.pushState = await state().catch(() => 'unsupported');
                } finally {
                    this.pushBusy = false;
                }
            },
            // 種類ごとの設定。押したらすぐ切り替え、押すのが止まってから保存する（続けて押しても保存は1回）。失敗したら保存済みの状態に戻す
            togglePushSetting(key, inAppOff) {
                if (this.pushDetailsLocked()) {
                    alert(pick(this.pushState === 'denied' ? NOTE.denied : DEVICE_OFF));
                    return;
                }
                if (inAppOff) {
                    alert(pick(IN_APP_OFF));
                    return;
                }
                this.pushValues[key] = !this.pushValues[key];
                clearTimeout(this.pushTimers[key]);
                this.pushTimers[key] = setTimeout(() => this.savePushSetting(key), SAVE_DELAY_MS);
            },
            async savePushSetting(key) {
                const value = !!this.pushValues[key];
                if (this.pushSaving[key] || value === !!this.pushSaved[key]) return;
                this.pushSaving[key] = true;
                try {
                    await post('settings', { setting_key: key, enabled: value });
                    this.pushSaved[key] = value;
                } catch (e) {
                    console.error('Push setting error:', e);
                    this.pushValues[key] = this.pushSaved[key];
                    alert(pick(ERROR));
                } finally {
                    this.pushSaving[key] = false;
                }
                // 保存している間にまた押されていたら、その状態も保存する
                if (!!this.pushValues[key] !== !!this.pushSaved[key]) this.savePushSetting(key);
            },
            // [この部分は公開用リポジトリでは非公開にされています]
        };
    };

    function start() {
        sync().catch(() => {});
        showPending(600);
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
})();
