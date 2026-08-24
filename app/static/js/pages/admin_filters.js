function withAdminFilterCollapse(component, pageKey) {
    const storageKey = `bi_admin_filter_open_${pageKey}`;
    const originalInit = component.init;
    const originalInitFilters = component.initFilters;

    function ensureFilterCollapseWatch() {
        if (this._adminFilterCollapseReady) {
            return;
        }
        this._adminFilterCollapseReady = true;
        this.$watch("filtersOpen", (val) => {
            sessionStorage.setItem(storageKey, String(val));
        });
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
    component.resetFilters = function resetFilters() {
        if (!this.filterDefaults) {
            return;
        }
        this.draft = { ...this.filterDefaults };
        const defaults = serializedFilters.call(this, this.filterDefaults);
        if (serializedFilters.call(this, this.applied) === defaults) {
            return;
        }
        if (typeof this.submitFilters === "function") {
            this.submitFilters();
            return;
        }
        if (typeof this.applyFilters === "function") {
            this.applyFilters();
        }
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
    return component;
}
