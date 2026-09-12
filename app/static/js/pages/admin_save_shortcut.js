(function initAdminSaveShortcut() {
    if (window.__adminSaveShortcutBound) {
        return;
    }
    window.__adminSaveShortcutBound = true;

    function isAppleKeyboardPlatform() {
        const platform = navigator.platform || "";
        const userAgent = navigator.userAgent || "";
        return /Mac|iPod|iPhone|iPad/.test(platform) || /Mac|iPhone|iPad|iPod/.test(userAgent);
    }

    function shortcutLabel() {
        return isAppleKeyboardPlatform() ? "⌘S" : "Ctrl+S";
    }

    function isVisible(el) {
        return el.getClientRects().length > 0;
    }

    function fillLabels() {
        const label = shortcutLabel();
        document.querySelectorAll(".admin-save-shortcut").forEach((el) => {
            el.textContent = label;
        });
    }

    function onKeydown(event) {
        if (event.isComposing || event.code !== "KeyS") {
            return;
        }
        const isCmdOrCtrl = event.metaKey || event.ctrlKey;
        if (!isCmdOrCtrl || event.altKey || event.shiftKey) {
            return;
        }
        const visibleButtons = [...document.querySelectorAll(".admin-save-button")].filter(isVisible);
        if (visibleButtons.length === 0) {
            return;
        }
        event.preventDefault();
        const enabled = visibleButtons.find((el) => !el.disabled);
        if (enabled) {
            enabled.click();
        }
    }

    fillLabels();
    document.addEventListener("alpine:initialized", fillLabels);
    window.addEventListener("keydown", onKeydown);
})();
