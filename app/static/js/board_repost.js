// /app/static/js/board_repost.js
// なんでも掲示板の投稿のリポスト（掲示板のカード・チャットのメニューで共通）。window.BIRepost として使う。
// 押せるかどうかの最終判定はサーバー（app/services/board_repost_service.py）。ここでは表示時の状態から、
// グレーアウトと押したときの理由のアラートを出す。時刻で変わる判定は押したときにも確かめ直す。
(function () {
    'use strict';
    if (window.BIRepost) return;

    function parseUtc(str) {
        if (!str) return null;
        const date = new Date(str.includes('T') ? str : str.replace(' ', 'T'));
        return Number.isNaN(date.getTime()) ? null : date;
    }

    // カード・メニュー項目の data-repost-* から状態を読む
    function readState(el) {
        const d = el.dataset;
        return {
            postId: d.postId,
            cost: parseInt(d.repostCost || '0', 10),
            tokens: parseInt(d.repostTokens || '0', 10),
            prohibited: d.repostProhibited === '1',
            postAvailableAt: d.repostPostAt || null,
            userAvailableAt: d.repostUserAt || null,
        };
    }

    function writeState(el, state) {
        el.dataset.repostTokens = String(state.tokens);
        el.dataset.repostPostAt = state.postAvailableAt || '';
        el.dataset.repostUserAt = state.userAvailableAt || '';
    }

    // 今リポストできない理由（できるなら null）。待ち時間は投稿単位・ユーザー単位の遅いほう
    function evaluate(state) {
        if (state.prohibited) return { code: 'prohibit_posting' };
        const now = Date.now();
        const postAt = parseUtc(state.postAvailableAt)?.getTime() || 0;
        const userAt = parseUtc(state.userAvailableAt)?.getTime() || 0;
        if (postAt > now || userAt > now) {
            return postAt >= userAt
                ? { code: 'cooldown', waitSeconds: (postAt - now) / 1000 }
                : { code: 'user_interval', waitSeconds: (userAt - now) / 1000 };
        }
        if (state.tokens < state.cost) return { code: 'insufficient_tokens' };
        return null;
    }

    function formatWait(seconds, lang) {
        const total = Math.max(1, Math.ceil(seconds / 60));
        const hours = Math.floor(total / 60);
        const minutes = total % 60;
        if (lang === 'ja') {
            if (hours && minutes) return `${hours}時間${minutes}分`;
            return hours ? `${hours}時間` : `${minutes}分`;
        }
        const parts = [];
        if (hours) parts.push(`${hours} hour${hours !== 1 ? 's' : ''}`);
        if (minutes) parts.push(`${minutes} minute${minutes !== 1 ? 's' : ''}`);
        return parts.join(' ');
    }

    function blockedMessage(blocked, state, lang) {
        const wait = formatWait(blocked.waitSeconds || 0, lang);
        const ja = lang === 'ja';
        switch (blocked.code) {
            case 'prohibit_posting':
                return ja ? '投稿が制限されているため、リポストできません。' : "You can't repost because posting is restricted on your account.";
            case 'cooldown':
                return ja ? `この投稿は、あと${wait}でリポストできます。` : `You can repost this post in ${wait}.`;
            case 'user_interval':
                return ja
                    ? `リポストは30分に1回までです。あと${wait}でリポストできます。`
                    : `You can repost once every 30 minutes. You can repost again in ${wait}.`;
            default:
                return ja
                    ? `トークンが足りないため、リポストできません（${state.cost}トークン必要です。現在のトークン数: ${state.tokens}）。`
                    : `You don't have enough tokens to repost (${state.cost} tokens needed. You have ${state.tokens}).`;
        }
    }

    // グレーアウトの切り替え（押せないときも押下でアラートを出すため disabled にはしない）
    function applyState(el, mutedClass) {
        const blocked = evaluate(readState(el));
        el.classList.toggle(mutedClass, Boolean(blocked));
        el.setAttribute('aria-disabled', blocked ? 'true' : 'false');
    }

    // 確認 → リポスト → 結果のアラート。成功したらサーバーの応答（新しい状態を含む）を返す
    async function run(el, lang) {
        if (el.dataset.processing === '1') return null;
        const state = readState(el);
        const blocked = evaluate(state);
        if (blocked) {
            alert(blockedMessage(blocked, state, lang));
            return null;
        }
        const after = state.tokens - state.cost;
        const message = lang === 'ja'
            ? `${state.cost}トークン消費して、この投稿をリポストしますか？\n投稿が「最新」のタイムラインに掲載されます。\n(トークン数: ${state.tokens} → ${after})`
            : `Spend ${state.cost} tokens to repost this post?\nIt will be shown at the top of the "Latest" timeline.\n(Tokens: ${state.tokens} → ${after})`;
        if (!confirm(message)) return null;

        el.dataset.processing = '1';
        try {
            const response = await fetch(`/${lang}/boards/posts/${state.postId}/repost`, { method: 'POST' });
            const data = await response.json().catch(() => ({}));
            if (!response.ok || !data.success) {
                if (response.status === 401) {
                    alert(lang === 'ja'
                        ? 'この機能を利用するにはログインが必要です。アカウントタブより、メールアドレス不要でログインできます。'
                        : 'You need to log in to use this feature. You can log in from the Account tab without an email address.');
                } else {
                    alert(data.detail || (lang === 'ja' ? 'リポストに失敗しました。' : 'Failed to repost.'));
                }
                return null;
            }
            writeState(el, {
                tokens: data.tokens,
                postAvailableAt: data.post_available_at,
                userAvailableAt: data.user_available_at,
            });
            alert(data.message);
            return data;
        } catch {
            alert(lang === 'ja' ? 'エラーが発生しました' : 'An error occurred');
            return null;
        } finally {
            el.dataset.processing = '0';
        }
    }

    window.BIRepost = { applyState, run, writeState, parseUtc };
})();
