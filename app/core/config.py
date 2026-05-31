# app/core/config.py
import os
import secrets
from dotenv import load_dotenv
from pathlib import Path

# main.py から uvicorn で起動する場合、カレントディレクトリはプロジェクトルートになる想定
# .env ファイルのパスをプロジェクトルートからの相対パスで指定
# この config.py は app/core/ にあるので、2つ上の階層がプロジェクトルート
env_path = Path(__file__).resolve().parent.parent.parent / '.env'

# .env ファイルが存在すれば読み込む
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
    print(f"環境変数ファイルをロードしました: {env_path}") # デバッグ用
else:
    # .envファイルが見つからない場合の警告 (本番環境ではサーバーの環境変数が使われることを想定)
    print(f"警告: .env ファイルが見つかりませんでした: {env_path}")

class Settings:
    BRAWL_TOKEN: str | None = os.getenv("BRAWL_TOKEN")
    REVENUECAT_WEBHOOK_AUTH: str | None = os.getenv("REVENUECAT_WEBHOOK_AUTH", None)
    
    # セッション管理用のシークレットキーを追加
    SESSION_SECRET_KEY: str = os.getenv("SESSION_SECRET_KEY", secrets.token_hex(32)) # 環境変数になければランダム生成
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper() # 環境変数からログレベルを読み込み、デフォルトはINFO、大文字に統一
    
    # ログをローテーションするかどうか (本番環境では内部ではやらないのでオフ)
    TIMEDROTATING: bool = bool(os.getenv("TIMEDROTATING", False))
    
    # Redis設定を追加
    REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", 6379))
    REDIS_PASSWORD: str | None = os.getenv("REDIS_PASSWORD", None)
    REDIS_DB: int = int(os.getenv("REDIS_DB", 0))
    
    # PostgreSQL設定を追加
    DB_HOST: str = os.getenv("DB_HOST", "localhost")
    DB_PORT: int = int(os.getenv("DB_PORT", 5432))
    DB_USER: str = os.getenv("DB_USER", "brawl_insights_user")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", None)
    DB_NAME: str = os.getenv("DB_NAME", "brawl_insights_db")
    HOME_IP: str | None = os.getenv("HOME_IP")
    
    # セッションクッキーをHTTPS経由でのみ送信するかどうか
    SESSION_HTTPS_ONLY: bool = bool(os.getenv("SESSION_HTTPS_ONLY", False))
    
    # Googleアナリティクス設定
    GA_MEASUREMENT_ID: str | None = os.getenv("GA_MEASUREMENT_ID", None)

    # マルチサーバー・クラスタの設定
    SERVER_NAME: str = os.getenv("SERVER_NAME", "Main")
    # CLUSTER_NODES は '["http://ip:port", ...]' 形式のJSON文字列を想定
    CLUSTER_NODES: list[str] = []
    _cluster_nodes_raw: str | None = os.getenv("CLUSTER_NODES")
    
    # 内部通信用シークレットキー
    INTERNAL_API_SECRET: str | None = os.getenv("INTERNAL_API_SECRET")

    def __init__(self):
        if self._cluster_nodes_raw:
            try:
                import json
                self.CLUSTER_NODES = json.loads(self._cluster_nodes_raw)
            except Exception:
                self.CLUSTER_NODES = []
        if self.BRAWL_TOKEN is None:
            # 起動時にBRAWL_TOKENがない場合はエラーログを出し、プログラムの実行に影響が出ることを警告
            # main.py の logger が初期化された後にこのファイルが import されることを想定
            try:
                from app.core.logger import logger # 遅延インポート
                logger.critical("環境変数 BRAWL_TOKEN が設定されていません！API関連の機能が正しく動作しません。")
            except ImportError:
                print("CRITICAL: 環境変数 BRAWL_TOKEN が設定されていません！API関連の機能が正しく動作しません。(logger未初期化)")
                
        # SESSION_SECRET_KEY がデフォルト値で生成された場合に警告を出す (開発時のみ)
        if self.SESSION_SECRET_KEY == secrets.token_hex(32) and not os.getenv("SESSION_SECRET_KEY"):
            try:
                from app.core.logger import logger # 遅延インポート
                logger.warning("環境変数 SESSION_SECRET_KEY が設定されていません。開発用に一時的なキーを生成しましたが、本番環境では必ず固定のキーを設定してください。")
            except ImportError:
                print("WARNING: 環境変数 SESSION_SECRET_KEY が設定されていません。開発用に一時的なキーを生成しましたが、本番環境では必ず固定のキーを設定してください。(logger未初期化)")

settings = Settings()

# これで、他のファイルからは from app.core.config import settings でアクセスできます。
# 例: settings.BRAWL_TOKEN