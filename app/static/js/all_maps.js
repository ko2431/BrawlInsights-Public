// /app/static/js/all_maps.js
// マップ一覧: モード絞り込み・検索・マップ周期との連携を、リロードなしでクライアント側で行う。

(function () {
    const RENDER_CHUNK = 120; // 一度に描画するカード数。スクロールに応じて追加する
    const LOAD_MORE_MARGIN = 800; // 画面下端からこの距離(px)に近づいたら追加描画する
    const MAP_ID_BASE = 15000000;
    const DAYS_JA = { Sun: '日', Mon: '月', Tue: '火', Wed: '水', Thu: '木', Fri: '金', Sat: '土' };
    const MONTHS_EN = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const SECTION_KEYS = ['rotation', 'offRotation', 'disabled'];

    // ひらがなとカタカナ、大文字と小文字、全角と半角を同一視する
    const hiraToKana = (str) => str.replace(/[ぁ-ゖ]/g, (match) =>
        String.fromCharCode(match.charCodeAt(0) + 0x60)
    );
    const normalizeText = (str) => hiraToKana((str || '').normalize('NFKC').toLowerCase());

    function readData() {
        const el = document.getElementById('allMapsData');
        if (!el) return { modes: [], maps: [], rotation: [] };
        try {
            return JSON.parse(el.textContent || '{}');
        } catch (e) {
            console.error('マップ一覧のデータを読み込めませんでした', e);
            return { modes: [], maps: [], rotation: [] };
        }
    }

    function createZonedFormatter(timeZone) {
        const formatter = new Intl.DateTimeFormat('en-US', {
            timeZone,
            year: 'numeric', month: 'numeric', day: 'numeric',
            hour: 'numeric', minute: 'numeric', hourCycle: 'h23', weekday: 'short',
        });
        return (date) => {
            const parts = formatter.formatToParts(date);
            const get = (type) => parts.find((part) => part.type === type)?.value;
            return {
                year: Number(get('year')),
                month: Number(get('month')),
                day: Number(get('day')),
                hour: Number(get('hour')),
                minute: Number(get('minute')),
                weekday: get('weekday'),
            };
        };
    }

    // マップ周期ページの周期表と同じ計算で、マップごとに最も早い出現時刻を求める
    function buildRotationInfo(slots, nowTime) {
        const info = new Map();
        (slots || []).forEach((slot) => {
            const maps = slot.maps || [];
            const originIso = slot.latestStart || slot.anchorStart;
            if (!maps.length || !originIso) return;
            const origin = new Date(originIso).getTime();
            if (Number.isNaN(origin)) return;
            const periodMs = (slot.durationMinutes || 1440) * 60 * 1000;
            const steps = Math.max(0, Math.floor((nowTime - origin) / periodMs));
            const length = maps.length;
            const predicted = new Set(slot.predicted || []);
            for (let i = 0; i < length; i++) {
                const index = ((slot.latestIndex || 0) + steps + i) % length;
                // 現在枠は周期に無いマップでも観測値を優先する(同期が遅れて観測が古い場合は周期から予測)
                const observed = i === 0 && steps === 0 && slot.currentMapId;
                const mapId = observed ? slot.currentMapId : maps[index];
                if (!mapId) continue;
                const time = origin + (steps + i) * periodMs;
                const current = info.get(mapId);
                if (!current || time < current.time || (time === current.time && slot.order < current.order)) {
                    info.set(mapId, { time, order: slot.order, active: i === 0, predicted: !observed && predicted.has(index) });
                }
            }
        });
        return info;
    }

    window.allMapsApp = function allMapsApp(config) {
        const isJa = config.lang === 'ja';
        const data = readData();
        const staticPrefix = (config.staticPrefix || '/static').replace(/\/+$/, '');
        const zonedParts = createZonedFormatter(isJa ? 'Asia/Tokyo' : 'UTC');

        const modesById = new Map();
        (data.modes || []).forEach((mode) => modesById.set(mode.id, Object.freeze(mode)));

        // Alpine に深くリアクティブ化させないよう、マップデータは凍結してクロージャに保持する
        const maps = Object.freeze((data.maps || []).map((map) => Object.freeze({
            ...map,
            searchKey: normalizeText(map.ja) + '\n' + normalizeText(map.en),
        })));

        const nowTime = Date.now();
        const rotationInfo = buildRotationInfo(data.rotation, nowTime);
        const today = zonedParts(new Date(nowTime));
        const tomorrow = zonedParts(new Date(nowTime + 24 * 60 * 60 * 1000));
        const isSameDay = (a, b) => a.year === b.year && a.month === b.month && a.day === b.day;

        function formatAppearance(entry) {
            if (entry.active) return isJa ? '現在出現中' : 'Active';
            const parts = zonedParts(new Date(entry.time));
            let timeText;
            if (isJa) {
                timeText = `${parts.hour}:${String(parts.minute).padStart(2, '0')}〜`;
            } else {
                const hour12 = parts.hour % 12 || 12;
                const suffix = parts.hour < 12 ? 'AM' : 'PM';
                timeText = parts.minute ? `${hour12}:${String(parts.minute).padStart(2, '0')}${suffix}-` : `${hour12}${suffix}-`;
            }
            if (isSameDay(parts, today)) return isJa ? `今日 ${timeText}` : `Today ${timeText}`;
            if (isSameDay(parts, tomorrow)) return isJa ? `明日 ${timeText}` : `Tomorrow ${timeText}`;
            if (isJa) return `${parts.month}/${parts.day}(${DAYS_JA[parts.weekday] || ''}) ${timeText}`;
            return `${MONTHS_EN[parts.month - 1]} ${parts.day} ${timeText}`;
        }

        // IntersectionObserver が使えない古い環境では、段階的に描画せず全件描画する
        const chunkSize = 'IntersectionObserver' in window ? RENDER_CHUNK : Infinity;

        // カードごとの出現情報。重複としてまとめたID(alt)や、同名ソロマップ(link)の周期も含めて最も早いものを使う
        const cardRotation = new Map();
        const badges = new Map();
        for (const map of maps) {
            let best = null;
            for (const mapId of [map.id, ...(map.alt || []), map.link]) {
                const entry = mapId ? rotationInfo.get(mapId) : null;
                if (entry && (!best || entry.time < best.time || (entry.time === best.time && entry.order < best.order))) {
                    best = entry;
                }
            }
            if (best) {
                cardRotation.set(map.id, best);
                badges.set(map.id, formatAppearance(best));
            }
        }

        function modeIconSrc(mode) {
            const path = mode && mode.icons && mode.icons[0];
            return path ? staticPrefix + path : '';
        }

        return {
            isJa,
            modeId: config.initialMode || null,
            query: '',
            renderLimit: chunkSize,
            view: Object.freeze({ rotation: [], offRotation: [], disabled: [], total: 0 }),
            collapsed: { rotation: false, offRotation: false, disabled: false },
            // モードごとのマップ画像の縦横比(読み込んだ画像から求める)。モード選択時にカードの高さを合わせる
            modeAspects: {},

            init() {
                this.refresh();
                this.$nextTick(() => {
                    const sentinel = this.$refs.sentinel;
                    if (!sentinel || chunkSize === Infinity) return;
                    const observer = new IntersectionObserver((entries) => {
                        if (entries.some((entry) => entry.isIntersecting)) this.loadMore();
                    }, { rootMargin: `0px 0px ${LOAD_MORE_MARGIN}px 0px` });
                    observer.observe(sentinel);
                    this.loadMore();
                });
            },

            // 絞り込み結果を作り直す。モード・検索語が変わった時だけ呼ぶ
            refresh() {
                const query = normalizeText(this.query.trim());
                // 隠し仕様: 数字だけならマップID(全体、または15000000を引いた値)の完全一致でも探す
                const idQuery = /^\d+$/.test(query) ? Number(query) : null;
                const result = { rotation: [], offRotation: [], disabled: [] };
                for (const map of maps) {
                    if (this.modeId && map.mode !== this.modeId) continue;
                    if (query) {
                        const idMatched = idQuery !== null
                            && [map.id, ...(map.alt || [])].some((id) => id === idQuery || id - MAP_ID_BASE === idQuery);
                        if (!idMatched && !map.searchKey.includes(query)) continue;
                    }
                    if (cardRotation.has(map.id)) result.rotation.push(map);
                    else if (map.off) result.disabled.push(map);
                    else result.offRotation.push(map);
                }
                result.rotation.sort((a, b) => {
                    const ra = cardRotation.get(a.id);
                    const rb = cardRotation.get(b.id);
                    return (ra.time - rb.time) || (ra.order - rb.order) || (a.id - b.id);
                });
                result.total = result.rotation.length + result.offRotation.length + result.disabled.length;
                this.view = Object.freeze(result);
                this.renderLimit = chunkSize;
                this.$nextTick(() => this.loadMore());
            },

            // 番兵が画面に近い間は描画数を増やす(増やした後も近ければ続けて増やす)
            loadMore() {
                const sentinel = this.$refs.sentinel;
                if (!sentinel || this.renderLimit >= this.view.total) return;
                const rect = sentinel.getBoundingClientRect();
                if (rect.top > window.innerHeight + LOAD_MORE_MARGIN) return;
                this.renderLimit += chunkSize;
                this.$nextTick(() => this.loadMore());
            },

            setMode(value) {
                const parsed = Number(value);
                this.modeId = Number.isInteger(parsed) && modesById.has(parsed) ? parsed : null;
                // マップ個別ページから戻った時に選択を復元できるよう、URLにも反映する
                try {
                    const url = new URL(window.location.href);
                    if (this.modeId) url.searchParams.set('mode', String(this.modeId));
                    else url.searchParams.delete('mode');
                    window.history.replaceState(window.history.state, '', url.toString());
                } catch (e) {
                    // URLの更新に失敗しても表示には影響しない
                }
                this.refresh();
            },

            setQuery(value) {
                this.query = value || '';
                this.refresh();
            },

            // 各セクションの描画対象。開いているセクションをまたいで、上から順に renderLimit 件まで描画する
            visibleItems(key) {
                if (this.collapsed[key]) return [];
                let offset = 0;
                for (const sectionKey of SECTION_KEYS) {
                    if (sectionKey === key) break;
                    if (!this.collapsed[sectionKey]) offset += this.view[sectionKey].length;
                }
                const remaining = this.renderLimit - offset;
                return remaining > 0 ? this.view[key].slice(0, remaining) : [];
            },

            toggleSection(key) {
                this.collapsed[key] = !this.collapsed[key];
                this.$nextTick(() => this.loadMore());
            },

            // ⌘F / Ctrl+F で検索欄にカーソルを移す
            handleKeydown(event) {
                if (!(event.metaKey || event.ctrlKey) || event.altKey || event.shiftKey) return;
                if ((event.key || '').toLowerCase() !== 'f') return;
                const input = this.$refs.searchInput;
                if (!input) return;
                event.preventDefault();
                input.focus();
                input.select();
            },

            onMapImageLoad(event, map) {
                const img = event.target;
                if (map.mode in this.modeAspects || !img.naturalWidth || !img.naturalHeight) return;
                this.modeAspects[map.mode] = `${img.naturalWidth} / ${img.naturalHeight}`;
            },

            // 「すべて」では他モードと高さを揃えるため既定の縦横比のまま。モード選択時だけそのモードの比率にする
            gridStyle() {
                const aspect = this.modeId ? this.modeAspects[this.modeId] : null;
                return aspect ? `--map-card-aspect: ${aspect}` : '';
            },

            sectionCount(key) {
                return this.view[key].length;
            },

            get selectedMode() {
                return this.modeId ? modesById.get(this.modeId) : null;
            },

            headingName() {
                const mode = this.selectedMode;
                if (!mode) return isJa ? 'すべて' : 'All';
                return (isJa ? mode.ja : mode.en) || mode.en || mode.ja || String(mode.id);
            },

            countText(count) {
                const text = Number(count).toLocaleString('en-US');
                return isJa ? `${text}マップ` : `${text} map${count === 1 ? '' : 's'}`;
            },

            mapName(map) {
                return (isJa ? map.ja : map.en) || map.en || map.ja || String(map.id);
            },

            mapLink(map) {
                return `/${config.lang}/tools/map/${map.id}?tab=tools&from=all_maps`;
            },

            mapImage(map) {
                return `https://cdn.bsinfox.com/maps/${map.id}.png`;
            },

            onMapImageError(event, map) {
                const img = event.target;
                if (img.dataset.fallbackApplied === '1') return;
                img.dataset.fallbackApplied = '1';
                img.src = `https://cdn.brawlify.com/maps/regular/${map.id}.png`;
            },

            cardClass(map) {
                const mode = modesById.get(map.mode);
                return mode && mode.theme ? `map-card--${mode.theme}` : '';
            },

            badge(map) {
                return badges.get(map.id) || '';
            },

            // 出現日時が確定ではなく予想の場合
            isPredicted(map) {
                const entry = cardRotation.get(map.id);
                return !!(entry && entry.predicted);
            },

            isActive(map) {
                const entry = cardRotation.get(map.id);
                return !!(entry && entry.active);
            },

            modeIconSrc(modeOrId) {
                const mode = typeof modeOrId === 'object' ? modeOrId : modesById.get(modeOrId);
                return modeIconSrc(mode);
            },

            modeIconFallbacks(modeOrId) {
                const mode = typeof modeOrId === 'object' ? modeOrId : modesById.get(modeOrId);
                return JSON.stringify(mode && mode.icons ? mode.icons.slice(1) : []);
            },
        };
    };
})();
