// /app/static/js/pages/admin_announcement_editor.js
// アナウンス管理の編集画面（お知らせ・テンプレート・部品）。検討メモ announcement_plan_2026-10-29.md の 4章・6章。
// Tiptap（vendor/tiptap/tiptap.bundle.js）は init で動的に読み込む。出す HTML の形は app/services/announcement_html.py の許可リストとそろえる。
// Tiptap の Editor は Alpine のリアクティブな値に入れない（プロキシで包まれると動かなくなる）。
(function () {
    'use strict';

    const COLORS = [
        { key: '2', label: '赤' },
        { key: '9', label: '橙' },
        { key: '7', label: '黄' },
        { key: '3', label: '緑' },
        { key: '5', label: '水色' },
        { key: '4', label: '青' },
        { key: '8', label: '紫' },
        { key: '6', label: '桃' },
    ];
    const SIZES = [
        { key: 's', label: '小' },
        { key: '', label: '標準' },
        { key: 'l', label: '大' },
        { key: 'xl', label: '特大' },
    ];
    const SITE_PAGES = [
        { path: '/account#giftcode', ja: 'コード入力', en: 'Enter a code' },
        { path: '/account', ja: 'アカウントタブ', en: 'Account' },
        { path: '/help/version_history', ja: 'バージョン履歴', en: 'Version history' },
        { path: '/help/announcements', ja: 'お知らせ一覧', en: 'News' },
        { path: '/tools', ja: 'ツールタブ', en: 'Tools' },
        { path: '/tools/app_faq', ja: 'よくある質問', en: 'FAQ' },
        { path: '/tournaments', ja: '大会', en: 'Tournaments' },
    ];
    // カテゴリーを選んだときにタイトルの先頭に入れる【】（今までの定型文と同じ）
    const CATEGORY_PREFIX = {
        1: { ja: '<c2>【緊急のお知らせ】</c2>', en: '<c2>【Urgent Notice】</c2>' },
        2: { ja: '<c5>【お知らせ】</c5>', en: '<c5>【Notice】</c5>' },
        3: { ja: '<c4>【アップデート】</c4>', en: '<c4>【Update】</c4>' },
        4: { ja: '<c9>【プレゼント企画】</c9>', en: '<c9>【Giveaway】</c9>' },
        5: { ja: '<c5>【お知らせ】</c5>', en: '<c5>【Notice】</c5>' },
    };
    const PLATFORM_ORDER = ['web', 'ios', 'android'];
    const PREF_KEY = 'bi_ann_editor_prefs';
    const LANGS = ['ja', 'en'];

    function readPrefs() {
        try {
            return JSON.parse(localStorage.getItem(PREF_KEY) || '{}') || {};
        } catch {
            return {};
        }
    }

    function writePrefs(prefs) {
        try {
            localStorage.setItem(PREF_KEY, JSON.stringify(prefs));
        } catch {
            /* ignore */
        }
    }

    function readStore(key) {
        try {
            const raw = localStorage.getItem(key);
            return raw ? JSON.parse(raw) : null;
        } catch {
            return null;
        }
    }

    function writeStore(key, value) {
        try {
            if (value == null) localStorage.removeItem(key);
            else localStorage.setItem(key, JSON.stringify(value));
        } catch {
            /* quota / private mode */
        }
    }

    function stripTags(html) {
        const div = document.createElement('div');
        div.innerHTML = html || '';
        return (div.textContent || '').trim();
    }

    function nowJstInput() {
        const d = new Date(Date.now() + 9 * 3600 * 1000);
        return d.toISOString().slice(0, 16);
    }

    function jstInputToUtcText(value) {
        if (!value) return '';
        const d = new Date(`${value}:00+09:00`);
        if (Number.isNaN(d.getTime())) return '';
        return d.toISOString().slice(0, 16).replace('T', ' ');
    }

    function formatTime(ms) {
        const d = new Date(ms);
        const pad = (n) => String(n).padStart(2, '0');
        return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    }

    function normalizePlatforms(form) {
        return PLATFORM_ORDER.filter((p) => form[p]).join(',');
    }

    // 本文の最後の空の段落は保存しない（画像・折りたたみの後に自動で足される行）
    function trimTrailingEmpty(html) {
        return (html || '').replace(/(?:<p><\/p>)+$/, '');
    }

    function prettyHtml(html) {
        return (html || '').replace(/(<\/(?:p|h3|li|ul|ol|div|details|summary|figure)>|<hr>)/g, '$1\n');
    }

    // --- Tiptap の部品（許可リストの書式に対応） ---------------------------------------------
    function buildExtensions(T) {
        const { Extension, Mark, Node, mergeAttributes } = T;

        const BBold = T.Bold.extend({
            renderHTML({ HTMLAttributes }) {
                return ['b', mergeAttributes(this.options.HTMLAttributes, HTMLAttributes), 0];
            },
        });
        const BItalic = T.Italic.extend({
            renderHTML({ HTMLAttributes }) {
                return ['i', mergeAttributes(this.options.HTMLAttributes, HTMLAttributes), 0];
            },
        });
        const BStrike = T.Strike.extend({
            renderHTML({ HTMLAttributes }) {
                return ['s', mergeAttributes(this.options.HTMLAttributes, HTMLAttributes), 0];
            },
        });

        // 文字色。今までの <c2>〜<c9>（base.css でダークモードの色もある）をそのまま使う
        const TextColor = Mark.create({
            name: 'textColor',
            addAttributes() {
                return { c: { default: '2', renderHTML: () => ({}), parseHTML: (el) => el.tagName.toLowerCase().slice(1) } };
            },
            parseHTML() {
                return ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9'].map((k) => ({ tag: `c${k}` }));
            },
            renderHTML({ mark }) {
                return [`c${mark.attrs.c}`, 0];
            },
        });

        const FontSize = Mark.create({
            name: 'fontSize',
            addAttributes() {
                return {
                    size: {
                        default: 'l',
                        parseHTML: (el) => ['s', 'l', 'xl'].find((s) => el.classList.contains(`fs-${s}`)) || 'l',
                        renderHTML: (attrs) => ({ class: `fs-${attrs.size}` }),
                    },
                };
            },
            parseHTML() {
                return [{ tag: 'span.fs-s' }, { tag: 'span.fs-l' }, { tag: 'span.fs-xl' }];
            },
            renderHTML({ HTMLAttributes }) {
                return ['span', HTMLAttributes, 0];
            },
        });

        const onlyAttr = {
            platforms: {
                default: 'web',
                parseHTML: (el) => el.getAttribute('data-only'),
                renderHTML: (attrs) => ({ 'data-only': attrs.platforms }),
            },
        };
        // 端末別（文字）。後ろに続けて打った文字には広げない
        const OnlyInline = Mark.create({
            name: 'onlyInline',
            priority: 1100,
            inclusive: false,
            addAttributes() {
                return onlyAttr;
            },
            parseHTML() {
                return [{ tag: 'span[data-only]' }];
            },
            renderHTML({ HTMLAttributes }) {
                return ['span', HTMLAttributes, 0];
            },
        });
        const OnlyBlock = Node.create({
            name: 'onlyBlock',
            group: 'block',
            content: 'block+',
            defining: true,
            addAttributes() {
                return onlyAttr;
            },
            parseHTML() {
                return [{ tag: 'div[data-only]' }];
            },
            renderHTML({ HTMLAttributes }) {
                return ['div', HTMLAttributes, 0];
            },
        });

        const NoteBox = Node.create({
            name: 'noteBox',
            group: 'block',
            content: 'block+',
            defining: true,
            parseHTML() {
                return [{ tag: 'div.bi-note' }];
            },
            renderHTML() {
                return ['div', { class: 'bi-note' }, 0];
            },
        });

        // 折りたたみ。出力は <details class="bi-fold"><summary>…</summary><div class="bi-fold__body">…</div></details>。
        // エディターの中では開いたままの箱にする（details のままだと見出しを押すたびに閉じてしまう）
        const Fold = Node.create({
            name: 'fold',
            group: 'block',
            content: 'foldSummary foldBody',
            defining: true,
            isolating: true,
            parseHTML() {
                return [{ tag: 'details.bi-fold' }];
            },
            renderHTML() {
                return ['details', { class: 'bi-fold' }, 0];
            },
            addNodeView() {
                return () => {
                    const dom = document.createElement('div');
                    dom.className = 'bi-fold bi-fold--editor';
                    return { dom, contentDOM: dom };
                };
            },
        });
        const FoldSummary = Node.create({
            name: 'foldSummary',
            content: 'text*',
            marks: 'bold italic strike underline textColor onlyInline',
            defining: true,
            isolating: true,
            parseHTML() {
                return [{ tag: 'summary' }];
            },
            renderHTML() {
                return ['summary', 0];
            },
            addNodeView() {
                return () => {
                    const dom = document.createElement('div');
                    dom.className = 'bi-fold__summary';
                    return { dom, contentDOM: dom };
                };
            },
        });
        const FoldBody = Node.create({
            name: 'foldBody',
            content: 'block+',
            defining: true,
            isolating: true,
            parseHTML() {
                return [{ tag: 'div.bi-fold__body' }];
            },
            renderHTML() {
                return ['div', { class: 'bi-fold__body' }, 0];
            },
        });

        const Figure = Node.create({
            name: 'figure',
            group: 'block',
            atom: true,
            draggable: true,
            selectable: true,
            addAttributes() {
                return {
                    src: { default: null },
                    alt: { default: '' },
                    width: { default: null },
                    height: { default: null },
                    size: { default: 'auto' },
                    align: { default: 'center' },
                };
            },
            parseHTML() {
                const fromImg = (img, size, align) => ({
                    src: img.getAttribute('src'),
                    alt: img.getAttribute('alt') || '',
                    width: img.getAttribute('width'),
                    height: img.getAttribute('height'),
                    size: ['full', 'half', 'auto'].includes(size) ? size : 'auto',
                    align: ['left', 'center', 'right'].includes(align) ? align : 'center',
                });
                return [
                    {
                        tag: 'figure.bi-figure',
                        getAttrs: (el) => {
                            const img = el.querySelector('img');
                            return img && img.getAttribute('src') ? fromImg(img, el.getAttribute('data-size'), el.getAttribute('data-align')) : false;
                        },
                    },
                    { tag: 'img[src]', getAttrs: (img) => fromImg(img, 'auto') },
                ];
            },
            renderHTML({ node }) {
                const a = node.attrs;
                const img = { src: a.src, alt: a.alt || '' };
                if (a.width) img.width = String(a.width);
                if (a.height) img.height = String(a.height);
                return ['figure', { class: 'bi-figure', 'data-size': a.size || 'auto', 'data-align': a.align || 'center' }, ['img', img]];
            },
        });

        const Align = Extension.create({
            name: 'align',
            addGlobalAttributes() {
                return [{
                    types: ['paragraph', 'heading'],
                    attributes: {
                        align: {
                            default: null,
                            parseHTML: (el) => (el.classList.contains('ta-center') ? 'center' : el.classList.contains('ta-right') ? 'right' : null),
                            renderHTML: (attrs) => (attrs.align === 'center' || attrs.align === 'right' ? { class: `ta-${attrs.align}` } : {}),
                        },
                    },
                }];
            },
        });

        const BLink = T.Link.extend({
            addAttributes() {
                const parent = this.parent?.() || {};
                return {
                    href: parent.href,
                    variant: {
                        default: null,
                        parseHTML: (el) => (el.classList.contains('bi-btn') ? 'button' : null),
                        renderHTML: (attrs) => (attrs.variant === 'button' ? { class: 'bi-btn' } : {}),
                    },
                };
            },
        }).configure({
            openOnClick: false,
            autolink: true,
            linkOnPaste: true,
            defaultProtocol: 'https',
            HTMLAttributes: { target: null, rel: null, class: null },
        });

        const SingleLine = Extension.create({
            name: 'singleLine',
            addKeyboardShortcuts() {
                return { Enter: () => true, 'Shift-Enter': () => true, 'Mod-Enter': () => true };
            },
        });

        return {
            title() {
                return [
                    T.Document.extend({ content: 'text*' }),
                    T.Text,
                    BBold,
                    BItalic,
                    BStrike,
                    T.Underline,
                    TextColor,
                    OnlyInline,
                    T.UndoRedo,
                    SingleLine,
                ];
            },
            body(placeholder) {
                return [
                    T.StarterKit.configure({
                        bold: false,
                        italic: false,
                        strike: false,
                        link: false,
                        code: false,
                        codeBlock: false,
                        blockquote: false,
                        heading: { levels: [3] },
                    }),
                    BBold,
                    BItalic,
                    BStrike,
                    TextColor,
                    FontSize,
                    OnlyInline,
                    OnlyBlock,
                    NoteBox,
                    Fold,
                    FoldSummary,
                    FoldBody,
                    Figure,
                    Align,
                    BLink,
                    T.Placeholder.configure({ placeholder }),
                ];
            },
        };
    }

    window.annEditor = function annEditor() {
        const raw = JSON.parse(document.getElementById('annEditorData').textContent);
        const editors = {};
        let T = null;
        let ext = null;
        let focusedKey = 'body_ja';
        let baseline = '';
        let dirtyTimer = 0;
        let draftTimer = 0;
        let previewTimer = 0;
        let toastTimer = 0;
        let suppressDirty = false;
        const prefs = readPrefs();

        const post = raw.post ? { ...raw.post, category: raw.post.category == null ? '' : String(raw.post.category) } : {};
        const tpl = raw.template
            ? { ...raw.template, category: raw.template.category == null ? '' : String(raw.template.category), settings: { ...(raw.template.settings || {}) } }
            : { kind: 'template', settings: {} };

        return {
            mode: raw.mode,
            canEdit: !!raw.canEdit,
            urls: raw.urls,
            post,
            tpl,
            snippets: raw.snippets || [],
            ready: false,
            loadError: '',
            saving: false,
            dirty: false,
            split: !!prefs.split,
            activeLang: 'ja',
            focusedLang: 'ja',
            bodyFocused: true,
            menu: '',
            dialog: '',
            active: {},
            counts: { ja: 0, en: 0 },
            colors: COLORS,
            sizes: SIZES,
            sitePages: SITE_PAGES,
            message: { text: '', kind: 'info', action: '' },
            toast: { text: '', visible: false, error: false },
            draftRestore: { available: false, savedAt: '', data: null },
            previewOpen: false,
            previewPlatform: 'web',
            previewLang: 'ja',
            previewLoading: false,
            preview: { title_html: '', body_html: '' },
            revisions: { open: false, loading: false, items: [] },
            linkForm: { href: '', text: '', button: false, needText: false, editing: false },
            imageForm: { q: '', items: [], total: 0, loading: false, selected: null, size: 'auto', align: 'center', alt: '', editing: false },
            giftForm: { code: '', label: '' },
            onlyForm: { web: true, ios: false, android: false, scope: 'inline', editing: false },
            htmlForm: { text: '' },
            snippetForm: { name: '', html: '', saving: false },
            templateSettingOptions: [
                { key: 'show_web', label: 'Web' },
                { key: 'show_ios', label: 'iOS' },
                { key: 'show_android', label: 'Android' },
                { key: 'show_ja', label: '日本語' },
                { key: 'show_en', label: '英語' },
                { key: 'on_home', label: 'ホーム' },
                { key: 'listed', label: '一覧' },
            ],

            // --- 表示用 -------------------------------------------------------------------
            get editable() {
                return this.canEdit && !(this.mode === 'post' && this.post.state === 'deleted');
            },
            get stateKey() {
                return this.post.state || 'new';
            },
            get stateLabel() {
                return {
                    new: '新規',
                    draft: '下書き',
                    scheduled: `予約 ${(this.post.published_at || '').replace('T', ' ')}`,
                    published: '公開中',
                    deleted: '削除済み',
                }[this.stateKey] || '';
            },
            get saveLabel() {
                if (this.mode !== 'post') return '保存';
                if (this.post.status === 'draft') return '下書きを保存';
                if (this.post.publish_mode === 'at' && this.post.published_at && new Date(`${this.post.published_at}:00+09:00`) > new Date()) {
                    return '予約して保存';
                }
                return this.stateKey === 'published' ? '保存' : '公開する';
            },
            get sizeLabel() {
                const size = SIZES.find((s) => s.key === (this.active.size || ''));
                return `大きさ: ${size ? size.label : '標準'}`;
            },
            get destinationSummary() {
                const p = this.post;
                const platforms = [p.show_web && 'Web', p.show_ios && 'iOS', p.show_android && 'Android'].filter(Boolean);
                const langs = [p.show_ja && '日本語', p.show_en && '英語'].filter(Boolean);
                if (!platforms.length || !langs.length) return '端末と言語を1つ以上選んでください。';
                const places = [p.on_home && 'ホーム', p.listed && 'お知らせ一覧'].filter(Boolean);
                const where = places.length ? `${places.join('と')}に出します。` : 'ホーム・一覧には出さず、ページの URL だけで見られます。';
                return `${platforms.join('・')} の ${langs.join('・')} のユーザーに、${where}`;
            },
            get previewDate() {
                if (this.mode !== 'post') return '';
                const jst = this.post.publish_mode === 'at' && this.post.published_at ? this.post.published_at : nowJstInput();
                return this.previewLang === 'ja' ? jst.replace('T', ' ') : jstInputToUtcText(jst);
            },
            get previewNote() {
                if (this.mode !== 'post') return '';
                const p = this.post;
                if (!p[`show_${this.previewLang}`] || !p[`show_${this.previewPlatform}`]) {
                    return 'この端末・言語には掲載しません（掲載の設定で選ぶと出ます）。';
                }
                return '';
            },

            // --- 読み込み ---------------------------------------------------------------------
            async init() {
                // 上の帯の高さ（折り返すと変わる）に合わせて、ツールバーを固定する位置を決める
                const bar = this.$root.querySelector('.aa-edit__bar');
                if (bar && 'ResizeObserver' in window) {
                    new ResizeObserver(() => {
                        this.$root.style.setProperty('--aa-bar-h', `${bar.offsetHeight}px`);
                    }).observe(bar);
                }
                try {
                    T = await import(raw.bundleUrl);
                } catch (e) {
                    console.error(e);
                    this.loadError = 'エディターを読み込めませんでした。ページを読み込み直してください。';
                    return;
                }
                ext = buildExtensions(T);
                const source = this.mode === 'post' ? this.post : this.tpl;
                const withTitle = this.mode === 'post' || this.tpl.kind === 'template';
                for (const lang of LANGS) {
                    if (withTitle) this.makeEditor(`title_${lang}`, source[`title_${lang}`] || '');
                    this.makeEditor(`body_${lang}`, source[`body_${lang}`] || '');
                }
                baseline = this.serialize();
                this.updateCounts();
                this.ready = true;
                this.refreshActive();
                this.checkLocalDraft();
                window.addEventListener('beforeunload', (event) => {
                    if (!this.dirty) return;
                    event.preventDefault();
                    event.returnValue = '';
                });
                this.$root.addEventListener('keydown', (event) => {
                    if ((event.metaKey || event.ctrlKey) && !event.altKey && event.code === 'KeyK' && this.bodyFocused) {
                        event.preventDefault();
                        this.openLinkDialog();
                    }
                });
            },

            makeEditor(key, html) {
                const [kind, lang] = key.split('_');
                const element = document.getElementById(kind === 'title' ? `aaTitle_${lang}` : `aaBody_${lang}`);
                const isTitle = kind === 'title';
                const placeholder = isTitle
                    ? (lang === 'ja' ? 'タイトル' : 'Title')
                    : (lang === 'ja' ? '本文を入力…（URL を貼ると自動でリンクになります）' : 'Write the body…');
                const self = this;
                const editor = new T.Editor({
                    element,
                    extensions: isTitle ? ext.title() : ext.body(placeholder),
                    content: html,
                    editable: this.editable,
                    // 「1. 」「- 」「### 」「---」などを打ったときに、勝手にリスト・見出しなどに変えない（打ったとおりに残す）
                    enableInputRules: false,
                    enablePasteRules: ['link'],
                    editorProps: {
                        attributes: { spellcheck: 'false', 'aria-label': `${lang === 'ja' ? '日本語' : '英語'}の${isTitle ? 'タイトル' : '本文'}` },
                        handlePaste(view, event) {
                            if (event.clipboardData?.files?.length) {
                                self.showToast('画像のアップロードは準備中です。今は「画像」→「サイト内の画像」から入れられます。', true);
                                return true;
                            }
                            return false;
                        },
                        handleDrop(view, event) {
                            if (event.dataTransfer?.files?.length) {
                                self.showToast('画像のアップロードは準備中です。今は「画像」→「サイト内の画像」から入れられます。', true);
                                return true;
                            }
                            return false;
                        },
                    },
                    onUpdate: () => this.onEditorUpdate(key),
                    onSelectionUpdate: () => {
                        if (focusedKey === key) this.refreshActive();
                    },
                    onFocus: () => this.onEditorFocus(key),
                });
                if (isTitle) {
                    element.dataset.placeholder = placeholder;
                    element.classList.toggle('is-empty', editor.isEmpty);
                }
                editors[key] = editor;
            },

            // --- 値の取り出し ---------------------------------------------------------------
            editorHtml(key) {
                const editor = editors[key];
                if (!editor) return '';
                if (key.startsWith('title_')) return editor.isEmpty ? '' : editor.getHTML();
                return editor.isEmpty ? '' : trimTrailingEmpty(editor.getHTML());
            },

            collect() {
                const texts = {};
                for (const lang of LANGS) {
                    texts[`title_${lang}`] = this.editorHtml(`title_${lang}`);
                    texts[`body_${lang}`] = this.editorHtml(`body_${lang}`);
                }
                if (this.mode === 'post') {
                    const p = this.post;
                    return {
                        category: p.category,
                        status: p.status,
                        publish_mode: p.publish_mode,
                        published_at: p.published_at,
                        show_web: p.show_web,
                        show_ios: p.show_ios,
                        show_android: p.show_android,
                        show_ja: p.show_ja,
                        show_en: p.show_en,
                        on_home: p.on_home,
                        listed: p.listed,
                        source_type: p.source_type,
                        source_id: p.source_id,
                        source_role: p.source_role,
                        ...texts,
                    };
                }
                const t = this.tpl;
                return { name: t.name, category: t.category, settings: t.settings, sort_order: t.sort_order, ...texts };
            },

            serialize() {
                return JSON.stringify(this.collect());
            },

            // --- 変更の検出・ブラウザへの下書き -------------------------------------------------------
            draftKey() {
                const id = this.mode === 'post' ? this.post.id : this.tpl.id;
                return `bi_ann_draft:${this.mode}:${this.mode === 'template' ? this.tpl.kind + ':' : ''}${id || 'new'}`;
            },

            markDirty() {
                if (suppressDirty) return;
                clearTimeout(dirtyTimer);
                dirtyTimer = setTimeout(() => {
                    const current = this.serialize();
                    this.dirty = current !== baseline;
                    clearTimeout(draftTimer);
                    draftTimer = setTimeout(() => {
                        if (this.dirty) writeStore(this.draftKey(), { savedAt: Date.now(), data: current });
                        else writeStore(this.draftKey(), null);
                    }, 800);
                }, 120);
                if (this.previewOpen) this.schedulePreview();
            },

            checkLocalDraft() {
                const stored = readStore(this.draftKey());
                if (!stored || !stored.data || stored.data === baseline) return;
                this.draftRestore = { available: true, savedAt: formatTime(stored.savedAt), data: stored.data };
            },

            restoreLocalDraft() {
                try {
                    this.applyData(JSON.parse(this.draftRestore.data));
                } catch {
                    /* 壊れていたら捨てる */
                }
                this.draftRestore = { available: false, savedAt: '', data: null };
            },

            discardLocalDraft() {
                writeStore(this.draftKey(), null);
                this.draftRestore = { available: false, savedAt: '', data: null };
            },

            applyData(data, { asBaseline = false } = {}) {
                suppressDirty = true;
                const target = this.mode === 'post' ? this.post : this.tpl;
                for (const [key, value] of Object.entries(data)) {
                    if (key.startsWith('title_') || key.startsWith('body_')) {
                        const editor = editors[key];
                        if (editor) {
                            editor.commands.setContent(value || '', { emitUpdate: false });
                            if (key.startsWith('title_')) editor.options.element.classList.toggle('is-empty', editor.isEmpty);
                        }
                    } else if (key in target || this.mode === 'post') {
                        target[key] = key === 'category' ? (value == null ? '' : String(value)) : value;
                    }
                }
                suppressDirty = false;
                this.updateCounts();
                if (asBaseline) {
                    baseline = this.serialize();
                    this.dirty = false;
                } else {
                    this.markDirty();
                }
            },

            onEditorUpdate(key) {
                const editor = editors[key];
                if (key.startsWith('title_')) editor.options.element.classList.toggle('is-empty', editor.isEmpty);
                this.updateCounts();
                this.markDirty();
            },

            updateCounts() {
                for (const lang of LANGS) {
                    const body = editors[`body_${lang}`];
                    this.counts[lang] = body ? body.getText().replace(/\s/g, '').length : 0;
                }
            },

            onEditorFocus(key) {
                focusedKey = key;
                const [kind, lang] = key.split('_');
                this.bodyFocused = kind === 'body';
                this.focusedLang = lang;
                if (!this.split) this.activeLang = lang;
                this.refreshActive();
            },

            currentAlign(ed) {
                const sel = ed.state.selection;
                if (sel.node && sel.node.type.name === 'figure') return sel.node.attrs.align || 'center';
                for (let d = sel.$from.depth; d > 0; d -= 1) {
                    const node = sel.$from.node(d);
                    if (node.type.name === 'paragraph' || node.type.name === 'heading') return node.attrs.align || 'left';
                }
                return 'left';
            },

            refreshActive() {
                const ed = editors[focusedKey];
                if (!ed) return;
                const is = (name, attrs) => {
                    try {
                        return ed.isActive(name, attrs);
                    } catch {
                        return false;
                    }
                };
                const attr = (name, key) => {
                    if (!ed.schema.nodes[name] && !ed.schema.marks[name]) return null;
                    try {
                        return ed.getAttributes(name)[key] ?? null;
                    } catch {
                        return null;
                    }
                };
                this.active = {
                    bold: is('bold'),
                    italic: is('italic'),
                    strike: is('strike'),
                    underline: is('underline'),
                    color: is('textColor') ? attr('textColor', 'c') : null,
                    size: is('fontSize') ? attr('fontSize', 'size') : '',
                    heading: is('heading', { level: 3 }),
                    bulletList: is('bulletList'),
                    orderedList: is('orderedList'),
                    align: this.currentAlign(ed),
                    listStart: is('orderedList') ? (attr('orderedList', 'start') || 1) : null,
                    note: is('noteBox'),
                    fold: is('fold'),
                    link: is('link'),
                    only: is('onlyInline') || is('onlyBlock'),
                };
            },

            // --- ツールバー -----------------------------------------------------------------
            toolbarMousedown(event) {
                // ボタンを押してもエディターの選択を失わないようにする
                if (event.target.closest('input, select, textarea')) return;
                event.preventDefault();
            },

            focusedEditor() {
                return editors[focusedKey];
            },

            cmd(name, arg) {
                const ed = this.focusedEditor();
                if (!ed || !this.editable) return;
                const c = ed.chain().focus();
                switch (name) {
                    case 'bold': c.toggleBold(); break;
                    case 'italic': c.toggleItalic(); break;
                    case 'strike': c.toggleStrike(); break;
                    case 'underline': c.toggleUnderline(); break;
                    case 'color':
                        if (arg) c.setMark('textColor', { c: arg });
                        else c.unsetMark('textColor');
                        break;
                    case 'size':
                        if (!this.bodyFocused) return;
                        if (arg) c.setMark('fontSize', { size: arg });
                        else c.unsetMark('fontSize');
                        break;
                    case 'clear':
                        for (const mark of ['bold', 'italic', 'strike', 'underline', 'textColor', 'fontSize']) {
                            if (ed.schema.marks[mark]) c.unsetMark(mark);
                        }
                        break;
                    case 'heading': c.toggleHeading({ level: 3 }); break;
                    case 'bulletList': c.toggleBulletList(); break;
                    case 'orderedList': c.toggleOrderedList(); break;
                    case 'align': {
                        // 画像を選んでいれば画像の配置、それ以外は段落・見出しの配置（ボタン・ギフトコードもこれに従う）
                        const sel = ed.state.selection;
                        if (sel.node && sel.node.type.name === 'figure') {
                            c.updateAttributes('figure', { align: arg || 'center' });
                        } else {
                            const align = arg === 'left' ? null : arg;
                            c.updateAttributes('paragraph', { align }).updateAttributes('heading', { align });
                        }
                        break;
                    }
                    case 'listStart': {
                        const start = Math.max(1, Math.min(9999, parseInt(arg, 10) || 1));
                        c.updateAttributes('orderedList', { start });
                        break;
                    }
                    case 'hr': c.setHorizontalRule(); break;
                    case 'note':
                        if (ed.isActive('noteBox')) c.lift('noteBox');
                        else c.wrapIn('noteBox');
                        break;
                    case 'fold':
                        this.toggleFold(ed);
                        return;
                    case 'undo': c.undo(); break;
                    case 'redo': c.redo(); break;
                    default: return;
                }
                c.run();
                this.refreshActive();
            },

            toggleFold(ed) {
                const { state } = ed;
                const { schema } = state;
                const { $from, $to } = state.selection;
                for (let d = $from.depth; d > 0; d -= 1) {
                    const node = $from.node(d);
                    if (node.type.name !== 'fold') continue;
                    // 折りたたみを外す: 見出しを1段落にして、中身をそのまま出す
                    const pos = $from.before(d);
                    const content = [];
                    const summary = node.child(0);
                    if (summary.content.size) content.push(schema.nodes.paragraph.create(null, summary.content));
                    node.child(1).forEach((child) => content.push(child));
                    ed.chain().focus().command(({ tr }) => {
                        tr.replaceWith(pos, pos + node.nodeSize, content);
                        return true;
                    }).run();
                    this.refreshActive();
                    return;
                }
                const label = this.focusedLang === 'ja' ? '見出し（押すと開きます）' : 'Title (tap to open)';
                const range = state.selection.empty ? null : $from.blockRange($to);
                let body = [schema.nodes.paragraph.create()];
                if (range) {
                    body = [];
                    state.doc.slice(range.start, range.end).content.forEach((child) => body.push(child));
                }
                const fold = schema.nodes.fold.create(null, [
                    schema.nodes.foldSummary.create(null, schema.text(label)),
                    schema.nodes.foldBody.create(null, body),
                ]);
                ed.chain().focus().command(({ tr }) => {
                    if (range) tr.replaceWith(range.start, range.end, fold);
                    else tr.replaceSelectionWith(fold);
                    return true;
                }).run();
                this.refreshActive();
            },

            // --- リンク -------------------------------------------------------------------
            openLinkDialog() {
                const ed = this.focusedEditor();
                if (!ed || !this.bodyFocused || !this.editable) return;
                const editing = ed.isActive('link');
                const attrs = ed.getAttributes('link');
                this.linkForm = {
                    href: attrs.href || '',
                    text: '',
                    button: attrs.variant === 'button',
                    needText: ed.state.selection.empty && !editing,
                    editing,
                };
                this.dialog = 'link';
                this.$nextTick(() => this.$refs.linkHref?.focus());
            },

            normalizeHref(value) {
                const href = (value || '').trim();
                if (!href) return '';
                if (/^(https?:|mailto:|\/|#)/i.test(href)) return href;
                if (/^[\w-]+(\.[\w-]+)+(\/|$)/.test(href)) return `https://${href}`;
                return href;
            },

            applyLink() {
                const ed = this.focusedEditor();
                const href = this.normalizeHref(this.linkForm.href);
                if (!ed || !href) return;
                const attrs = { href, variant: this.linkForm.button ? 'button' : null };
                const c = ed.chain().focus();
                if (this.linkForm.editing) {
                    c.extendMarkRange('link').setLink(attrs);
                } else if (this.linkForm.needText) {
                    const text = this.linkForm.text.trim() || href;
                    c.insertContent({ type: 'text', text, marks: [{ type: 'link', attrs }] });
                } else {
                    c.setLink(attrs);
                }
                c.run();
                this.dialog = '';
                this.refreshActive();
            },

            removeLink() {
                const ed = this.focusedEditor();
                if (ed) ed.chain().focus().extendMarkRange('link').unsetLink().run();
                this.dialog = '';
                this.refreshActive();
            },

            // --- 画像（今はサイト内の画像だけ） -------------------------------------------------------
            openImageDialog() {
                const ed = this.focusedEditor();
                if (!ed || !this.bodyFocused || !this.editable) return;
                const sel = ed.state.selection;
                const node = sel.node && sel.node.type.name === 'figure' ? sel.node : null;
                this.imageForm.editing = !!node;
                this.imageForm.selected = node ? { src: node.attrs.src, width: node.attrs.width, height: node.attrs.height } : null;
                this.imageForm.size = node ? node.attrs.size : 'auto';
                this.imageForm.align = node ? (node.attrs.align || 'center') : 'center';
                this.imageForm.alt = node ? node.attrs.alt : '';
                this.dialog = 'image';
                if (!this.imageForm.items.length) this.searchImages();
                this.$nextTick(() => this.$refs.imageQ?.focus());
            },

            async searchImages() {
                this.imageForm.loading = true;
                try {
                    const res = await fetch(`${this.urls.staticImages}?q=${encodeURIComponent(this.imageForm.q)}`);
                    const data = await res.json();
                    this.imageForm.items = data.items || [];
                    this.imageForm.total = data.total || 0;
                } catch {
                    this.imageForm.items = [];
                    this.imageForm.total = 0;
                } finally {
                    this.imageForm.loading = false;
                }
            },

            applyImage() {
                const ed = this.focusedEditor();
                const img = this.imageForm.selected;
                if (!ed || !img) return;
                const attrs = {
                    src: img.src,
                    alt: this.imageForm.alt.trim(),
                    width: img.width || null,
                    height: img.height || null,
                    size: this.imageForm.size,
                    align: this.imageForm.align,
                };
                if (this.imageForm.editing) {
                    ed.chain().focus().updateAttributes('figure', attrs).run();
                } else {
                    ed.chain().focus().insertContent({ type: 'figure', attrs }).run();
                    // 入れた画像が選ばれたままだと、次に打った文字・差し込んだもので置き換わるので、次の行へ移る
                    const after = ed.state.selection.to + 1;
                    if (after <= ed.state.doc.content.size) ed.commands.setTextSelection(after);
                }
                this.dialog = '';
            },

            // --- ギフトコード ---------------------------------------------------------------------
            openGiftDialog() {
                if (!this.bodyFocused || !this.editable) return;
                this.giftForm = { code: '', label: this.focusedLang === 'ja' ? 'コードを入力する' : 'Enter the code' };
                this.dialog = 'gift';
                this.$nextTick(() => this.$refs.giftCode?.focus());
            },

            applyGift() {
                const ed = this.focusedEditor();
                const code = this.giftForm.code.trim();
                if (!ed || !code) return;
                const href = `/${this.focusedLang}/account?giftcode=${encodeURIComponent(code)}`;
                const text = this.giftForm.label.trim() || code;
                ed.chain().focus().insertContent({
                    type: 'paragraph',
                    attrs: { align: 'center' },
                    content: [{ type: 'text', text, marks: [{ type: 'link', attrs: { href, variant: 'button' } }] }],
                }).run();
                this.dialog = '';
            },

            // --- 端末別 ---------------------------------------------------------------------
            openOnlyDialog() {
                const ed = this.focusedEditor();
                if (!ed || !this.editable) return;
                const { selection } = ed.state;
                let scope = 'inline';
                let editing = false;
                let value = 'web';
                if (ed.isActive('onlyInline')) {
                    editing = true;
                    value = ed.getAttributes('onlyInline').platforms || 'web';
                } else if (this.bodyFocused && ed.isActive('onlyBlock') && selection.empty) {
                    scope = 'block';
                    editing = true;
                    value = ed.getAttributes('onlyBlock').platforms || 'web';
                } else if (this.bodyFocused && (selection.empty || !selection.$from.sameParent(selection.$to))) {
                    scope = 'block';
                } else if (selection.empty) {
                    this.showToast('端末別にする文字を選んでください。', true);
                    return;
                }
                const picked = value.split(',');
                this.onlyForm = {
                    web: picked.includes('web'),
                    ios: picked.includes('ios'),
                    android: picked.includes('android'),
                    scope,
                    editing,
                };
                this.dialog = 'only';
            },

            setOnly(web, ios, android) {
                Object.assign(this.onlyForm, { web, ios, android });
            },

            applyOnly() {
                const ed = this.focusedEditor();
                if (!ed) return;
                const platforms = normalizePlatforms(this.onlyForm);
                if (!platforms) return;
                if (platforms === PLATFORM_ORDER.join(',')) {
                    this.removeOnly();
                    return;
                }
                const c = ed.chain().focus();
                if (this.onlyForm.scope === 'inline') {
                    if (this.onlyForm.editing) c.extendMarkRange('onlyInline');
                    c.setMark('onlyInline', { platforms });
                } else if (this.onlyForm.editing) {
                    c.updateAttributes('onlyBlock', { platforms });
                } else {
                    c.wrapIn('onlyBlock', { platforms });
                }
                c.run();
                this.dialog = '';
                this.refreshActive();
            },

            removeOnly() {
                const ed = this.focusedEditor();
                if (!ed) return;
                if (this.onlyForm.scope === 'inline') ed.chain().focus().extendMarkRange('onlyInline').unsetMark('onlyInline').run();
                else ed.chain().focus().lift('onlyBlock').run();
                this.dialog = '';
                this.refreshActive();
            },

            // --- HTML の直接編集・部品 -------------------------------------------------------------
            openHtmlDialog() {
                const ed = this.focusedEditor();
                if (!ed || !this.bodyFocused || !this.editable) return;
                this.htmlForm.text = prettyHtml(ed.getHTML());
                this.dialog = 'html';
            },

            applyHtml() {
                const ed = this.focusedEditor();
                if (!ed) return;
                ed.commands.setContent(this.htmlForm.text.replace(/\n/g, ''), { emitUpdate: true });
                this.dialog = '';
            },

            snippetsFor(lang) {
                return this.snippets.filter((s) => s[`body_${lang}`]);
            },

            insertSnippet(snippet) {
                const ed = this.focusedEditor();
                if (!ed || !this.bodyFocused) return;
                ed.chain().focus().insertContent(snippet[`body_${this.focusedLang}`]).run();
            },

            openSnippetSave() {
                const ed = this.focusedEditor();
                if (!ed || !this.bodyFocused) return;
                if (ed.state.selection.empty) {
                    this.showToast('部品にする部分を選んでください。', true);
                    return;
                }
                this.snippetForm = {
                    name: '',
                    html: T.getHTMLFromFragment(ed.state.selection.content().content, ed.schema),
                    saving: false,
                };
                this.dialog = 'snippetSave';
                this.$nextTick(() => this.$refs.snippetName?.focus());
            },

            async saveSnippet() {
                if (!this.snippetForm.name.trim() || this.snippetForm.saving) return;
                this.snippetForm.saving = true;
                const payload = { kind: 'snippet', name: this.snippetForm.name.trim(), body_ja: '', body_en: '' };
                payload[`body_${this.focusedLang}`] = this.snippetForm.html;
                try {
                    const data = await this.postJson(this.urls.templateSave, payload);
                    this.snippets.push(data.template);
                    this.dialog = '';
                    this.showToast(`部品「${data.template.name}」を保存しました。`);
                } catch (e) {
                    this.showToast(e.message, true);
                } finally {
                    this.snippetForm.saving = false;
                }
            },

            closeDialogs() {
                this.dialog = '';
                this.menu = '';
            },

            // --- 掲載の設定 -----------------------------------------------------------------
            setField(name, value) {
                this.post[name] = value;
                if (name === 'publish_mode' && value === 'now') this.post.published_at = this.post.published_at || '';
                this.markDirty();
            },

            setPublishAt() {
                this.post.publish_mode = 'at';
                if (!this.post.published_at) this.post.published_at = nowJstInput();
                this.markDirty();
            },

            toggleField(name) {
                this.post[name] = !this.post[name];
                this.markDirty();
            },

            setPlatforms(web, ios, android) {
                Object.assign(this.post, { show_web: web, show_ios: ios, show_android: android });
                this.markDirty();
            },

            tplSetting(key) {
                return this.tpl.settings[key] !== false;
            },

            toggleTplSetting(key) {
                this.tpl.settings = { ...this.tpl.settings, [key]: !this.tplSetting(key) };
                this.markDirty();
            },

            onCategoryChange() {
                const prefix = CATEGORY_PREFIX[this.post.category];
                const known = Object.values(CATEGORY_PREFIX).flatMap((v) => [stripTags(v.ja), stripTags(v.en)]);
                if (prefix) {
                    for (const lang of LANGS) {
                        const ed = editors[`title_${lang}`];
                        if (!ed) continue;
                        const text = ed.getText().trim();
                        if (!text || known.includes(text)) {
                            ed.commands.setContent(prefix[lang], { emitUpdate: true });
                            // 続けて打つ文字に【】の色が付かないようにする
                            ed.chain().focus('end').unsetMark('textColor').run();
                        }
                    }
                }
                this.markDirty();
            },

            startFromTemplate(id) {
                if (!id) return;
                if (this.dirty && !confirm('今の内容は消えます。テンプレートから始めますか？')) return;
                this.dirty = false;
                writeStore(this.draftKey(), null);
                window.location.href = `${this.urls.new}?template=${encodeURIComponent(id)}`;
            },

            switchLang(lang) {
                this.activeLang = lang;
                focusedKey = `body_${lang}`;
                this.focusedLang = lang;
                this.bodyFocused = true;
                this.refreshActive();
            },

            toggleSplit() {
                this.split = !this.split;
                writePrefs({ ...readPrefs(), split: this.split });
            },

            copyJaToEn() {
                const from = editors.body_ja;
                const to = editors.body_en;
                if (!from || !to || !this.editable) return;
                if (!to.isEmpty && !confirm('英語の本文を、日本語の本文で置き換えますか？')) return;
                to.commands.setContent(from.getHTML(), { emitUpdate: true });
                const titleFrom = editors.title_ja;
                const titleTo = editors.title_en;
                if (titleFrom && titleTo && (titleTo.isEmpty || !confirm('タイトルはそのままにしますか？（キャンセルで日本語のタイトルを写します）'))) {
                    titleTo.commands.setContent(titleFrom.getHTML(), { emitUpdate: true });
                }
                this.switchLang('en');
                this.showToast('日本語を英語にコピーしました。英語に直してください。');
            },

            // --- プレビュー -----------------------------------------------------------------
            togglePreview() {
                this.previewOpen = !this.previewOpen;
                if (this.previewOpen) {
                    this.previewLang = this.split ? this.focusedLang : this.activeLang;
                    this.refreshPreview();
                }
            },

            schedulePreview() {
                clearTimeout(previewTimer);
                previewTimer = setTimeout(() => this.refreshPreview(), 600);
            },

            async refreshPreview() {
                if (!this.ready) return;
                const lang = this.previewLang;
                this.previewLoading = true;
                try {
                    const res = await fetch(this.urls.preview, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            platform: this.previewPlatform,
                            lang,
                            title: this.editorHtml(`title_${lang}`),
                            body: this.editorHtml(`body_${lang}`),
                        }),
                    });
                    if (!res.ok) throw new Error();
                    this.preview = await res.json();
                } catch {
                    this.showToast('プレビューを作れませんでした。', true);
                } finally {
                    this.previewLoading = false;
                }
            },

            // --- 保存・削除・復元 -------------------------------------------------------------
            async postJson(url, payload) {
                const res = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: payload === undefined ? undefined : JSON.stringify(payload),
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok || data.success === false) {
                    const error = new Error(data.message || data.detail || '処理に失敗しました。');
                    error.code = data.code;
                    throw error;
                }
                return data;
            },

            setMessage(text, kind = 'info', action = '') {
                this.message = { text, kind, action };
            },

            showToast(text, error = false) {
                this.toast = { text, visible: true, error };
                clearTimeout(toastTimer);
                toastTimer = setTimeout(() => {
                    this.toast.visible = false;
                }, 2600);
            },

            async save() {
                if (!this.ready || this.saving || !this.editable) return;
                if (this.mode === 'template') {
                    await this.saveTemplate();
                    return;
                }
                const p = this.post;
                const wasPublic = p.state === 'published' || p.state === 'scheduled';
                if (p.status === 'published' && !wasPublic) {
                    const when = p.publish_mode === 'at' && p.published_at ? `${p.published_at.replace('T', ' ')}（日本時間）に` : '今すぐ';
                    if (!confirm(`このお知らせを${when}公開しますか？`)) return;
                }
                this.saving = true;
                const payload = { ...this.collect(), id: p.id || null, version: p.version || null };
                try {
                    const data = await this.postJson(this.urls.save, payload);
                    Object.assign(this.post, {
                        id: data.id,
                        version: data.version,
                        state: data.state,
                        updated_at: data.updated_at,
                    });
                    if (data.published_at) {
                        this.post.published_at = data.published_at;
                        this.post.publish_mode = 'at';
                    }
                    writeStore(this.draftKey(), null);
                    if (!payload.id) {
                        writeStore(`bi_ann_draft:post:new`, null);
                        history.replaceState(history.state, '', data.edit_url);
                    }
                    baseline = this.serialize();
                    this.dirty = false;
                    this.setMessage('', 'info');
                    this.showToast(data.message);
                    if (this.revisions.open) this.loadRevisions();
                } catch (e) {
                    if (e.code === 'conflict') this.setMessage(e.message, 'error', 'reload');
                    else this.setMessage(e.message, 'error');
                } finally {
                    this.saving = false;
                }
            },

            async saveTemplate() {
                const t = this.tpl;
                this.saving = true;
                try {
                    const data = await this.postJson(this.urls.templateSave, {
                        ...this.collect(),
                        id: t.id || null,
                        kind: t.kind,
                        category: t.category || null,
                    });
                    const isNew = !t.id;
                    if (isNew) writeStore(this.draftKey(), null);
                    t.id = data.id;
                    writeStore(this.draftKey(), null);
                    if (isNew) history.replaceState(history.state, '', data.edit_url);
                    baseline = this.serialize();
                    this.dirty = false;
                    this.setMessage('', 'info');
                    this.showToast(data.message);
                } catch (e) {
                    this.setMessage(e.message, 'error');
                } finally {
                    this.saving = false;
                }
            },

            async reloadLatest() {
                if (!this.post.id) return;
                if (this.dirty && !confirm('最新の内容を読み込むと、この画面での変更は消えます。読み込みますか？')) return;
                try {
                    const res = await fetch(`${this.urls.list.replace(/\/$/, '')}/${this.post.id}/data`);
                    const data = await res.json();
                    const latest = data.post;
                    const fields = {};
                    for (const key of Object.keys(this.collect())) {
                        if (key in latest) fields[key] = latest[key];
                    }
                    this.applyData(fields, { asBaseline: true });
                    Object.assign(this.post, {
                        version: latest.version,
                        state: latest.state,
                        updated_at: latest.updated_at,
                        updated_by_name: latest.updated_by_name,
                    });
                    writeStore(this.draftKey(), null);
                    this.setMessage('最新の内容を読み込みました。', 'info');
                } catch {
                    this.setMessage('読み込めませんでした。ページを読み込み直してください。', 'error');
                }
            },

            async deletePost() {
                if (!this.post.id) return;
                if (!confirm(`#${this.post.id} を削除しますか？\n（一覧の「削除済み」から復元できます）`)) return;
                try {
                    const data = await this.postJson(`${this.urls.list.replace(/\/$/, '')}/${this.post.id}/delete`);
                    this.post.state = 'deleted';
                    this.post.version = data.version;
                    this.post.deleted_at = '今';
                    this.setEditable(false);
                    this.showToast(data.message);
                } catch (e) {
                    this.setMessage(e.message, 'error');
                }
            },

            async restorePost() {
                if (!this.post.id) return;
                try {
                    const data = await this.postJson(`${this.urls.list.replace(/\/$/, '')}/${this.post.id}/restore`);
                    this.post.state = data.state;
                    this.post.version = data.version;
                    this.setEditable(this.canEdit);
                    this.showToast(data.message);
                } catch (e) {
                    this.setMessage(e.message, 'error');
                }
            },

            setEditable(value) {
                for (const editor of Object.values(editors)) editor.setEditable(value);
            },

            // --- 変更の履歴 -----------------------------------------------------------------
            toggleRevisions() {
                this.revisions.open = !this.revisions.open;
                if (this.revisions.open) this.loadRevisions();
            },

            async loadRevisions() {
                this.revisions.loading = true;
                try {
                    const res = await fetch(`${this.urls.list.replace(/\/$/, '')}/${this.post.id}/revisions`);
                    const data = await res.json();
                    this.revisions.items = data.items || [];
                } catch {
                    this.revisions.items = [];
                } finally {
                    this.revisions.loading = false;
                }
            },

            async loadRevision(revision) {
                if (this.dirty && !confirm('この画面での変更は消えます。この版を読み込みますか？')) return;
                try {
                    const res = await fetch(`${this.urls.list.replace(/\/$/, '')}/${this.post.id}/revisions/${revision.id}`);
                    const data = await res.json();
                    const snap = data.snapshot || {};
                    const fields = {};
                    for (const key of Object.keys(this.collect())) {
                        if (key in snap) fields[key] = snap[key];
                    }
                    if (snap.published_at) fields.publish_mode = 'at';
                    this.applyData(fields);
                    this.showToast(`版 ${revision.version} を読み込みました。保存すると、この版に戻ります。`);
                } catch {
                    this.showToast('読み込めませんでした。', true);
                }
            },

            confirmLeave(event) {
                if (this.dirty && !confirm('保存していない変更があります。移動しますか？')) {
                    event.preventDefault();
                    return;
                }
                this.dirty = false;
            },
        };
    };
})();
