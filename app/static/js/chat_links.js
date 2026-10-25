// /app/static/js/chat_links.js
// チャットの本文の URL をリンクにする規則（掲示板のチャット・大会のチャットで共通）。window.BIChatLinks として使う。
// http(s)://・www.・サイト内のパス（/ja/... /en/...）と、スキームなしでも許可するドメイン（brawlinsights.com・x.com など）をリンクにする。
// 片方のチャットだけで規則を変えない。
(function () {
    'use strict';
    if (window.BIChatLinks) return;

    const MAX_URL_LENGTH = 2048;
    const MAX_RELATIVE_LENGTH = 200;
    const MAX_LINKS_PER_MESSAGE = 10;
    const DISPLAY_MAX_LENGTH = 70;
    const PATTERN = /https?:\/\/[^\s<]+|www\.[^\s<]+|\/(?:ja|en)\/[A-Za-z0-9\-._\/?=&%#+]+|\b(?:(?:[a-z0-9-]+\.)*)(?:brawlinsights\.com|brawlstars\.com|x\.com|twitter\.com|discord\.gg|discord\.com|youtube\.com|youtu\.be|tiktok\.com)(?:\/[A-Za-z0-9\-._\/?=&%#+]*)?/gi;

    // 表示が長すぎるリンクは途中まで
    function display(text) {
        if (text.length <= DISPLAY_MAX_LENGTH) return text;
        return `${text.slice(0, DISPLAY_MAX_LENGTH)}...`;
    }

    // 文末の句読点・閉じかっこ（対応する開きかっこがないもの）はリンクに含めない
    function stripTrailingPunctuation(rawUrl) {
        let url = rawUrl;
        while (/[.,;:!?。、』」】>]$/u.test(url)) {
            url = url.slice(0, -1);
        }
        while (/[)\]}]$/.test(url)) {
            const closing = url.slice(-1);
            const opening = { ')': '(', ']': '[', '}': '{' }[closing];
            const openCount = url.split(opening).length - 1;
            const closeCount = url.split(closing).length - 1;
            if (closeCount > openCount) {
                url = url.slice(0, -1);
            } else {
                break;
            }
        }
        return url;
    }

    function hostMatchesDomain(hostname, domain) {
        const host = String(hostname || '').toLowerCase().replace(/\.$/, '');
        const base = String(domain || '').toLowerCase();
        return host === base || host.endsWith(`.${base}`);
    }

    function isInternalHostname(hostname) {
        return hostMatchesDomain(hostname, 'brawlinsights.com');
    }

    // スキームなしでリンクにしてよいドメイン
    function isAllowedBareHostname(hostname) {
        const host = String(hostname || '').toLowerCase().replace(/\.$/, '');
        if (!host) return false;
        return isInternalHostname(host) || host === 'youtu.be'
            || ['brawlstars.com', 'x.com', 'twitter.com', 'discord.gg', 'discord.com', 'youtube.com', 'tiktok.com']
                .some((domain) => hostMatchesDomain(host, domain));
    }

    function isValidRelativeSitePath(path) {
        if (!/^\/(?:ja|en)\//.test(path)) return false;
        if (path.length > MAX_RELATIVE_LENGTH) return false;
        return /^\/(?:ja|en)\/[A-Za-z0-9\-._\/?=&%#+]*$/.test(path);
    }

    function resolve(matchedText) {
        const trimmed = stripTrailingPunctuation(matchedText);
        if (!trimmed) return null;

        if (/^\/(?:ja|en)\//.test(trimmed)) {
            if (!isValidRelativeSitePath(trimmed)) return null;
            return { matchedText: trimmed, href: trimmed, isInternal: true };
        }

        const href = /^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
        if (href.length > MAX_URL_LENGTH) return null;

        let parsed;
        try {
            parsed = new URL(href, window.location.origin);
        } catch (e) {
            return null;
        }
        if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null;

        // スキーム無しの許可ドメイン候補はホストを厳格チェック
        if (!/^https?:\/\//i.test(trimmed) && !/^www\./i.test(trimmed) && !isAllowedBareHostname(parsed.hostname)) return null;

        const isInternal = isInternalHostname(parsed.hostname);
        return {
            matchedText: trimmed,
            href: isInternal && /^\/(?:ja|en)\//.test(parsed.pathname + parsed.search + parsed.hash)
                ? `${parsed.pathname}${parsed.search}${parsed.hash}`
                : parsed.href,
            isInternal,
        };
    }

    // 本文の中のリンク（{ start, end, matchedText, href, isInternal }）を前から順に。重なりはない
    function find(text) {
        const pattern = new RegExp(PATTERN.source, PATTERN.flags);
        const matches = [];
        let match;
        while ((match = pattern.exec(text || '')) !== null) {
            const resolved = resolve(match[0]);
            if (!resolved) continue;
            const start = match.index;
            const end = start + resolved.matchedText.length;
            // 句読点除去で短くなった分、regex の lastIndex を戻す
            if (resolved.matchedText.length < match[0].length) {
                pattern.lastIndex = end;
            }
            matches.push({ start, end, ...resolved });
        }
        return matches;
    }

    // 本文をリンクとそれ以外に分ける（[{ text }] と [{ text, link: { href, isInternal } }]）。
    // 1つのメッセージでリンクにするのは MAX_LINKS_PER_MESSAGE 個まで（それより後ろは文字のまま）
    function split(text) {
        text = text || '';
        const parts = [];
        let cursor = 0;
        let count = 0;
        for (const link of find(text)) {
            if (link.start < cursor) continue;
            if (count >= MAX_LINKS_PER_MESSAGE) break;
            if (link.start > cursor) parts.push({ text: text.slice(cursor, link.start) });
            parts.push({ text: link.matchedText, link: { href: link.href, isInternal: link.isInternal } });
            cursor = link.end;
            count += 1;
        }
        if (cursor < text.length || !parts.length) parts.push({ text: text.slice(cursor) });
        return parts;
    }

    window.BIChatLinks = { MAX_LINKS_PER_MESSAGE, display, find, split };
})();
