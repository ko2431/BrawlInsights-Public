function adminNotificationCenter(config) {
    const lang = config.lang || "ja";
    const apiUrl = `/${lang}/admin/api/notifications`;

    return withAdminFilterCollapse({
        loading: false,
        requestSeq: 0,
        listEpoch: 0,
        errorMessage: "",
        items: [],
        totalCount: 0,
        hasMore: false,
        draft: {
            level: "all",
            category: "all",
            text: "",
            createdAfter: "",
            createdBefore: "",
        },
        applied: {
            level: "all",
            category: "all",
            text: "",
            createdAfter: "",
            createdBefore: "",
        },
        filterDefaults: {
            level: "all",
            category: "all",
            text: "",
            createdAfter: "",
            createdBefore: "",
        },
        init() {
            this.fetchPage({ reset: true });
        },
        normalizeFilters(filters) {
            return {
                level: filters.level || "all",
                category: filters.category || "all",
                text: (filters.text || "").trim(),
                createdAfter: (filters.createdAfter || "").trim(),
                createdBefore: (filters.createdBefore || "").trim(),
            };
        },
        get canSubmitFilters() {
            return JSON.stringify(this.normalizeFilters(this.draft))
                !== JSON.stringify(this.normalizeFilters(this.applied));
        },
        levelBadgeColor(level) {
            if (level >= 30) return "red";
            if (level >= 20) return "yellow";
            return "gray";
        },
        buildQuery(beforeId) {
            const params = new URLSearchParams();
            const filter = this.applied;
            if (filter.level && filter.level !== "all") params.set("level", filter.level);
            if (filter.category && filter.category !== "all") params.set("category", filter.category);
            if ((filter.text || "").trim()) params.set("text", filter.text.trim());
            if (filter.createdAfter) params.set("created_after", filter.createdAfter);
            if (filter.createdBefore) params.set("created_before", filter.createdBefore);
            if (beforeId) params.set("before_id", String(beforeId));
            const query = params.toString();
            return query ? `${apiUrl}?${query}` : apiUrl;
        },
        async fetchPage({ reset }) {
            if (this.loading && !reset) return;
            const requestSeq = ++this.requestSeq;
            this.loading = true;
            this.errorMessage = "";
            if (reset) {
                this.listEpoch += 1;
                this.items = [];
                this.hasMore = false;
            }
            const beforeId = reset || this.items.length === 0 ? null : this.items[this.items.length - 1].id;
            try {
                const response = await fetch(this.buildQuery(beforeId), {
                    headers: { "Accept": "application/json" },
                });
                if (!response.ok) {
                    throw new Error("failed");
                }
                const data = await response.json();
                if (requestSeq !== this.requestSeq) return;
                const nextItems = Array.isArray(data.items) ? data.items : [];
                if (reset) {
                    this.items = nextItems;
                    this.totalCount = Number(data.total_count || 0);
                } else {
                    this.items = this.items.concat(nextItems);
                }
                this.hasMore = Boolean(data.has_more);
            } catch (error) {
                if (requestSeq !== this.requestSeq) return;
                this.errorMessage = "通知の取得に失敗しました。";
                if (reset) {
                    this.items = [];
                    this.totalCount = 0;
                    this.hasMore = false;
                }
            } finally {
                if (requestSeq === this.requestSeq) {
                    this.loading = false;
                }
            }
        },
        applyFilters() {
            if (!this.canSubmitFilters) {
                return;
            }
            this.applied = { ...this.draft };
            this.fetchPage({ reset: true });
        },
        isAppliedLevel(level) {
            return String(this.applied.level) === String(level);
        },
        isAppliedCategory(category) {
            return String(this.applied.category) === String(category);
        },
        filterByLevel(level) {
            const next = String(level);
            if (this.isAppliedLevel(next)) return;
            this.applied = { ...this.applied, level: next };
            this.draft.level = next;
            this.fetchPage({ reset: true });
        },
        filterByCategory(category) {
            const next = String(category);
            if (this.isAppliedCategory(next)) return;
            this.applied = { ...this.applied, category: next };
            this.draft.category = next;
            this.fetchPage({ reset: true });
        },
        loadMore() {
            if (!this.hasMore || this.loading) return;
            this.fetchPage({ reset: false });
        },
    }, "notifications");
}
