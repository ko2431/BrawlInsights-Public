const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../app/static/js/pages/admin_table_editor.js'), 'utf8');

function eventTarget() {
    const listeners = new Map();
    return {
        listeners,
        addEventListener: (name, callback) => listeners.set(name, callback),
        removeEventListener: (name, callback) => {
            if (listeners.get(name) === callback) listeners.delete(name);
        },
    };
}

function classes() {
    const values = new Set();
    return {
        contains: (name) => values.has(name),
        toggle: (name, enabled) => enabled ? values.add(name) : values.delete(name),
    };
}

function fixture() {
    const window = eventTarget();
    const root = eventTarget();
    const rows = [];
    root.contains = (row) => rows.includes(row);
    // 初期化時・変更時に表全体を走査した場合は失敗させる。
    root.querySelectorAll = () => { throw new Error('表全体の走査は禁止'); };
    const decorate = vm.runInNewContext(source + '\nwithAdminTableEditor;', { window });
    let initialized = 0;
    let destroyed = 0;
    const component = decorate({
        $root: root,
        init() { initialized++; },
        destroy() { destroyed++; },
    });
    component.init();

    function row(id, fields) {
        const item = { dataset: { adminEditId: String(id) }, classList: classes(), scans: 0 };
        item.inputs = Object.entries(fields).map(([name, options]) => {
            const attributes = new Map([['aria-checked', String(options.checked)]]);
            const control = {
                dataset: { adminEditField: name, adminEditInitial: String(options.checked) },
                disabled: !!options.disabled,
                classList: classes(),
                getAttribute: (key) => attributes.get(key),
                setAttribute: (key, value) => attributes.set(key, value),
                closest: (selector) => selector.startsWith('button') ? control : item,
            };
            return control;
        });
        item.querySelectorAll = () => { item.scans++; return item.inputs; };
        rows.push(item);
        return item;
    }

    return {
        component, root, window, row,
        initialized: () => initialized,
        destroyed: () => destroyed,
        changes: () => JSON.parse(JSON.stringify(component.getChangedRows())),
        change(input, checked) {
            if ((input.getAttribute('aria-checked') === 'true') !== checked) {
                root.listeners.get('click')({ target: input });
            }
        },
    };
}

test('初期化で行を走査せず、変更された行だけを保存対象にする', () => {
    const f = fixture();
    const edited = f.row(10, { is_deleted: { checked: false }, is_closed: { checked: true } });
    const untouched = f.row(20, { is_deleted: { checked: false } });
    assert.equal(f.component.hasUnsavedChanges, false);
    assert.deepEqual(f.changes(), []);
    f.change(edited.inputs[0], true);
    assert.deepEqual(f.changes(), [{ id: 10, is_deleted: true, is_closed: true }]);
    assert.equal(edited.classList.contains('changed'), true);
    assert.equal(edited.inputs[0].classList.contains('changed-field'), true);
    assert.equal(untouched.scans, 0);
    assert.equal(f.component.hasUnsavedChanges, true);
});

test('削除・クローズを個別に戻し、全項目を戻した行だけ保存対象から外す', () => {
    const f = fixture();
    const first = f.row(1, { is_deleted: { checked: false }, is_closed: { checked: true } });
    const second = f.row(2, { is_deleted: { checked: true } });
    f.change(first.inputs[0], true);
    f.change(first.inputs[1], false);
    f.change(second.inputs[0], false);
    f.change(first.inputs[0], false);
    assert.equal(first.inputs[0].classList.contains('changed-field'), false);
    assert.equal(first.classList.contains('changed'), true);
    f.change(first.inputs[1], true);
    assert.deepEqual(f.changes(), [{ id: 2, is_deleted: false }]);
    assert.equal(first.classList.contains('changed'), false);
    assert.equal(f.component.hasUnsavedChanges, true);
    f.change(second.inputs[0], true);
    assert.deepEqual(f.changes(), []);
    assert.equal(f.component.hasUnsavedChanges, false);
});

test('保護された項目・閲覧専用の行を変更として扱わない', () => {
    const f = fixture();
    const row = f.row(1, { is_deleted: { checked: false }, is_closed: { checked: true, disabled: true } });
    const readonly = f.row(2, { is_deleted: { checked: false, disabled: true } });
    f.change(row.inputs[1], false);
    f.change(readonly.inputs[0], true);
    assert.deepEqual(f.changes(), []);
    f.change(row.inputs[0], true);
    assert.deepEqual(f.changes(), [{ id: 1, is_deleted: true, is_closed: true }]);
    assert.equal(row.inputs[1].classList.contains('changed-field'), false);
});

test('保存用のスナップショットへの変更が、保持している変更内容に影響しない', () => {
    const f = fixture();
    const row = f.row(1, { is_deleted: { checked: false } });
    f.change(row.inputs[0], true);
    f.component.getChangedRows()[0].is_deleted = false;
    assert.deepEqual(f.changes(), [{ id: 1, is_deleted: true }]);
});

test('フィルターなど表の編集欄以外の変更を無視する', () => {
    const f = fixture();
    f.root.listeners.get('click')({ target: { closest: () => null } });
    const outside = { dataset: { adminEditId: '99' } };
    const outsideControl = { closest: (selector) => selector.startsWith('button') ? outsideControl : outside };
    f.root.listeners.get('click')({ target: outsideControl });
    assert.deepEqual(f.changes(), []);
    assert.equal(f.component.hasUnsavedChanges, false);
});

test('未保存時だけ離脱を警告し、破棄時にイベントと変更内容を解放する', () => {
    const f = fixture();
    const row = f.row(1, { is_deleted: { checked: false } });
    let prevented = 0;
    const event = { preventDefault() { prevented++; } };
    f.window.listeners.get('beforeunload')(event);
    assert.equal(prevented, 0);
    f.change(row.inputs[0], true);
    f.window.listeners.get('beforeunload')(event);
    assert.equal(prevented, 1);
    assert.equal(event.returnValue, '');
    assert.equal(f.initialized(), 1);
    f.component.destroy();
    assert.equal(f.root.listeners.size, 0);
    assert.equal(f.window.listeners.size, 0);
    assert.deepEqual(f.changes(), []);
    assert.equal(f.destroyed(), 1);
});
