// 掲示板の上部の帯（掲示板企画）。ページの後から読み込んで、種類のタブの下に出す（board_campaign_plan 10.1）
// - 畳んだ状態はすべての掲示板で共通にして、端末に記憶する（bi_board_campaign_collapsed）
// - 入れる前に畳んだ状態に合わせておく（Alpine が動くまでの一瞬だけ開いて見えないように）
(function () {
    var STORAGE_KEY = 'bi_board_campaign_collapsed';

    function readCollapsed() {
        try {
            return localStorage.getItem(STORAGE_KEY) === '1';
        } catch (e) {
            return false;
        }
    }

    window.boardCampaignBanner = function () {
        return {
            collapsed: readCollapsed(),
            toggle() {
                this.collapsed = !this.collapsed;
                try {
                    localStorage.setItem(STORAGE_KEY, this.collapsed ? '1' : '0');
                } catch (e) {}
            },
        };
    };

    var slot = document.querySelector('[data-board-campaign-banner]');
    if (!slot || slot.dataset.loaded) return;
    slot.dataset.loaded = '1';
    fetch(slot.dataset.boardCampaignBanner, { credentials: 'same-origin' })
        .then(function (res) { return res.status === 200 ? res.text() : ''; })
        .then(function (html) {
            if (!html.trim()) return;
            var tpl = document.createElement('template');
            tpl.innerHTML = html.trim();
            if (readCollapsed()) {
                tpl.content.querySelectorAll('[data-bcb-open]').forEach(function (el) { el.setAttribute('x-cloak', ''); });
                tpl.content.querySelectorAll('[data-bcb-closed]').forEach(function (el) { el.removeAttribute('x-cloak'); });
                var root = tpl.content.querySelector('.bcb');
                if (root) root.classList.add('bcb--collapsed');
            }
            slot.appendChild(tpl.content);
        })
        .catch(function () {});
})();
