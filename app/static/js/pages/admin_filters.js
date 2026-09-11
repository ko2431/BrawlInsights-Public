function withAdminFilterCollapse(component, pageKey) {
    const storageKey = `bi_admin_filter_open_${pageKey}`;
    const originalInit = component.init;
    const originalInitFilters = component.initFilters;

    function isAppleKeyboardPlatform() {
        const platform = navigator.platform || "";
        const userAgent = navigator.userAgent || "";
        return /Mac|iPod|iPhone|iPad/.test(platform) || /Mac|iPhone|iPad|iPod/.test(userAgent);
    }

    function ensureFilterCollapseWatch() {
        if (this._adminFilterCollapseReady) {
            return;
        }
        this._adminFilterCollapseReady = true;
        this.$watch("filtersOpen", (val) => {
            sessionStorage.setItem(storageKey, String(val));
        });
        this._onAdminFilterKeydown = (event) => {
            if (event.isComposing || event.key !== "Enter") {
                return;
            }
            const isCmdOrCtrl = event.metaKey || event.ctrlKey;
            if (!isCmdOrCtrl || event.altKey) {
                return;
            }
            if (event.shiftKey) {
                if (!this.canResetFilters) {
                    return;
                }
                event.preventDefault();
                this.resetFilters();
                return;
            }
            if (!this.canSubmitFilters) {
                return;
            }
            event.preventDefault();
            this.submitAdminFilters();
        };
        window.addEventListener("keydown", this._onAdminFilterKeydown);
    }

    function serializedFilters(filters) {
        if (!filters) {
            return "";
        }
        if (typeof this.normalizeFilters === "function") {
            return JSON.stringify(this.normalizeFilters(filters));
        }
        return JSON.stringify(filters);
    }

    // スプレッドすると getter（canSubmitFilters など）が一度評価された値になり、
    // 以降リアクティブに更新されなくなるため、元オブジェクトを直接拡張する。
    component.filtersOpen = sessionStorage.getItem(storageKey) === "true";
    component.isAppleFilterShortcut = isAppleKeyboardPlatform();
    Object.defineProperty(component, "filterSubmitShortcutLabel", {
        enumerable: true,
        configurable: true,
        get() {
            return this.isAppleFilterShortcut ? "⌘Enter" : "Ctrl+Enter";
        },
    });
    Object.defineProperty(component, "filterResetShortcutLabel", {
        enumerable: true,
        configurable: true,
        get() {
            return this.isAppleFilterShortcut ? "⌘⇧Enter" : "Ctrl+Shift+Enter";
        },
    });
    Object.defineProperty(component, "canResetFilters", {
        enumerable: true,
        configurable: true,
        get() {
            if (!this.filterDefaults) {
                return false;
            }
            const defaults = serializedFilters.call(this, this.filterDefaults);
            return serializedFilters.call(this, this.draft) !== defaults
                || serializedFilters.call(this, this.applied) !== defaults;
        },
    });
    component.submitAdminFilters = function submitAdminFilters() {
        if (typeof this.submitFilters === "function") {
            this.submitFilters();
            return;
        }
        if (typeof this.applyFilters === "function") {
            this.applyFilters();
        }
    };
    component.resetFilters = function resetFilters() {
        if (!this.filterDefaults) {
            return;
        }
        this.draft = { ...this.filterDefaults };
        const defaults = serializedFilters.call(this, this.filterDefaults);
        if (serializedFilters.call(this, this.applied) === defaults) {
            return;
        }
        this.submitAdminFilters();
    };
    component.init = function init() {
        if (typeof originalInit === "function") {
            originalInit.call(this);
        }
        ensureFilterCollapseWatch.call(this);
    };
    component.initFilters = function initFilters(appliedFilters) {
        if (typeof originalInitFilters === "function") {
            originalInitFilters.call(this, appliedFilters);
        }
        ensureFilterCollapseWatch.call(this);
    };
    const originalDestroy = component.destroy;
    component.destroy = function destroy() {
        if (this._onAdminFilterKeydown) {
            window.removeEventListener("keydown", this._onAdminFilterKeydown);
            this._onAdminFilterKeydown = null;
        }
        if (typeof originalDestroy === "function") {
            originalDestroy.call(this);
        }
    };
    return component;
}
