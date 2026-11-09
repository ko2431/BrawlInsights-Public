"""ミニゲーム型のプレゼント企画の管理（作成とコピー・番号・設定・公開・中止・還元率・規約・開始のお知らせ・状況）。

検討メモ notes_private/minigame_campaign_plan_2026-11-09.md の 7a。ゲームの抽選・演出・景品の付与は minigame_service、
絵柄の枠と検査は minigame_assets。事前応募型（giveaway_service・giveaway_prizes）の作りを流用し、番号は事前応募型と通し。
状態: draft（下書き）・scheduled（予約）・live（開催中）・ended（終了）・canceled（中止。is_invalid）。
"""
from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg

from app.core.logger import logger

# [この部分は公開用リポジトリでは非公開にされています]
