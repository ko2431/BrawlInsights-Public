---
description: Brawl InsightsのフロントエンドをヘッドレスChromium(Playwright)で開き、表示・コンソールエラーを確認する。フロントエンドの変更確認、表示崩れチェック、ログイン後の画面確認、JA/EN・ライト/ダークモードの見た目確認をしたいときに使う。
alwaysApply: false
---

# Brawl Insights ブラウザ動作確認

開発サーバー(`uvicorn app.main:app --reload`)は原則として常にポート8000で起動済み([CLAUDE.md](../../../CLAUDE.md)参照)。まずそれを前提に、起動確認してから進める。

## 1. サーバー生存確認

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/ja/
```

200/307以外が返る場合や接続できない場合のみ、以下で起動する(基本的には起動済みのはず):

```bash
cd /Users/kosuke/BrawlInsights
nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --lifespan on > /tmp/brawl_uvicorn.log 2>&1 &
timeout 30 bash -c 'until curl -sf http://localhost:8000/ja/ >/dev/null; do sleep 1; done'
```

## 2. ブラウザで開いてスクリーンショット

Playwrightはプロジェクトの devDependency としてインストール済み(`package.json`)。Chromiumバイナリも `~/Library/Caches/ms-playwright` にキャッシュ済みなので、追加インストールは不要。

再利用可能なドライバースクリプト: [scripts/shot.js](scripts/shot.js)

```bash
cd /Users/kosuke/BrawlInsights
node .claude/skills/brawl-browser-test/scripts/shot.js /ja/ /tmp/shot.png
```

オプション(環境変数):
- `THEME=dark|light|auto` — ダークモード/ライトモードの見た目確認(`localStorage.brawlInsightsTheme` を模倣)
- `USERNAME=こうすけ PASSWORD=12345678` — ログイン状態での画面確認(開発環境の管理者アカウント。他のテストアカウントもパスワードは全て `12345678`)
- `VIEWPORT=mobile` — スマホ幅(390x844)での表示確認
- `FULL_PAGE=1` — ページ全体のスクリーンショット
- `BASE_URL=...` — デフォルトは `http://localhost:8000`

例:
```bash
# 英語版・ダークモード・ログイン状態でアカウントページを確認
BASE_URL=http://localhost:8000 THEME=dark USERNAME=こうすけ PASSWORD=12345678 \
  node .claude/skills/brawl-browser-test/scripts/shot.js /en/account /tmp/account_en_dark.png
```

スクリプトは標準出力に `status`, `title`, `console errors` を出す。`console errors` が空配列でなければ画面が壊れている可能性が高いので、**必ず確認する**こと(終了コードも1になる)。

## 3. 結果の確認

スクリーンショットは Read ツールで画像として直接確認できる。テキストのcurl確認だけで済ませず、実際に画像を見て表示崩れがないか判断すること。

## 補足

- 対話操作(クリック・フォーム入力など)が必要な場合は `shot.js` を都度カスタマイズするか、直接 `node -e "..."` でPlaywrightスクリプトを書いて実行する。使い回せそうな操作は `scripts/` に追加してよい。
- キャッシュバスティングのためCSS/JSを変更したら `app/main.py` のバージョン番号を1つ上げること([CLAUDE.md](../../../CLAUDE.md)参照)。`--reload` 中なら保存だけで自動反映される。
