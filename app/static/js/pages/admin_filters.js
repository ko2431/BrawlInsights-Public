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

    // スプレッドすると getter（canSubmitFilters など）が一度評価された値になり、
    // 以降リアクティブに更新されなくなるため、元オブジェクトを直接拡張する。
    component.filtersOpen = sessionStorage.getItem(storageKey) === "true";
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
