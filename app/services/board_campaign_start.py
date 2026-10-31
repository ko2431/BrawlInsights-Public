"""掲示板企画の開始のお知らせ（5c）。

検討メモ notes_private/board_campaign_plan_2026-10-31.md の 11章。役目のテンプレート「プレゼント企画 告知（掲示板企画・自動生成）」
（settings.role = board_campaign_start）のタイトル・冒頭の紹介の置き換えの印に企画の値を入れ、
本文の印の段落に、企画の設定から作った景品（抽選・参加賞）・応募方法・確定賞・期間・当選発表・応募規約を入れて下書きを作る。
文面は 2026年11月の開始のお知らせ（notes_public/giveaways_news_promotion_board_202611.txt。ユーザーが直したもの）が元。
その月だけの記載（リニューアル・NEW など）は、作った下書きで手で足す。連携元は board_campaign_start・企画の id。
"""
from __future__ import annotations

import html
import re
from typing import Any

import asyncpg

from app.core.logger import logger
from app.services import board_campaign_service as bcs

# [この部分は公開用リポジトリでは非公開にされています]
