"""事前応募型企画の当選者・景品と受け取り・お知らせ（開始のお知らせと当選発表）。

検討メモ notes_private/giveaway_campaign_plan_2026-11-04.md の 6章・7章・8章。抽選は当面外部（notes_private/giveaways.py）。
流れ: 終了 → 管理画面で応募者のリストを書き出して抽選 → 等級ごとに当選者の ID を貼って確かめる → 当選者を確定（応募者の写し・
抽選の景品・参加賞の行を作る）→ 当選発表のお知らせの下書きを作る（役目のテンプレート）→ お知らせの公開で受け取りが始まる。
アプリ内の景品は本人がアカウントタブで受け取る（自動では付与しない。掲示板企画と同じ）。ギフトはメールで連絡してもらい、運営が状態を付ける。
結果の通知は送らない（お知らせのプッシュ・ホームのお知らせが兼ねる。GD19）。
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg

from app.core.logger import logger
from app.services import giveaway_service as gs

# [この部分は公開用リポジトリでは非公開にされています]
