"""掲示板企画の確定・抽選・景品と受け取り・当選発表（5b）。

検討メモ notes_private/board_campaign_plan_2026-10-31.md の 7章（景品）・9章（抽選）・11章（お知らせとの連携）・10.3（管理画面）。
流れ: 集計を確定（確定賞の山分けと参加賞の行を作る）→ 抽選（上の等級から口数の重みで1人1つまで。乱数の種を記録）→
当選者の確認（除外して引き直す）→ 当選者を確定 → 当選発表のお知らせの下書きを作る（役目のテンプレート）→
お知らせの公開で受け取りが始まり、応募した全員に共通の通知が届く（当選を直接知らせる通知はしない。BD14）。
アプリ内の景品は本人がアカウントタブで受け取る（自動では付与しない。BD20）。ギフトはメールで連絡してもらい、運営が状態を付ける。
"""
from __future__ import annotations

import html
import random
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg

from app.core.logger import logger
from app.services import board_campaign_service as bcs

# [この部分は公開用リポジトリでは非公開にされています]
