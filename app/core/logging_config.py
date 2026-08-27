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

# [この部分は公開用リポジトリでは非公開にされています]
    _silence_websocket_framework_loggers()

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
