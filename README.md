# Brawl Insights

Brawl Insightsは、人気モバイルゲーム「ブロスタ」のデータ分析・便利ツールなどを提供するWeb・iOSアプリケーションです。
個人で開発・運営しています。

- **Web版**: https://brawlinsights.com/ja/
- **iOS版**: https://apps.apple.com/app/id6751238098

## 公開リポジトリに関する注意事項

本リポジトリは、ポートフォリオ・実績公開用として整備した**公開用リポジトリ**です。
競合アプリへの対策のため、一部のコードブロック・変数値などは、スクリプトにより自動的に以下のコメントに置換されています。

```text
[この部分は公開用リポジトリでは非公開にされています]
```

また、一部のディレクトリやファイルも公開対象外としています。そのため、本番環境のソースコードとは完全には一致せず、そのままではローカル環境でのビルドや起動が難しい点をご了承ください。

## 実績・トラフィック
本アプリは2025年7月のリリース以降、多くのユーザーにご利用いただいています。（※データは2026年7月時点）

- **累計ページビュー（PV）**: 3,500万 PV以上
- **月間ページビュー（PV）**: 約 516万 PV
- **月間アクティブユーザー（MAU）**: 約 24.0万人

    ※上記はGoogle アナリティクスのデータ。実際にはこれよりさらに数十%多いユーザーにご利用いただいています。

- **iOSアプリ版ダウンロード数**: 11万 DL以上
- **iOSアプリ版評価**: 4.7
- **App Store最高順位**: 7位 (ユーティリティカテゴリ)

- **任意のアカウント登録者数**: 6万人以上

## 主な機能

- **30以上のコンテンツ・便利ツール**: ブロスタプレイヤーの様々なニーズに応えるコンテンツやツールを多数提供。
  - **戦績データの確認**: 表やグラフで、戦績データをわかりやすく確認可能。
  - **ランキング**: 独自に集計したランキングも含め、15種類以上のゲームに関するさまざまなランキングを提供。
  - **キャラクターの提案ツール**: 毎日集計した多くのバトル履歴データをもとに、バトルで選択すべきキャラクターをリアルタイムで提案するツール。
  - **プロフィール画像生成ツール**: プレイヤーを選択するだけで、SNSでの共有や記録用に使える戦績データをまとめた画像を生成するツール。
  - **各種便利ツール**: 育成計算機や報酬量計算機など、さまざまな便利ツール。
  - **掲示板機能**: チャット機能を備えた本格的な掲示板。
- **多言語対応**: 日本語・英語の2言語に対応。
- **クロスプラットフォーム**: Webブラウザだけでなく、Capacitorを利用したiOS向けのWebView型ハイブリッドアプリとしてもリリース。

## 使用技術

![Python](https://img.shields.io/badge/Python-3.13-blue?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-316192?logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-D82C20?logo=redis&logoColor=white)
![Ubuntu](https://img.shields.io/badge/Ubuntu-24.04-E95420?logo=ubuntu&logoColor=white)
![Nginx](https://img.shields.io/badge/Nginx-009639?logo=nginx&logoColor=white)
![Cloudflare](https://img.shields.io/badge/Cloudflare-F38020?logo=cloudflare&logoColor=white)
![Capacitor](https://img.shields.io/badge/Capacitor-119EFF?logo=capacitor&logoColor=white)
![HTML5](https://img.shields.io/badge/HTML5-E34F26?logo=html5&logoColor=white)
![CSS3](https://img.shields.io/badge/CSS3-1572B6?logo=css3&logoColor=white)

- **バックエンド**: Python 3.13, FastAPI
- **データベース**: PostgreSQL, Redis
- **フロントエンド**: HTML5, CSS3, Vanilla JS, Jinja2 (サーバーサイドレンダリング)
- **インフラ/サーバー**: Kagoya Cloud VPS (Ubuntu 24.04), Nginx, Uvicorn, Cloudflare
- **モバイルアプリ**: Capacitor (iOSハイブリッドアプリ)

## インフラアーキテクチャ

本番環境は、以下のような親サーバー・子サーバーの構成で負荷分散を行い稼働しています。

- **親サーバー** (メモリ16GB / CPU 8コア / NVMe SSD 1000GB)
  - Nginxをリバースプロキシとして配置
  - アプリへのアクセスの **10%** を処理
- **子サーバー** (メモリ8GB / CPU 6コア / NVMe SSD 800GB)
  - アプリケーションのプロセス専任
  - アプリへのアクセスの **90%** を処理

## ディレクトリ構成

主要なディレクトリ構成は以下の通りです。

```text
.
├── app/                  # FastAPI アプリケーション本体
│   ├── main.py           # エントリーポイント
│   ├── routers/          # ルーティング (APIエンドポイント)
│   ├── services/         # ビジネスロジック
│   ├── models/           # データベースモデル
│   ├── templates/        # Jinja2 HTMLテンプレート
│   ├── static/           # 静的ファイル (CSS, JS, 画像など)
│   ├── core/             # コア設定・環境設定
│   ├── db/               # データベース接続管理
│   ├── utils/            # 共通ユーティリティ関数
│   └── background_tasks/ # バックグラウンド処理
├── alembic/              # データベースマイグレーション
├── ios/                  # iOSネイティブプロジェクト (Capacitor)
├── tests/                # テストコード (pytest)
├── scripts/              # ユーティリティスクリプト (公開リポジトリ用変換スクリプト等)
├── worker.py             # ワーカープロセス（バックグラウンドタスク実行用）
├── image_worker.py       # 画像ワーカープロセス（画像生成タスク実行用）
├── requirements.txt      # 依存パッケージ
└── README.md             # 本ドキュメント
```

## 今後の展望・やりたいこと

- **新機能の追加**: ユーザーからのフィードバックやアンケート結果をもとに、新たな機能やUIの改善を多数構想中です。
- **管理画面の改善**: 管理画面はすでに独自で作っていますが、運営負荷を下げるためにさらなる強化を進めていく予定です。
- **Android版アプリの開発**: ユーザーからの多くの要望を受け、iOS版に加えてAndroid版のアプリの開発も構想中です。
- **テストコードの充実**: 現在 `tests/` 内のテストコード（pytest）を導入したばかりのため未完成となっており、今後整備を進めていく予定です。
- **パフォーマンス最適化**: アクセス増加に備え、さらなるキャッシュ戦略の強化やDB周りの最適化等を実施予定です。

## 外部リンク

- **Web版**: https://brawlinsights.com/ja/
- **iOS版**: https://apps.apple.com/app/id6751238098

- **バージョン履歴**: https://brawlinsights.com/ja/help/version_history
- **公式X**: https://x.com/BrawlInsights
- **公式Discord**: https://discord.com/servers/brawl-insights-1129770095158239272