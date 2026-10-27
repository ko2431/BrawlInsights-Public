// /app/static/js/sw.js（/sw.js で配る。main.py の service_worker）
// プッシュ通知を受け取る Service Worker。プッシュ通知をオンにしたときだけ登録する（push.js）。
// - fetch のハンドラーは書かない（ページの読み込み・キャッシュには手を出さない）
// - 通知を押したら、開いているタブがあればそこで開き、なければ新しいタブで開く
// - 購読が切れて作り直されたら、サーバーの登録も付け替える

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

self.addEventListener('push', (event) => {
    let data = {};
    try {
        data = event.data ? event.data.json() : {};
    } catch (e) {
        data = {};
    }
    const title = data.title || 'Brawl Insights';
    event.waitUntil(self.registration.showNotification(title, {
        body: data.body || '',
        icon: data.icon || '/static/images/ui/logo.png',
        tag: data.tag || undefined,
        // 同じ tag で置き換えたときも知らせる（まとめた件数が変わったとき）
        renotify: !!data.tag,
        data: { url: data.url || '/' },
    }));
});

self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    const url = new URL((event.notification.data && event.notification.data.url) || '/', self.location.origin).href;
    event.waitUntil((async () => {
        const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
        const same = windows.find((c) => c.url.startsWith(self.location.origin));
        if (same) {
            try {
                await same.focus();
                if (same.url !== url && 'navigate' in same) await same.navigate(url);
                return;
            } catch (e) {
                // このタブを操作できないとき（ほかの Service Worker の管理下など）は、新しいタブで開く
            }
        }
        await self.clients.openWindow(url);
    })());
});

self.addEventListener('pushsubscriptionchange', (event) => {
    event.waitUntil((async () => {
        const old = event.oldSubscription;
        const options = old && old.options;
        if (!options || !options.applicationServerKey) return;
        const sub = event.newSubscription || await self.registration.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: options.applicationServerKey,
        });
        const json = sub.toJSON();
        if (old) {
            await fetch('/ja/push/devices/remove', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ token: old.endpoint }),
            }).catch(() => {});
        }
        // 言語は分からないので、前回の登録のまま（サーバーの upsert）ではなく、日本語で作り直す。次にページを開いたとき push.js が付け直す
        await fetch('/ja/push/devices', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ platform: 'web', token: sub.endpoint, p256dh: json.keys.p256dh, auth: json.keys.auth }),
        }).catch(() => {});
    })());
});
