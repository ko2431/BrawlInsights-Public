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
