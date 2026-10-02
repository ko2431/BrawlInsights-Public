/**
 * ツール等で切り替えて使う、自分のプレイヤーアカウント(メイン・サブ)の選択状態。
 * 選択は端末(localStorage)に保存し、ツール間で共有する。登録から外れたアカウントはメインに戻す。
 */
(function () {
    'use strict';

    const STORAGE_KEY = 'bi_selected_player_account';

    function load(options) {
        const list = Array.isArray(options) ? options : [];
        if (list.length === 0) return '';
        try {
            const stored = localStorage.getItem(STORAGE_KEY);
            if (stored && list.some(option => option.tag === stored)) return stored;
        } catch (e) {
            // localStorage が使えない場合はメインを使う
        }
        return list[0].tag;
    }

    function save(tag) {
        try {
            if (tag) localStorage.setItem(STORAGE_KEY, tag);
        } catch (e) {
            // 保存できなくても動作には影響しない
        }
    }

    window.BIPlayerAccount = { load, save };
})();
