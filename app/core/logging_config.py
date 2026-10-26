from fastapi import Request
import logging
import re
import colorlog
import time
import os
import datetime
from logging import FileHandler
from logging.handlers import TimedRotatingFileHandler

from app.core.config import settings

# ログビューアが1行=1件として扱うため、本文・例外メッセージ等に含まれる改行をエスケープする
_LOG_LINE_BREAK_RE = re.compile(r"[\r\n\v\f\x85\u2028\u2029]+")


def flatten_log_text(value: object) -> object:
    """ログ出力用に、文字列中の改行を \\n へ置換する。文字列以外はそのまま返す。"""
    if not isinstance(value, str):
        return value
    if not _LOG_LINE_BREAK_RE.search(value):
        return value
    return _LOG_LINE_BREAK_RE.sub(r"\\n", value)


class SingleLineLogFilter(logging.Filter):
    """LogRecord のメッセージと引数から改行を除去し、1レコードが複数行に割れないようにする。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = flatten_log_text(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: flatten_log_text(v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(flatten_log_text(a) for a in record.args)
            else:
                record.args = flatten_log_text(record.args)
        return True


def _attach_single_line_filter(logger: logging.Logger) -> None:
    logger.filters = [f for f in logger.filters if not isinstance(f, SingleLineLogFilter)]
    logger.addFilter(SingleLineLogFilter())


def _silence_websocket_framework_loggers():
    for logger_name in (
        "uvicorn.access",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.websockets_impl",
        "websockets",
        "websockets.server",
    ):
        logging.getLogger(logger_name).setLevel(logging.WARNING)

def setup_worker_logger():
    os.makedirs("logs", exist_ok=True)

    # タイムゾーンをJSTに変更（UTC+9）
    logging.Formatter.converter = time.localtime # これはJSTで表示するためのもので、ローテーションの時刻基準とは直接関係ないが、残しておいても良い

    # カラー対応のコンソールログフォーマット
    color_formatter = colorlog.ColoredFormatter(
        fmt="%(log_color)s[%(asctime)s] [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S", # 表示される時刻のフォーマット (JST)
        log_colors={
            'DEBUG': 'cyan',
            'INFO': 'green',
            'WARNING': 'yellow',
            'ERROR': 'red',
            'CRITICAL': 'bold_red,bg_white',
        }
    )

    # ファイル用（カラーなし）
    file_formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S" # 表示される時刻のフォーマット (JST)
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(color_formatter)

    #* ローテーション機能付きのファイルハンドラ
    if settings.TIMEDROTATING:
        file_handler = TimedRotatingFileHandler(
            filename="logs/app.log",    # ログファイル名
            when='midnight',            # 毎日深夜0時にローテーション ('D' から 'midnight' に変更すると atTime指定が不要かつ確実)
            interval=1,                 # 1日ごと
            backupCount=3650,           # 10年分のログファイルを保持
            encoding="utf-8",
            utc=False                   # Falseに設定してローカルタイム（JST）基準で0時を判定
            # atTime=datetime.time(0, 0, 0) # when='midnight' の場合、atTimeは通常不要（指定しても良いが、'midnight'が優先される）
                                        # もし when='D' を使う場合は atTime=datetime.time(0,0,0) と utc=False が重要
        )
    #* ローテーションなしのシンプルなファイルハンドラ
    else:
        file_handler = FileHandler(
            filename="logs/app.log",
            mode='a',
            encoding="utf-8"
        )
    file_handler.setFormatter(file_formatter)

    logger = logging.getLogger("brawl_insights")
    
    # 環境変数からログレベルを取得し、数値に変換
    log_level_str = settings.LOG_LEVEL
    numeric_level = getattr(logging, log_level_str, logging.INFO) # デフォルトはINFO
    logger.setLevel(numeric_level) # どのレベル以上のログを表示するか
    
    logger.handlers = [] # 既存のハンドラをクリア（二重登録防止のより確実な方法）
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    _attach_single_line_filter(logger)
    # [この部分は公開用リポジトリでは非公開にされています]
    _silence_websocket_framework_loggers()

    # JSTで動作していることを確認するためのログ（オプション）
    logger.info(f"ロガーはJST (UTC+9) でセットアップされました。ログローテーションは毎日JST午前0時に行われます。現在の時刻 (JST): {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    return logger

def setup_logger():
    os.makedirs("logs", exist_ok=True)

    # タイムゾーンをJSTに変更（UTC+9）
    logging.Formatter.converter = time.localtime # これはJSTで表示するためのもので、ローテーションの時刻基準とは直接関係ないが、残しておいても良い

    # カラー対応のコンソールログフォーマット
    color_formatter = colorlog.ColoredFormatter(
        fmt="%(log_color)s[%(asctime)s] [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S", # 表示される時刻のフォーマット (JST)
        log_colors={
            'DEBUG': 'cyan',
            'INFO': 'green',
            'WARNING': 'yellow',
            'ERROR': 'red',
            'CRITICAL': 'bold_red,bg_white',
        }
    )

    # ファイル用（カラーなし）
    file_formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S" # 表示される時刻のフォーマット (JST)
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(file_formatter)

    logger = logging.getLogger("brawl_insights")
    
    # 環境変数からログレベルを取得し、数値に変換
    log_level_str = settings.LOG_LEVEL
    numeric_level = getattr(logging, log_level_str, logging.INFO) # デフォルトはINFO
    logger.setLevel(numeric_level) # どのレベル以上のログを表示するか
    
    logger.handlers = [] # 既存のハンドラをクリア（二重登録防止のより確実な方法）
    logger.addHandler(console_handler)
    _attach_single_line_filter(logger)
    # [この部分は公開用リポジトリでは非公開にされています]
    _silence_websocket_framework_loggers()

    # JSTで動作していることを確認するためのログ（オプション）
    logger.info(f"ロガーはJST (UTC+9) でセットアップされました。現在の時刻 (JST): {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    return logger

def add_log_info(request: Request, info: str):
    """リクエスト処理中に追加情報をログに追加するための関数"""
    if not hasattr(request.state, "extra_log_info"):
        request.state.extra_log_info = []
    request.state.extra_log_info.append(info)

def get_log_extra_info(request: Request):
    """現在のリクエストに関連する追加情報を取得"""
    if hasattr(request.state, "extra_log_info"):
        return " | ".join(request.state.extra_log_info)
    return ""
