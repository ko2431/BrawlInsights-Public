"""アカウントタブの「プレゼント企画」のミニゲーム型の箱（開催中・管理者の先行プレイ・予告・終了後の手続き）と参加ボーナス。

検討メモ notes_private/minigame_campaign_plan_2026-11-09.md の 7b。企画ごとに箱を出す（同時開催 MD6）。参加の数・上限・ボーナス・ゲージ（MD32・MD34）、
割引参加の残り（企画ごと。同じタグのアカウントで共有 M6）、終了後のギフト・特別報酬リンクの手続き（当選から72時間 MD20・MD25）。
抽選・演出・景品の付与は minigame_service、企画の設定は minigame_campaign_service。
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg

from app.core.logger import logger

# [この部分は公開用リポジトリでは非公開にされています]
