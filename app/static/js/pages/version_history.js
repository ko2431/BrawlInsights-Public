// /app/static/js/pages/version_history.js
// バージョン履歴: すべて開く／閉じる、URL の #v15-3-0 などで該当バージョンを開いて移動する
(function () {
    const toggleButton = document.getElementById('vhToggleAll');
    const groups = Array.from(document.querySelectorAll('.vh-group'));
    if (!toggleButton || groups.length === 0) return;

    // グループと「残りを表示」をまとめて扱う
    const allDetails = () => Array.from(document.querySelectorAll('.vh-group, .vh-more'));

    function updateToggleLabel() {
        const allOpen = groups.every((group) => group.open);
        toggleButton.textContent = allOpen ? toggleButton.dataset.labelClose : toggleButton.dataset.labelOpen;
    }

    toggleButton.addEventListener('click', () => {
        const open = !groups.every((group) => group.open);
        allDetails().forEach((details) => { details.open = open; });
        updateToggleLabel();
    });
    groups.forEach((group) => group.addEventListener('toggle', updateToggleLabel));

    function openHashTarget() {
        const id = decodeURIComponent(location.hash.slice(1));
        if (!id) return;
        const target = document.getElementById(id);
        if (!target || !target.closest('.vh-page')) return;
        const group = target.closest('.vh-group');
        if (group && !group.open) group.open = true;
        target.scrollIntoView({ block: 'start' });
    }

    window.addEventListener('hashchange', openHashTarget);
    openHashTarget();
    updateToggleLabel();
})();
