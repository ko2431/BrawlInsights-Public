/* 統計データ・ツールのハブサブタブ。リロードなしで切り替え、URLの sub とストレージに選択を残す。 */
document.addEventListener('alpine:init', () => {
    Alpine.data('hubSubtabs', (config) => {
        const ids = Array.isArray(config.ids) ? config.ids : [];
        const storageKey = config.storageKey;
        const storage = config.platform === 'web' ? window.sessionStorage : window.localStorage;

        const readSaved = () => {
            try {
                const saved = storage.getItem(storageKey);
                return ids.includes(saved) ? saved : null;
            } catch (e) {
                return null;
            }
        };

        const fromUrl = () => {
            const sub = new URLSearchParams(window.location.search).get('sub');
            return ids.includes(sub) ? sub : null;
        };

        const initial = fromUrl() || readSaved() || ids[0];

        return {
            activeSubTab: initial,
            init() {
                this.persist(this.activeSubTab);
            },
            handleKeydown(e) {
                if (e.metaKey || e.ctrlKey) return;
                if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
                const el = document.activeElement;
                if (el && (el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName))) return;
                e.preventDefault();
                const index = Math.max(0, ids.indexOf(this.activeSubTab));
                const nextIndex = e.key === 'ArrowRight'
                    ? (index + 1) % ids.length
                    : (index - 1 + ids.length) % ids.length;
                this.setSubTab(ids[nextIndex]);
            },
            setSubTab(id) {
                if (!ids.includes(id) || id === this.activeSubTab) return;
                this.activeSubTab = id;
                this.persist(id);
                this.$nextTick(() => {
                    const nav = this.$root.querySelector('.hub-subtabs');
                    if (!nav) return;
                    if (nav.getBoundingClientRect().top < 0) {
                        nav.scrollIntoView({ block: 'start', behavior: 'auto' });
                    }
                });
            },
            persist(id) {
                try {
                    storage.setItem(storageKey, id);
                } catch (e) {
                    // ストレージが使えない場合はURLだけ更新する
                }
                const url = new URL(window.location.href);
                if (url.searchParams.get('sub') === id) return;
                url.searchParams.set('sub', id);
                history.replaceState(null, '', url.pathname + url.search + url.hash);
            },
        };
    });
});
