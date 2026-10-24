// /app/static/js/mentions.js
// チャットのメンションの画面側の規則（掲示板のチャット・大会のチャットで共通）。window.BIMentions として使う。
// 成立はサーバーが本文の「@名前」で決める（app/services/mention_rules.py の pick_mentions）。
// 表示の色付け（split）はそれと同じ規則にしている。片方だけ変えない。
(function () {
    'use strict';
    if (window.BIMentions) return;

    // 候補の検索は、ひらがな・カタカナ、大文字・小文字、全角・半角を区別しない
    function normalize(str) {
        return (str || '').normalize('NFKC').toLowerCase()
            .replace(/[\u3041-\u3096]/g, (ch) => String.fromCharCode(ch.charCodeAt(0) + 0x60));
    }

    // カーソルの直前が「@」（全角の「＠」も）と名前の途中なら、「@」の位置・カーソルの位置・打った文字を返す（なければ null）。
    // 日本語入力の変換中（未確定）でも出すため、入力欄の今の値を見る。iOS は変換中の文字を選択範囲にし、
    // selectionStart がその手前を指すので、選択の終わりをカーソルとみなす
    function queryAt(el) {
        if (!el) return null;
        const caret = el.selectionEnd;
        const match = /(?:^|\s)[@＠]([^\s@＠]{0,20})$/.exec(el.value.slice(0, caret));
        if (!match) return null;
        return { query: match[1], start: caret - match[1].length - 1, end: caret };
    }

    // 本文を「@名前」とそれ以外に分ける（[{ text }] と [{ text, user }]）。mentions は保存したメンション（{ id, name, ... }）。
    // サーバーの pick_mentions と同じく、全角の「＠」も、英字の大文字・小文字の違いも、同じ位置ではいちばん長い名前、直前が英数字の「@」は除く
    function split(text, mentions) {
        text = text || '';
        if (!text || !mentions || !mentions.length) return [{ text }];
        const byName = new Map(mentions.filter((x) => x && x.name).map((x) => [x.name.toLowerCase(), x]));
        if (!byName.size) return [{ text }];
        const names = [...byName.keys()].sort((a, b) => b.length - a.length);
        const escape = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        const pattern = new RegExp(`[@＠](${names.map(escape).join('|')})`, 'gi');
        const parts = [];
        let last = 0;
        for (const hit of text.matchAll(pattern)) {
            if (hit.index > 0 && /[A-Za-z0-9]/.test(text[hit.index - 1])) continue;
            const user = byName.get(hit[1].toLowerCase());
            // 大会のチャットの @everyone は、すぐ後に英数字・「_」が続くもの（「@everyones」）を除く（mention_rules.mentions_everyone）
            if (user.everyone && /[A-Za-z0-9_]/.test(text[hit.index + hit[0].length] || '')) continue;
            if (hit.index > last) parts.push({ text: text.slice(last, hit.index) });
            parts.push({ text: hit[0], user });
            last = hit.index + hit[0].length;
        }
        if (last < text.length) parts.push({ text: text.slice(last) });
        return parts;
    }

    window.BIMentions = { normalize, queryAt, split };
})();
