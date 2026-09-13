// /app/static/js/pages/announcements.js
(function () {
    const CATEGORY_KEY = 'bi_announcements_category';
    const VIEW_KEY = 'bi_announcements_view';
    const VALID_CATEGORIES = new Set(['all', '1', '2', '3', '4', '5']);
    const VIEWPORT_MARKER_PX = 96;

    function readSession(key) {
        try {
            return sessionStorage.getItem(key);
        } catch {
            return null;
        }
    }

    function writeSession(key, value) {
        try {
            sessionStorage.setItem(key, value);
        } catch {
            /* quota / private mode */
        }
    }

    function readSavedCategory() {
        const saved = readSession(CATEGORY_KEY);
        return VALID_CATEGORIES.has(saved) ? saved : 'all';
    }

    function readSavedView() {
        try {
            const raw = readSession(VIEW_KEY);
            if (!raw) return null;
            const state = JSON.parse(raw);
            if (!state || typeof state !== 'object') return null;
            const id = state.id != null ? String(state.id) : '';
            return id ? { id } : null;
        } catch {
            return null;
        }
    }

    function isShown(el) {
        if (!el) return false;
        if (el.hasAttribute('hidden')) return false;
        const style = window.getComputedStyle(el);
        return style.display !== 'none' && style.visibility !== 'hidden';
    }

    function announcementItems() {
        return document.querySelectorAll('.announcement-item[id]');
    }

    function getHashTarget() {
        const raw = window.location.hash.slice(1);
        if (!raw) return null;
        let id = raw;
        try {
            id = decodeURIComponent(raw);
        } catch {
            id = raw;
        }
        const byId = document.getElementById(id);
        if (byId) return byId;
        try {
            return document.querySelector(`#${CSS.escape(id)}`);
        } catch {
            return null;
        }
    }

    function announcementFromHash() {
        const target = getHashTarget();
        if (!target) return null;
        return target.closest('.announcement-item') || (target.classList.contains('announcement-item') ? target : null);
    }

    function categoryOfItem(el) {
        if (!el) return null;
        const value = el.dataset.category;
        return VALID_CATEGORIES.has(value) ? value : 'all';
    }

    function viewedAnnouncementId() {
        let current = '';
        for (const el of announcementItems()) {
            if (!isShown(el)) continue;
            const rect = el.getBoundingClientRect();
            if (rect.bottom <= 0) continue;
            if (rect.top <= VIEWPORT_MARKER_PX) {
                current = el.id;
                continue;
            }
            if (!current) current = el.id;
            break;
        }
        return current;
    }

    function disableBrowserScrollRestoration() {
        try {
            history.scrollRestoration = 'manual';
        } catch {
            /* ignore */
        }
    }

    function scrollToAnnouncement(el) {
        if (!el || !isShown(el)) return false;
        el.scrollIntoView({ block: 'start', behavior: 'auto' });
        return true;
    }

    function keepAnnouncementInView(el) {
        if (!scrollToAnnouncement(el)) return;

        const retry = () => scrollToAnnouncement(el);
        window.setTimeout(retry, 120);
        window.setTimeout(retry, 400);

        const images = el.querySelectorAll('img');
        if (!images.length) return;

        let pending = images.length;
        const onDone = () => {
            pending -= 1;
            if (pending <= 0) retry();
        };
        images.forEach((img) => {
            if (img.complete) {
                onDone();
                return;
            }
            img.addEventListener('load', onDone, { once: true });
            img.addEventListener('error', onDone, { once: true });
        });
    }

    window.announcements = function announcements() {
        return {
            categoryFilter: readSavedCategory(),
            _restoring: true,
            _saveTimer: 0,
            _persistWatchReady: false,

            init() {
                disableBrowserScrollRestoration();
                this.categoryFilter = readSavedCategory();
                this.bindPersistence();
                this.queueRestore();
                window.addEventListener('hashchange', () => this.restoreView());
                this.$nextTick(() => {
                    this.$watch('categoryFilter', (value) => {
                        if (this._restoring || !this._persistWatchReady) return;
                        if (!VALID_CATEGORIES.has(value)) return;
                        writeSession(CATEGORY_KEY, value);
                        this.$nextTick(() => this.saveViewState());
                    });
                    this._persistWatchReady = true;
                });
            },

            queueRestore() {
                this.$nextTick(() => {
                    requestAnimationFrame(() => {
                        requestAnimationFrame(() => this.restoreView());
                    });
                });
            },

            bindPersistence() {
                const save = () => this.saveViewState();
                window.addEventListener('scroll', () => this.scheduleSaveViewState(), { passive: true });
                window.addEventListener('pagehide', save);
                document.addEventListener('visibilitychange', () => {
                    if (document.visibilityState === 'hidden') save();
                });
                document.addEventListener('click', (event) => {
                    if (event.target.closest?.('a[href]')) save();
                }, true);
            },

            scheduleSaveViewState() {
                if (this._restoring) return;
                window.clearTimeout(this._saveTimer);
                this._saveTimer = window.setTimeout(() => this.saveViewState(), 120);
            },

            saveViewState() {
                if (this._restoring) return;
                const id = viewedAnnouncementId();
                if (!id) return;
                writeSession(VIEW_KEY, JSON.stringify({ id }));
            },

            applySavedCategory() {
                const savedCategory = readSavedCategory();
                this.categoryFilter = savedCategory;
                const select = this.$el?.querySelector('#categoryFilter');
                if (select && select.value !== savedCategory) {
                    select.value = savedCategory;
                }
                return savedCategory;
            },

            ensureItemVisible(el) {
                if (!el || isShown(el)) return;
                this.categoryFilter = categoryOfItem(el);
            },

            finishRestore() {
                window.setTimeout(() => {
                    this._restoring = false;
                    this.saveViewState();
                }, 450);
            },

            restoreView() {
                this._restoring = true;
                this.applySavedCategory();

                const hashedItem = announcementFromHash();
                if (hashedItem) {
                    this.ensureItemVisible(hashedItem);
                    this.$nextTick(() => {
                        this.ensureItemVisible(hashedItem);
                        keepAnnouncementInView(hashedItem);
                        this.finishRestore();
                    });
                    return;
                }

                const saved = readSavedView();
                this.$nextTick(() => {
                    const el = saved ? document.getElementById(saved.id) : null;
                    if (el) {
                        this.ensureItemVisible(el);
                        this.$nextTick(() => keepAnnouncementInView(el));
                    }
                    this.finishRestore();
                });
            },
        };
    };
})();
