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

    return {
        ...component,
        filtersOpen: sessionStorage.getItem(storageKey) === "true",
        init() {
            if (typeof originalInit === "function") {
                originalInit.call(this);
            }
            ensureFilterCollapseWatch.call(this);
        },
        initFilters(appliedFilters) {
            if (typeof originalInitFilters === "function") {
                originalInitFilters.call(this, appliedFilters);
            }
            ensureFilterCollapseWatch.call(this);
        },
    };
}
