/**
 * URLの検出とリンク化の規則。掲示板のチャット・大会のチャット・大会のお知らせのリンク化と、
 * 掲示板のひとこと/コメント禁止判定で共用する（片方だけ規則を変えない）。
 */
(function (global) {
    'use strict';

    const MAX_URL_LENGTH = 2048;
    const MAX_RELATIVE_LENGTH = 200;

    const URL_CANDIDATE_RE = /https?:\/\/[^\s<]+|www\.[^\s<]+|\/(?:ja|en)\/[A-Za-z0-9\-._\/?=&%#+]+|\b(?:(?:[a-z0-9-]+\.)*)(?:brawlinsights\.com|brawlstars\.com|x\.com|twitter\.com|discord\.gg|discord\.com|youtube\.com|youtu\.be|tiktok\.com)(?:\/[A-Za-z0-9\-._\/?=&%#+]*)?/gi;

    function stripTrailingUrlPunctuation(rawUrl) {
        let url = String(rawUrl || '');
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

    function isAllowedBareHostname(hostname) {
        const host = String(hostname || '').toLowerCase().replace(/\.$/, '');
        if (!host) return false;
        if (isInternalHostname(host)) return true;
        if (hostMatchesDomain(host, 'brawlstars.com')) return true;
        if (hostMatchesDomain(host, 'x.com')) return true;
        if (hostMatchesDomain(host, 'twitter.com')) return true;
        if (hostMatchesDomain(host, 'discord.gg')) return true;
        if (hostMatchesDomain(host, 'discord.com')) return true;
        if (hostMatchesDomain(host, 'youtube.com')) return true;
        if (host === 'youtu.be') return true;
        if (hostMatchesDomain(host, 'tiktok.com')) return true;
        return false;
    }

    function isValidRelativeSitePath(path) {
        if (!/^\/(?:ja|en)\//.test(path)) return false;
        if (path.length > MAX_RELATIVE_LENGTH) return false;
        return /^\/(?:ja|en)\/[A-Za-z0-9\-._\/?=&%#+]*$/.test(path);
    }

    function resolveLinkFromMatch(matchedText, baseOrigin) {
        const trimmed = stripTrailingUrlPunctuation(matchedText);
        if (!trimmed) return null;

        if (/^\/(?:ja|en)\//.test(trimmed)) {
            if (!isValidRelativeSitePath(trimmed)) return null;
            return {
                matchedText: trimmed,
                href: trimmed,
                isInternal: true,
            };
        }

        let href = trimmed;
        if (/^www\./i.test(trimmed)) {
            href = `https://${trimmed}`;
        } else if (!/^https?:\/\//i.test(trimmed)) {
            href = `https://${trimmed}`;
        }

        if (href.length > MAX_URL_LENGTH) return null;

        let parsed;
        try {
            parsed = new URL(href, baseOrigin || 'https://brawlinsights.com');
        } catch (e) {
            return null;
        }

        if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
            return null;
        }

        if (!/^https?:\/\//i.test(trimmed) && !/^www\./i.test(trimmed)) {
            if (!isAllowedBareHostname(parsed.hostname)) return null;
        }

        const isInternal = isInternalHostname(parsed.hostname);
        return {
            matchedText: trimmed,
            href: isInternal && /^\/(?:ja|en)\//.test(parsed.pathname + parsed.search + parsed.hash)
                ? `${parsed.pathname}${parsed.search}${parsed.hash}`
                : parsed.href,
            isInternal,
        };
    }

    function findLinkMatches(text, baseOrigin) {
        const source = text == null ? '' : String(text);
        const pattern = new RegExp(URL_CANDIDATE_RE.source, URL_CANDIDATE_RE.flags);
        const matches = [];
        let match;
        while ((match = pattern.exec(source)) !== null) {
            const resolved = resolveLinkFromMatch(match[0], baseOrigin);
            if (!resolved) continue;
            const start = match.index;
            const end = start + resolved.matchedText.length;
            if (resolved.matchedText.length < match[0].length) {
                pattern.lastIndex = end;
            }
            matches.push({
                start,
                end,
                ...resolved,
            });
        }
        return matches;
    }

    function containsUrl(text, baseOrigin) {
        return findLinkMatches(text, baseOrigin).length > 0;
    }

    // 1つのメッセージでリンクにする数と、リンクの表示の長さ（掲示板のチャット・大会のチャット・お知らせで共通）
    const MAX_LINKS_PER_MESSAGE = 10;
    const DISPLAY_MAX_LENGTH = 70;

    function display(text) {
        const source = String(text || '');
        return source.length <= DISPLAY_MAX_LENGTH ? source : `${source.slice(0, DISPLAY_MAX_LENGTH)}...`;
    }

    // 本文をリンクとそれ以外に分ける（[{ text }] と [{ text, link: { href, isInternal } }]）。
    // リンクにするのは MAX_LINKS_PER_MESSAGE 個まで（それより後ろは文字のまま）
    function split(text, baseOrigin) {
        const source = text == null ? '' : String(text);
        const parts = [];
        let cursor = 0;
        let count = 0;
        for (const link of findLinkMatches(source, baseOrigin)) {
            if (link.start < cursor) continue;
            if (count >= MAX_LINKS_PER_MESSAGE) break;
            if (link.start > cursor) parts.push({ text: source.slice(cursor, link.start) });
            parts.push({ text: link.matchedText, link: { href: link.href, isInternal: link.isInternal } });
            cursor = link.end;
            count += 1;
        }
        if (cursor < source.length || !parts.length) parts.push({ text: source.slice(cursor) });
        return parts;
    }

    global.BrawlInsightsUrlDetect = {
        MAX_LINKS_PER_MESSAGE,
        findLinkMatches,
        containsUrl,
        resolveLinkFromMatch,
        display,
        split,
    };
})(typeof window !== 'undefined' ? window : globalThis);
