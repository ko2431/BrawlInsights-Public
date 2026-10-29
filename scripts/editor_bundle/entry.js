// アナウンス管理のエディターで使う Tiptap の部品を1つの ES モジュールにまとめる。
// 版を上げるときは package.json の版をそろえて `npm install && npm run build` し、
// 出力（app/static/js/vendor/tiptap/）をコミットする。main.py のバージョン番号も上げる。
export { Editor, Extension, Mark, Node, mergeAttributes, getHTMLFromFragment } from '@tiptap/core';
export { StarterKit } from '@tiptap/starter-kit';
export { Document } from '@tiptap/extension-document';
export { Text } from '@tiptap/extension-text';
export { Bold } from '@tiptap/extension-bold';
export { Italic } from '@tiptap/extension-italic';
export { Strike } from '@tiptap/extension-strike';
export { Underline } from '@tiptap/extension-underline';
export { Link } from '@tiptap/extension-link';
export { Placeholder, UndoRedo } from '@tiptap/extensions';
export { TextSelection, NodeSelection } from '@tiptap/pm/state';
