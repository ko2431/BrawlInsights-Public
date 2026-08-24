"""WARNING以上のログを管理者向け通知として非同期に記録する。"""
from __future__ import annotations

import asyncio
import logging
import traceback
from typing import Any

# [この部分は公開用リポジトリでは非公開にされています]
