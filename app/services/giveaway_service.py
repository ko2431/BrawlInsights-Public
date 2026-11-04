"""事前応募型のプレゼント企画（トークンで応募する企画）の企画・応募・表示・管理画面。

検討メモ notes_private/giveaway_campaign_plan_2026-11-04.md。
- 応募は giveaway_entries（企画・ユーザー・UTC の日）に口数を足す。1日（UTC 0時で切り替え）と期間の上限は、同じタグを登録している
  アカウント全体でも共有する（main_account_reward_service。キーは giveaway:{id}:day と giveaway:{id}）。
- アカウントタブの「プレゼント企画」には、開催中・予告の企画と、当選発表から受け取りの期限までの結果の箱（応募した人だけ）を出す。
- 抽選は当面外部（notes_private/giveaways.py）。管理画面で応募者のリストを書き出し、当選者の ID を貼って確定する（giveaway_prizes）。
"""
from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import asyncpg

from app.core.cache import delete_cache, get_cache, set_cache
from app.core.logger import logger

# [この部分は公開用リポジトリでは非公開にされています]
