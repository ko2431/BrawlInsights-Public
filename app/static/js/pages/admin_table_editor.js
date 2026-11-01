// 行ごとのリアクティブな状態を作らず、変更された行だけを保持する。
function withAdminTableEditor(component) {
    const changedRows = new Map();
    const originalInit = component.init;
    const originalDestroy = component.destroy;
    let root;
    let onClick;
    let onBeforeUnload;

    component.hasUnsavedChanges = false;
    component.getChangedRows = () => Array.from(changedRows.values(), (row) => ({ ...row }));
    component.init = function init() {
        root = this.$root;
        // 大量の input を置くとパスワード自動入力の解析で停止するため、チェック切替にはボタンを使う。
        onClick = (event) => {
            const input = event.target.closest?.('button[role="checkbox"][data-admin-edit-field]');
            if (!input || input.disabled) {
                return;
            }
            const row = input.closest('tr[data-admin-edit-id]');
            if (!row || !root.contains(row)) {
                return;
            }
            input.setAttribute('aria-checked', String(input.getAttribute('aria-checked') !== 'true'));

            const data = { id: Number(row.dataset.adminEditId) };
            let dirty = false;
            // 初期値はサーバーで出力した属性に保持する。走査は変更された行だけ。
            row.querySelectorAll('button[data-admin-edit-field]').forEach((field) => {
                const initial = field.dataset.adminEditInitial === 'true';
                const checked = field.getAttribute('aria-checked') === 'true';
                const changed = !field.disabled && checked !== initial;
                field.classList.toggle('changed-field', changed);
                data[field.dataset.adminEditField] = field.disabled ? initial : checked;
                dirty = dirty || changed;
            });
            row.classList.toggle('changed', dirty);
            if (dirty) {
                changedRows.set(data.id, data);
            } else {
                changedRows.delete(data.id);
            }
            this.hasUnsavedChanges = changedRows.size > 0;
        };
        onBeforeUnload = (event) => {
            if (this.hasUnsavedChanges) {
                event.preventDefault();
                event.returnValue = '';
            }
        };
        root.addEventListener('click', onClick);
        window.addEventListener('beforeunload', onBeforeUnload);
        if (typeof originalInit === 'function') {
            originalInit.call(this);
        }
    };
    component.destroy = function destroy() {
        root.removeEventListener('click', onClick);
        window.removeEventListener('beforeunload', onBeforeUnload);
        changedRows.clear();
        if (typeof originalDestroy === 'function') {
            originalDestroy.call(this);
        }
    };
    return component;
}
