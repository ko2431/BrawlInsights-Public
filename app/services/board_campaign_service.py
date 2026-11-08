"""掲示板企画（毎月のプレゼント企画）の集計と表示。

検討メモ notes_private/board_campaign_plan_2026-10-31.md（5〜8章・10章）。応募は元の表（投稿・メッセージ・いいね・
リアクション・大会・警告）から数え直す。掲示板の処理には手を入れない（本人の表示のキャッシュを消すだけ）。
- worker（10分ごと。recount_due_campaigns）: 日付が変わったら、いいね・リアクションの前の日の分を、その時点で残っている
  ものから写して確定する（取り消すと元の行が消えるため）。終了したら、ほかの方法の分も写す（ここから集計中。終了後の削除は
  効かない）。全員の口数・確定賞・無効を数え直して board_campaign_scores と企画の stats に書く。
- 本人の表示（掲示板の上部の帯・アカウントタブ）: 最後の集計の日（counted_day）より前の分は scores の days_before から、
  その日からの分は、その人の行だけを同じ SQL で数えて足す（60秒キャッシュ。投稿・メッセージ・いいね・リアクションをしたら
  touch_user で消す）。確定賞は10分ごとの集計のもの。
- 警告（管理者の対応の warning・penalty が投稿・メッセージに付いたもの）は、数えるときに、その投稿・メッセージを作った日の、
  その方法の分を0にする（終了後に付いたものも、集計の確定まで効く）。
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from itertools import groupby
from datetime import date, datetime, timedelta, timezone
from typing import Any

import asyncpg

from app.core.cache import delete_cache, get_cache, set_cache
from app.core.logger import logger

_USER_CACHE_KEY = "board_campaign:user:{}"


async def touch_user(user_id: int | None) -> None:
    """投稿・メッセージ・いいね・リアクションをしたときに、本人の表示のキャッシュを消す（すぐ「今日の分 ✓」が付くように）。"""
    if user_id:
        await delete_cache(_USER_CACHE_KEY.format(user_id))


# [この部分は公開用リポジトリでは非公開にされています]
