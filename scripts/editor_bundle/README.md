# アナウンス管理のエディターのビルド

`app/static/js/vendor/tiptap/tiptap.bundle.js`（アナウンス管理の編集画面だけで読む）を作る。

```
cd scripts/editor_bundle
npm install
npm run build
```

- 版は package.json で固定している。上げるときは Tiptap のパッケージの版をそろえる。
- ビルドしたら `app/static/js/vendor/tiptap/LICENSE.txt` の版の一覧も直し、`main.py` のバージョン番号を1つ上げる。
- 書き出したファイルはコミットする（本番ではビルドしない）。
