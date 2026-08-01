"""ミニゲームの抽選、景品付与、管理用サービス。"""

from __future__ import annotations

import copy
import datetime
import json
import random
from typing import Any

import asyncpg

from app.core.cache import delete_cache, get_cache, set_cache
from app.core.config import settings
from app.core.logger import logger
from app.services.brawl_service import (
    add_auto_tracking_time,
    extend_battle_log_retention,
    get_battle_log_retention_months,
    get_player_from_db,
)
from app.services.minigame_assets import BACK_SYMBOL, CARD_ASSETS, MYSTERY_IMAGE, static_url_path
from app.services.user_service import User, _current_token_claim_date

DEFAULT_PRICE_AD_TOKENS = 7
DEFAULT_PRICE_TOKEN_TOKENS = 20
DEFAULT_AD_DAILY_LIMIT = 5
AD_SKIP_TICKET_COST = 2
MINIGAME_AD_PLAY_CUTOFF_SECONDS = 60
USER_HISTORY_LIMIT = 10
MAX_PRIZE_TIERS = 6
MIN_PRIZE_TIERS = 2
RETENTION_COMPENSATION_TOKENS_PER_MONTH = 50
MAX_BATTLE_LOG_RETENTION_MONTHS = 120
DEFAULT_EXPECTED_TOTAL_PLAYS = 500
GAME_TYPES = ("card_flip_single", "card_flip_multi1", "card_flip_multi2", "scratch1")

ACTIVE_CAMPAIGN_CACHE_TTL = 60

_RNG = random.SystemRandom()


def _message(lang: str, ja: str, en: str) -> str:
    return ja if lang == "ja" else en


def _as_utc(dt: datetime.datetime) -> datetime.datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def is_within_ad_play_cutoff(
    ends_at: datetime.datetime,
    *,
    now: datetime.datetime | None = None,
    cutoff_seconds: int = MINIGAME_AD_PLAY_CUTOFF_SECONDS,
) -> bool:
    """広告視聴が必要な ad 参加を締め切る終了直前の猶予かどうか。"""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return (_as_utc(ends_at) - now).total_seconds() <= cutoff_seconds


def ad_play_requires_rewarded_ad(*, is_delete_ads: bool, can_spend_tickets: bool) -> bool:
    """割引 ad 参加でリワード広告の視聴が必要か（広告削除・チケットスキップ時は不要）。"""
    return not is_delete_ads and not can_spend_tickets


_AD_PLAY_CUTOFF_MESSAGE_JA = (
    "まもなく企画が終了するため、広告視聴による参加はできません。ご了承ください。"
)
_AD_PLAY_CUTOFF_MESSAGE_EN = (
    "Ad-based entry is unavailable because the event is ending soon. Thank you for your understanding."
)


def _record(row: asyncpg.Record | dict[str, Any]) -> dict[str, Any]:
    return dict(row)


def _parse_json_field(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _campaign_for_cache(campaign: dict[str, Any] | None) -> dict[str, Any] | None:
    """datetime を含む企画行を Redis 保存用に正規化する。"""
    if campaign is None:
        return None
    out = dict(campaign)
    for key, value in list(out.items()):
        if isinstance(value, datetime.datetime):
            out[key] = value.isoformat()
        elif isinstance(value, datetime.date):
            out[key] = value.isoformat()
    out["prizes"] = _parse_json_field(out.get("prizes"))
    return out


def _campaign_from_cache(cached: dict[str, Any] | None) -> dict[str, Any] | None:
    """キャッシュから読み出した企画を実行時オブジェクトに戻す。"""
    if cached is None:
        return None
    out = dict(cached)
    for key in ("starts_at", "ends_at", "created_at", "updated_at"):
        value = out.get(key)
        if isinstance(value, str):
            out[key] = datetime.datetime.fromisoformat(value)
    out["prizes"] = _parse_json_field(out.get("prizes"))
    return out


def resolve_prices(campaign_row: Any) -> tuple[int, int, int]:
    """企画の未設定価格を標準値で補う。"""
    get = campaign_row.get if isinstance(campaign_row, dict) else campaign_row.__getitem__
    return (
        get("price_ad_tokens") or DEFAULT_PRICE_AD_TOKENS,
        get("price_token_tokens") or DEFAULT_PRICE_TOKEN_TOKENS,
        get("ad_daily_limit") or DEFAULT_AD_DAILY_LIMIT,
    )


def validate_prizes(prizes: dict) -> list[str]:
    """景品定義を管理画面向けに検証する。"""
    errors: list[str] = []
    tiers = prizes.get("tiers") if isinstance(prizes, dict) else None
    if not isinstance(tiers, list) or not MIN_PRIZE_TIERS <= len(tiers) <= MAX_PRIZE_TIERS:
        return ["景品階層は2～6件で設定してください。"]
    ranks = [tier.get("rank") for tier in tiers if isinstance(tier, dict)]
    if (
        len(ranks) != len(tiers)
        or any(not isinstance(rank, int) for rank in ranks)
        or sorted(ranks) != list(range(1, len(tiers) + 1))
    ):
        errors.append("景品ランクは1から連続した番号で設定してください。")
    weighted = 0
    for tier in tiers:
        if not isinstance(tier, dict):
            errors.append("景品階層の形式が不正です。")
            continue
        allocation, quantity = tier.get("allocation"), tier.get("quantity")
        if allocation not in {"stock", "weight"}:
            errors.append(f"ランク{tier.get('rank', '?')}の配分方式が不正です。")
        elif allocation == "stock":
            if not isinstance(quantity, int) or quantity <= 0:
                errors.append(f"ランク{tier.get('rank', '?')}の在庫数は1以上の整数で設定してください。")
        elif not isinstance(quantity, (int, float)) or isinstance(quantity, bool) or quantity <= 0:
            errors.append(f"ランク{tier.get('rank', '?')}の重みは0より大きい値で設定してください。")
        if allocation == "weight":
            weighted += 1
        probability = tier.get("max_probability")
        if probability is not None and (
            not isinstance(probability, (int, float)) or isinstance(probability, bool) or not 0 <= probability <= 1
        ):
            errors.append(f"ランク{tier.get('rank', '?')}の最大確率は0～1で設定してください。")
        items = tier.get("items")
        if not isinstance(items, list) or not items:
            errors.append(f"ランク{tier.get('rank', '?')}には景品を設定してください。")
        elif any(not isinstance(item, dict) or not item.get("type") for item in items):
            errors.append(f"ランク{tier.get('rank', '?')}の景品形式が不正です。")
    if not weighted:
        errors.append("少なくとも1つは重み抽選の景品階層を設定してください。")
    for tier in tiers:
        items = tier.get("items") if isinstance(tier, dict) else None
        if isinstance(items, list) and any(item.get("type") == "none" for item in items if isinstance(item, dict)):
            if tier.get("rank") != len(tiers) or len(items) != 1:
                errors.append("「なし」は最低ランクの唯一の景品としてのみ設定できます。")
    return errors


async def get_display_campaign(db: asyncpg.Connection, *, user_is_admin: bool) -> dict[str, Any] | None:
    """表示可能な企画を取得する。"""
    audience = "admin" if user_is_admin else "user"
    key = f"minigame:display_campaign:{audience}"
    cached = await get_cache(key)
    if cached is not None:
        return _campaign_from_cache(cached)
    row = await db.fetchrow(
        """SELECT * FROM minigame_campaigns
           WHERE NOT is_invalid AND starts_at <= now() AND ends_at >= now()
           ORDER BY starts_at DESC LIMIT 1"""
    )
    if row is None and user_is_admin:
        row = await db.fetchrow(
            """SELECT * FROM minigame_campaigns
               WHERE NOT is_invalid AND starts_at > now()
               ORDER BY starts_at ASC LIMIT 1"""
        )
    result = _record(row) if row else None
    if result is not None:
        result["prizes"] = _parse_json_field(result.get("prizes"))
    await set_cache(key, _campaign_for_cache(result), ACTIVE_CAMPAIGN_CACHE_TTL)
    return result


async def get_user_pending_play(db: asyncpg.Connection, user_id: int) -> dict[str, Any] | None:
    row = await db.fetchrow(
        """SELECT p.*, c.name_ja, c.name_en, c.game_type FROM minigame_plays p
           JOIN minigame_campaigns c ON c.id = p.campaign_id
           WHERE p.user_id = $1 AND p.status = 'pending_reveal' AND c.ends_at >= now()
           ORDER BY p.created_at DESC LIMIT 1""",
        user_id,
    )
    if not row:
        return None
    play = _record(row)
    play["result_prizes"] = _parse_json_field(play.get("result_prizes"))
    play["animation_payload"] = _parse_json_field(play.get("animation_payload"))
    play["grant_log"] = _parse_json_field(play.get("grant_log"))
    return play


async def get_user_play_history(
    db: asyncpg.Connection, user_id: int, limit: int = USER_HISTORY_LIMIT
) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 100))
    rows = await db.fetch(
        """SELECT p.*, c.name_ja, c.name_en, c.game_type FROM minigame_plays p
           JOIN minigame_campaigns c ON c.id = p.campaign_id
           WHERE p.user_id = $1 AND p.status = 'completed'
           ORDER BY p.created_at DESC LIMIT $2""",
        user_id, limit,
    )
    plays: list[dict[str, Any]] = []
    for row in rows:
        play = _record(row)
        play["result_prizes"] = _parse_json_field(play.get("result_prizes"))
        play["grant_log"] = _parse_json_field(play.get("grant_log"))
        play["animation_payload"] = _parse_json_field(play.get("animation_payload"))
        plays.append(play)
    return plays


async def count_campaign_stats(db: asyncpg.Connection, campaign_id: int) -> dict[str, int]:
    row = await db.fetchrow(
        """SELECT count(DISTINCT user_id) FILTER (WHERE NOT is_admin_play) AS users,
                  count(*) FILTER (WHERE NOT is_admin_play) AS plays
           FROM minigame_plays WHERE campaign_id = $1""",
        campaign_id,
    )
    return {"users": row["users"] or 0, "plays": row["plays"] or 0}


async def estimate_remaining_plays(db: asyncpg.Connection, campaign: dict[str, Any]) -> float:
    """残りプレイ数を外挿する（非管理者のみ・前回企画参照あり）。"""
    now = datetime.datetime.now(datetime.timezone.utc)
    starts_at = campaign["starts_at"]
    ends_at = campaign["ends_at"]
    if starts_at.tzinfo is None:
        starts_at = starts_at.replace(tzinfo=datetime.timezone.utc)
    if ends_at.tzinfo is None:
        ends_at = ends_at.replace(tzinfo=datetime.timezone.utc)
    remaining_seconds = max(0.0, (ends_at - now).total_seconds())
    elapsed_seconds = max(60.0, (now - starts_at).total_seconds())

    plays_so_far = await db.fetchval(
        """SELECT count(*) FROM minigame_plays
           WHERE campaign_id = $1 AND NOT is_admin_play""",
        campaign["id"],
    ) or 0

    extrapolated = None
    if plays_so_far >= 10 and elapsed_seconds >= 3600:
        rate = plays_so_far / elapsed_seconds
        extrapolated = rate * remaining_seconds

    previous = await db.fetchrow(
        """SELECT c.id, c.starts_at, c.ends_at,
                  (SELECT count(*) FROM minigame_plays p
                   WHERE p.campaign_id = c.id AND NOT p.is_admin_play) AS plays
           FROM minigame_campaigns c
           WHERE c.id <> $1 AND c.ends_at < now() AND NOT c.is_invalid
           ORDER BY c.ends_at DESC LIMIT 1""",
        campaign["id"],
    )
    previous_estimate = None
    if previous and previous["plays"]:
        prev_start, prev_end = previous["starts_at"], previous["ends_at"]
        if prev_start.tzinfo is None:
            prev_start = prev_start.replace(tzinfo=datetime.timezone.utc)
        if prev_end.tzinfo is None:
            prev_end = prev_end.replace(tzinfo=datetime.timezone.utc)
        duration = max(60.0, (prev_end - prev_start).total_seconds())
        previous_estimate = (previous["plays"] / duration) * remaining_seconds

    expected_total = campaign.get("expected_total_plays")
    expected_remaining = None
    if expected_total:
        expected_remaining = max(1.0, float(expected_total) - float(plays_so_far))

    for candidate in (extrapolated, previous_estimate, expected_remaining):
        if candidate is not None and candidate > 0:
            return float(candidate)
    return float(DEFAULT_EXPECTED_TOTAL_PLAYS)


async def draw_prize_rank(
    db: asyncpg.Connection, campaign: dict[str, Any], *, is_admin_play: bool
) -> tuple[int, dict[str, Any]]:
    """stock は推定残りプレイ数ベース、外れは weight 比重。max_probability でキャップ。"""
    tiers = sorted(campaign["prizes"]["tiers"], key=lambda tier: tier["rank"])
    stock_rows = await db.fetch(
        """SELECT rank, remaining FROM minigame_prize_stocks
           WHERE campaign_id = $1 FOR UPDATE""",
        campaign["id"],
    )
    remaining = {row["rank"]: row["remaining"] for row in stock_rows}
    estimated = max(1.0, await estimate_remaining_plays(db, campaign))

    stock_probs: list[tuple[dict[str, Any], float]] = []
    for tier in tiers:
        if tier.get("allocation") != "stock":
            continue
        left = remaining.get(tier["rank"], 0)
        if left <= 0 and not is_admin_play:
            continue
        use_left = left if not is_admin_play else max(left, tier["quantity"])
        p = use_left / estimated
        cap = tier.get("max_probability")
        if cap is not None:
            p = min(p, float(cap))
        if p > 0:
            stock_probs.append((tier, p))

    total_stock_p = sum(p for _, p in stock_probs)
    if total_stock_p > 0.95:
        scale = 0.95 / total_stock_p
        stock_probs = [(tier, p * scale) for tier, p in stock_probs]
        total_stock_p = 0.95

    roll = _RNG.random()
    chosen: dict[str, Any] | None = None
    cursor = 0.0
    for tier, p in stock_probs:
        cursor += p
        if roll <= cursor:
            chosen = tier
            break

    if chosen is None:
        weights = [tier for tier in tiers if tier.get("allocation") == "weight"]
        if not weights:
            raise RuntimeError("No weight prizes available")
        total_w = sum(float(tier["quantity"]) for tier in weights)
        w_roll = _RNG.uniform(0, total_w)
        cursor_w = 0.0
        chosen = weights[-1]
        for tier in weights:
            cursor_w += float(tier["quantity"])
            if w_roll <= cursor_w:
                chosen = tier
                break

    if chosen["allocation"] == "stock" and not is_admin_play:
        updated = await db.fetchrow(
            """UPDATE minigame_prize_stocks SET remaining = remaining - 1
               WHERE campaign_id = $1 AND rank = $2 AND remaining > 0
               RETURNING remaining""",
            campaign["id"],
            chosen["rank"],
        )
        if not updated:
            return await draw_prize_rank(db, campaign, is_admin_play=False)

    return chosen["rank"], copy.deepcopy({"rank": chosen["rank"], "items": chosen["items"]})


def _card(position: int, face: str, *, gold: bool = False, rank: int | None = None) -> dict[str, Any]:
    return {"position": position, "face": static_url_path(face), "back": static_url_path(BACK_SYMBOL), "gold": gold, "rank": rank}


def _combination_result_faces(
    *,
    tier_count: int,
    result_rank: int,
    ranks: dict[int, str],
    decoys: list[str],
    pool: list[str],
) -> list[str]:
    """組み合わせ型（multi2 / scratch1）の結果3絵柄を相対パスで返す。"""
    if tier_count <= 3:
        face_pool = pool
        if result_rank == 1:
            face = _RNG.choice(face_pool)
            return [face, face, face]
        if result_rank == 2:
            face = _RNG.choice(face_pool)
            other = _RNG.choice([f for f in face_pool if f != face] or face_pool)
            faces = [face, face, other]
            _RNG.shuffle(faces)
            return faces
        picks = list(face_pool)
        _RNG.shuffle(picks)
        faces = picks[:3]
        while len(set(faces)) < 3 and len(face_pool) >= 3:
            _RNG.shuffle(picks)
            faces = picks[:3]
        if len(set(faces)) < 3:
            faces = [face_pool[0], face_pool[1 % len(face_pool)], face_pool[2 % len(face_pool)]]
        return faces

    r1 = ranks[1]
    r2 = ranks.get(2)
    r3 = ranks.get(3)
    decoy = decoys[0] if decoys else r1

    def no_decoy_triple(fs: list[str]) -> list[str]:
        if decoys and fs.count(fs[0]) == 3 and fs[0] in decoys:
            fs[2] = r1 if r1 not in decoys else (r2 or r1)
        return fs

    if result_rank == 1:
        faces = [r1, r1, r1]
    elif result_rank == 2 and r2:
        faces = [r2, r2, r2]
    elif result_rank == 3 and tier_count >= 5 and r3:
        faces = [r3, r3, r3]
    elif (tier_count == 4 and result_rank == 3) or (tier_count == 5 and result_rank == 4) or (
        tier_count == 6 and result_rank == 5
    ):
        face = _RNG.choice([r1, r2, decoy] if r2 else [r1, decoy])
        if tier_count == 6 and face == r1:
            face = _RNG.choice([x for x in [r2, r3, decoy] if x] or [decoy])
        other = _RNG.choice([x for x in [r1, r2, r3, decoy] if x and x != face] or [decoy])
        faces = [face, face, other]
        _RNG.shuffle(faces)
    elif tier_count == 6 and result_rank == 4:
        other = _RNG.choice([x for x in [r2, r3, decoy] if x] or [decoy])
        faces = [r1, r1, other]
        _RNG.shuffle(faces)
    else:
        opts = [x for x in [r1, r2, r3, *decoys] if x]
        _RNG.shuffle(opts)
        faces = opts[:3]
        while len(faces) < 3:
            faces.append(decoy)
        if len(set(faces)) < 3:
            faces = [r1, r2 or decoy, decoy if decoy != r1 else (r2 or r1)]
    return no_decoy_triple(faces)


def build_animation_payload(
    game_type: str, tier_count: int, result_rank: int, result_prizes: dict[str, Any]
) -> dict[str, Any]:
    """クライアントが再生する演出ペイロードを作る。"""
    assets = CARD_ASSETS[(game_type, tier_count)]
    ranks: dict[int, str] = assets.get("ranks", {})
    decoys: list[str] = list(assets.get("decoys", []))
    pool: list[str] = list(assets.get("pool", []))

    def confirm_eligible() -> bool:
        if tier_count <= 4:
            return result_rank == 1
        return result_rank in {1, 2}

    payload: dict[str, Any] = {
        "version": 1,
        "game_type": game_type,
        "tier_count": tier_count,
        "result_rank": result_rank,
        "result_prizes": result_prizes,
        "layout_mode": "position_fixed",
        "mystery_image": static_url_path(MYSTERY_IMAGE),
        "effects": {"confirm": False, "confirm_card_indices": [], "upgrade": None},
    }

    if game_type == "card_flip_single":
        alternatives = [face for rank, face in ranks.items() if rank != result_rank] or list(ranks.values())
        gold = confirm_eligible() and _RNG.random() < 0.20
        indices = [0, 1, 2] if gold else []
        payload["effects"] = {"confirm": gold, "confirm_card_indices": indices, "upgrade": None}
        payload.update({
            "interaction": "single_pick",
            "result_face": static_url_path(ranks[result_rank]),
            "cards": [_card(i, _RNG.choice(alternatives), gold=gold) for i in range(3)],
        })
        return payload

    if game_type == "card_flip_multi1":
        gold_count = 0
        if confirm_eligible():
            roll = _RNG.random()
            if roll < 0.025:
                gold_count = 3
            elif roll < 0.025 + 0.075:
                gold_count = 2
            elif roll < 0.025 + 0.075 + 0.20:
                gold_count = 1
        gold_indices = set(_RNG.sample(range(3), gold_count)) if gold_count else set()
        faces: list[str] = [""] * 3
        # 少なくとも1枚は結果等級
        result_pos = _RNG.choice(range(3))
        faces[result_pos] = ranks[result_rank]
        for i in range(3):
            if faces[i]:
                continue
            if i in gold_indices:
                if (
                    tier_count >= 5
                    and result_rank == 1
                    and gold_count >= 2
                    and 2 in ranks
                    and _RNG.random() < 0.5
                ):
                    faces[i] = ranks[2]
                else:
                    faces[i] = ranks[result_rank]
            else:
                candidates = [face for rank, face in ranks.items() if rank >= result_rank] + decoys
                faces[i] = _RNG.choice(candidates or decoys or list(ranks.values()))
        # 金カードは結果絵柄（例外混在済み）を優先して配置し直す
        for i in gold_indices:
            if faces[i] not in {ranks[result_rank], ranks.get(2)}:
                faces[i] = ranks[result_rank]
        cards = [
            _card(
                i,
                faces[i],
                gold=i in gold_indices,
                rank=next((rank for rank, value in ranks.items() if value == faces[i]), None),
            )
            for i in range(3)
        ]
        payload["effects"] = {
            "confirm": bool(gold_indices),
            "confirm_card_indices": sorted(gold_indices),
            "upgrade": None,
        }
        payload.update({"interaction": "flip_all_any_order", "cards": cards})
        return payload

    # multi2 / scratch1
    faces = _combination_result_faces(
        tier_count=tier_count,
        result_rank=result_rank,
        ranks=ranks,
        decoys=decoys,
        pool=pool,
    )

    if game_type == "scratch1":
        board_gold = confirm_eligible() and _RNG.random() < 0.25
        payload["effects"] = {
            "confirm": board_gold,
            "confirm_card_indices": [],
            "confirm_board": board_gold,
            "upgrade": None,
        }
        payload.update({
            "interaction": "scratch_three_of_nine",
            "layout_mode": "reveal_sequence",
            "reveal_faces": [static_url_path(face) for face in faces],
            "cards": [],
        })
        return payload

    # multi2 gold
    if tier_count <= 3:
        gold_indices: set[int] = set()
        if result_rank == 1:
            roll = _RNG.random()
            if roll < 0.10:
                gold_indices = {0, 1, 2}
            elif roll < 0.10 + 0.20:
                gold_indices = set(_RNG.sample(range(3), 2))
        payload["effects"] = {
            "confirm": bool(gold_indices),
            "confirm_card_indices": sorted(gold_indices),
            "upgrade": None,
        }
        payload.update({
            "interaction": "flip_all_any_order",
            "cards": [_card(i, faces[i], gold=i in gold_indices) for i in range(3)],
        })
        return payload

    r1 = ranks[1]
    gold_indices = set()
    for i, face in enumerate(faces):
        if face == r1 and _RNG.random() < 0.20:
            gold_indices.add(i)
    payload["effects"] = {
        "confirm": bool(gold_indices),
        "confirm_card_indices": sorted(gold_indices),
        "upgrade": None,
    }
    payload.update({
        "interaction": "flip_all_any_order",
        "cards": [
            _card(
                i,
                faces[i],
                gold=i in gold_indices,
                rank=next((rank for rank, value in ranks.items() if value == faces[i]), None),
            )
            for i in range(3)
        ],
    })
    return payload


async def auto_complete_ended_campaign_pending_plays(
    db: asyncpg.Connection, user: User, *, lang: str
) -> list[dict[str, Any]]:
    """終了済み企画の未開封プレイを自動で確定し、抽選済み景品を付与する。"""
    rows = await db.fetch(
        """SELECT p.id FROM minigame_plays p
           JOIN minigame_campaigns c ON c.id = p.campaign_id
           WHERE p.user_id = $1 AND p.status = 'pending_reveal' AND c.ends_at < now()
           ORDER BY p.created_at ASC""",
        user.id,
    )
    if not rows:
        return []
    completed: list[dict[str, Any]] = []
    for row in rows:
        try:
            result = await complete_play(db, user, row["id"], skip=False, lang=lang)
            completed.append(result)
        except Exception as e:
            logger.error(
                "終了済みミニゲームの自動確定に失敗 (User: %s, Play: %s): %s",
                user.id,
                row["id"],
                e,
                exc_info=True,
            )
    if completed:
        await delete_cache(f"user:{user.id}")
    return completed


async def increment_minigame_ad_play(db: asyncpg.Connection, user_id: int, daily_limit: int) -> bool:
    """広告参加の日次回数を競合なく増加させる。"""
    today = _current_token_claim_date()
    row = await db.fetchrow(
        """UPDATE users SET minigame_ad_play_count = CASE WHEN last_minigame_ad_play_date = $1
                    THEN minigame_ad_play_count + 1 ELSE 1 END,
               last_minigame_ad_play_date = $1
           WHERE id = $2 AND (CASE WHEN last_minigame_ad_play_date = $1
                    THEN minigame_ad_play_count ELSE 0 END) < $3 RETURNING id""",
        today, user_id, daily_limit,
    )
    return row is not None


async def start_play(
    db: asyncpg.Connection,
    user: User,
    *,
    method: str,
    platform: str,
    lang: str,
    require_tickets: bool = False,
) -> dict[str, Any]:
    """参加費を消費して未開封の抽選結果を作成する。"""
    await auto_complete_ended_campaign_pending_plays(db, user, lang=lang)
    if method not in {"ad", "token"}:
        raise ValueError(_message(lang, "参加方法が不正です。", "Invalid play method."))
    if method == "ad" and platform not in {"ios"}:
        raise ValueError(
            _message(lang, "広告参加はiOSアプリでのみ利用できます。", "Ad plays are available only in the iOS app.")
        )
    if require_tickets and method != "ad":
        raise ValueError(_message(lang, "参加方法が不正です。", "Invalid play method."))
    async with db.transaction():
        campaign_row = await db.fetchrow(
            """SELECT * FROM minigame_campaigns WHERE NOT is_invalid
               AND starts_at <= now() AND ends_at >= now()
               ORDER BY starts_at DESC LIMIT 1 FOR UPDATE"""
        )
        if campaign_row is None and user.is_admin:
            campaign_row = await db.fetchrow(
                """SELECT * FROM minigame_campaigns WHERE NOT is_invalid AND starts_at > now()
                   ORDER BY starts_at ASC LIMIT 1 FOR UPDATE"""
            )
        if not campaign_row:
            raise ValueError(_message(lang, "開催中の企画はありません。", "There is no active campaign."))
        if await get_user_pending_play(db, user.id):
            raise ValueError(
                _message(
                    lang,
                    "未完了のミニゲームがあります。先に完了してください。",
                    "Please finish your pending minigame first.",
                )
            )
        campaign = _record(campaign_row)
        if isinstance(campaign.get("prizes"), str):
            campaign["prizes"] = json.loads(campaign["prizes"])
        ad_price, token_price, ad_limit = resolve_prices(campaign)
        price = ad_price if method == "ad" else token_price
        locked_user = await db.fetchrow(
            """SELECT tokens, ad_skip_tickets, minigame_use_ad_skip_ticket, is_delete_ads
               FROM users WHERE id = $1 FOR UPDATE""",
            user.id,
        )
        if not locked_user or locked_user["tokens"] < price:
            raise ValueError(_message(lang, "トークンが不足しています。", "Not enough tokens."))
        tickets_spent = 0
        can_spend_tickets = (
            method == "ad"
            and not locked_user["is_delete_ads"]
            and bool(locked_user["minigame_use_ad_skip_ticket"])
            and int(locked_user["ad_skip_tickets"] or 0) >= AD_SKIP_TICKET_COST
        )
        if can_spend_tickets:
            tickets_spent = AD_SKIP_TICKET_COST
        elif require_tickets:
            raise ValueError(
                _message(
                    lang,
                    "チケットが不足しているか、チケット使用設定がオフです。",
                    "Not enough tickets, or ticket usage is turned off.",
                )
            )
        if (
            method == "ad"
            and ad_play_requires_rewarded_ad(
                is_delete_ads=bool(locked_user["is_delete_ads"]),
                can_spend_tickets=can_spend_tickets,
            )
            and is_within_ad_play_cutoff(campaign["ends_at"])
        ):
            raise ValueError(
                _message(lang, _AD_PLAY_CUTOFF_MESSAGE_JA, _AD_PLAY_CUTOFF_MESSAGE_EN)
            )
        if method == "ad" and not await increment_minigame_ad_play(db, user.id, ad_limit):
            raise ValueError(
                _message(
                    lang,
                    "本日の割引の上限に達しました。リセットまでお待ちください。",
                    "You have reached today's discount limit. Please wait until reset.",
                )
            )
        if tickets_spent > 0:
            ticket_row = await db.fetchrow(
                """UPDATE users
                   SET tokens = tokens - $1,
                       ad_skip_tickets = ad_skip_tickets - $2
                   WHERE id = $3 AND tokens >= $1 AND ad_skip_tickets >= $2
                   RETURNING tokens, ad_skip_tickets""",
                price,
                tickets_spent,
                user.id,
            )
            if not ticket_row:
                raise ValueError(
                    _message(
                        lang,
                        "チケットまたはトークンが不足しています。",
                        "Not enough tickets or tokens.",
                    )
                )
            user.tokens = ticket_row["tokens"]
            user.ad_skip_tickets = ticket_row["ad_skip_tickets"]
        else:
            await db.execute("UPDATE users SET tokens = tokens - $1 WHERE id = $2", price, user.id)
            user.tokens = locked_user["tokens"] - price
        rank, prizes = await draw_prize_rank(db, campaign, is_admin_play=bool(user.is_admin))
        animation = build_animation_payload(
            campaign["game_type"], len(campaign["prizes"]["tiers"]), rank, prizes
        )
        has_gift = any(item.get("type") == "gift" for item in prizes.get("items", []))
        row = await db.fetchrow(
            """INSERT INTO minigame_plays (
                   campaign_id, user_id, play_method, tokens_spent, tickets_spent, result_rank,
                   result_prizes, animation_payload, is_admin_play, gift_fulfillment_status
               ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9, $10)
               RETURNING *""",
            campaign["id"],
            user.id,
            method,
            price,
            tickets_spent,
            rank,
            json.dumps(prizes),
            json.dumps(animation),
            bool(user.is_admin),
            "pending" if has_gift else None,
        )
    await delete_cache(f"user:{user.id}")
    return _record(row)


async def _grant_items(db: asyncpg.Connection, user: User, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grants: list[dict[str, Any]] = []
    for item in items:
        kind = item["type"]
        if kind == "none":
            continue
        granted = copy.deepcopy(item)
        if kind == "token":
            before = user.tokens
            await user.add_tokens_without_limit(db, int(item["amount"]))
            granted["before_tokens"] = before
            granted["after_tokens"] = user.tokens
        elif kind == "ad_skip_ticket":
            before_tickets = user.ad_skip_tickets
            before_tokens = user.tokens
            await user.claim_tickets(db, int(item["amount"]), convert_to_tokens_if_delete_ads=True)
            granted["before_tickets"] = before_tickets
            granted["after_tickets"] = user.ad_skip_tickets
            granted["before_tokens"] = before_tokens
            granted["after_tokens"] = user.tokens
            granted["converted_to_tokens"] = bool(user.is_delete_ads)
        elif kind == "auto_track_extend":
            if not user.main_account:
                raise ValueError("Main account is required.")
            player = await get_player_from_db(user.main_account, db)
            if player is None:
                raise ValueError("Main account was not found.")
            hours = int(item.get("days", 0) or 0) * 24 + int(item.get("hours", 0) or 0)
            await add_auto_tracking_time(player, hours)
            expiration = await db.fetchval(
                "SELECT auto_track_expiration FROM players WHERE tag = $1", user.main_account
            )
            remaining_hours = 0
            if expiration:
                remaining_hours = max(
                    0,
                    int((expiration - datetime.datetime.now(datetime.timezone.utc)).total_seconds() // 3600),
                )
            granted["player_name"] = getattr(player, "name", None) or user.main_account
            granted["remaining_hours"] = remaining_hours
        elif kind == "battle_log_retention":
            if not user.main_account:
                raise ValueError("Main account is required.")
            player = await get_player_from_db(user.main_account, db)
            months = int(item["months"])
            current_raw = await get_battle_log_retention_months(db, user.main_account)
            current = (
                current_raw
                if current_raw is not None
                else settings.DEFAULT_BATTLE_LOG_RETENTION_MONTHS
            )
            granted_months = min(months, max(0, MAX_BATTLE_LOG_RETENTION_MONTHS - current))
            if granted_months:
                await extend_battle_log_retention(db, user.main_account, current + granted_months)
            compensation = (months - granted_months) * RETENTION_COMPENSATION_TOKENS_PER_MONTH
            if compensation:
                await user.add_tokens_without_limit(db, compensation)
                granted["compensation_tokens"] = compensation
            granted["player_name"] = (
                getattr(player, "name", None) if player else None
            ) or user.main_account
            granted["after_months"] = current + granted_months
            granted["granted_months"] = granted_months
        elif kind != "gift":
            raise ValueError(f"Unknown prize type: {kind}")
        grants.append(granted)
    return grants


async def complete_play(db: asyncpg.Connection, user: User, play_id: int, *, skip: bool, lang: str) -> dict[str, Any]:
    """未開封結果を確定し、景品を一度だけ付与する。"""
    async with db.transaction():
        row = await db.fetchrow(
            "SELECT * FROM minigame_plays WHERE id = $1 AND user_id = $2 FOR UPDATE", play_id, user.id
        )
        if not row:
            raise ValueError(_message(lang, "参加履歴が見つかりません。", "Play not found."))
        if row["status"] != "pending_reveal":
            raise ValueError(_message(lang, "この結果はすでに受け取り済みです。", "This result has already been claimed."))
        prizes = row["result_prizes"]
        if isinstance(prizes, str):
            prizes = json.loads(prizes)
        if skip:
            updated = await db.fetchrow(
                """UPDATE minigame_plays SET status = 'skipped', grant_log = $1::jsonb,
                   completed_at = now(), gift_fulfillment_status = NULL WHERE id = $2 RETURNING *""",
                json.dumps({"items": [], "skipped": True}),
                play_id,
            )
            record = _record(updated)
            record["result_prizes"] = prizes
            record["grant_log"] = _parse_json_field(record.get("grant_log"))
            return {
                **record,
                "message": _message(lang, "プレイを中止しました。", "Play abandoned."),
            }
        grants = await _grant_items(db, user, prizes.get("items", []))
        gift = any(item["type"] == "gift" for item in grants)
        updated = await db.fetchrow(
            """UPDATE minigame_plays SET status = 'completed', grant_log = $1::jsonb, granted_at = now(),
               completed_at = now(), gift_fulfillment_status = $2 WHERE id = $3 RETURNING *""",
            json.dumps({"items": grants, "skipped": False}),
            "pending" if gift else None,
            play_id,
        )
        record = _record(updated)
        record["result_prizes"] = prizes
        record["grant_log"] = _parse_json_field(record.get("grant_log"))
        if isinstance(record.get("grant_log"), dict) and not record["grant_log"].get("items"):
            record["grant_log"] = {"items": grants, "skipped": False}
    return {**record, "message": _message(lang, "景品を受け取りました。", "Prize received.")}


def _format_duration_ja(days: int = 0, hours: int = 0) -> str:
    total_hours = days * 24 + hours
    if total_hours <= 0:
        return "0時間"
    d, h = divmod(total_hours, 24)
    if d and h:
        return f"{d}日{h}時間"
    if d:
        return f"{d}日間"
    return f"{h}時間"


def _format_duration_en(days: int = 0, hours: int = 0) -> str:
    total_hours = days * 24 + hours
    if total_hours <= 0:
        return "0 hours"
    d, h = divmod(total_hours, 24)
    parts: list[str] = []
    if d:
        parts.append(f"{d} day{'s' if d != 1 else ''}")
    if h:
        parts.append(f"{h} hour{'s' if h != 1 else ''}")
    return " ".join(parts)


def _format_months_ja(months: int) -> str:
    if months >= 12:
        years, rem = divmod(months, 12)
        if rem:
            return f"{years}年{rem}ヶ月"
        return f"{years}年"
    return f"{months}ヶ月"


def _format_months_en(months: int) -> str:
    if months >= 12:
        years, rem = divmod(months, 12)
        if rem:
            return f"{years} year{'s' if years != 1 else ''} {rem} month{'s' if rem != 1 else ''}"
        return f"{years} year{'s' if years != 1 else ''}"
    return f"{months} month{'s' if months != 1 else ''}"


def format_prize_label(items: list[dict[str, Any]], lang: str) -> str:
    """景品一覧を短い表示文にする。"""
    labels: list[str] = []
    for item in items:
        kind = item.get("type")
        if kind == "none":
            labels.append("ハズレ" if lang == "ja" else "No prize")
        elif kind == "token":
            labels.append(f"{item['amount']}トークン" if lang == "ja" else f"{item['amount']} Tokens")
        elif kind == "ad_skip_ticket":
            amount = int(item["amount"])
            labels.append(
                f"{amount}広告スキップチケット" if lang == "ja" else f"{amount} Ad Skip Ticket{'s' if amount != 1 else ''}"
            )
        elif kind == "auto_track_extend":
            duration = (
                _format_duration_ja(int(item.get("days", 0) or 0), int(item.get("hours", 0) or 0))
                if lang == "ja"
                else _format_duration_en(int(item.get("days", 0) or 0), int(item.get("hours", 0) or 0))
            )
            labels.append(
                f"プレイヤー自動追跡 {duration}" if lang == "ja" else f"Player auto-tracking {duration}"
            )
        elif kind == "battle_log_retention":
            months = int(item["months"])
            duration = _format_months_ja(months) if lang == "ja" else _format_months_en(months)
            labels.append(
                f"バトル履歴保存期間延長 {duration}"
                if lang == "ja"
                else f"Battle log retention extension {duration}"
            )
        elif kind == "gift":
            name = item.get(f"name_{lang}") or item.get("name_ja") or "Gift"
            qty = item.get("quantity") or item.get("amount")
            if qty and int(qty) > 1:
                labels.append(f"{name} {int(qty)}個" if lang == "ja" else f"{name} x{int(qty)}")
            else:
                labels.append(name)
        elif kind == "token_and_ticket":
            token_amount = int(item.get("token_amount", 0) or 0)
            ticket_amount = int(item.get("ticket_amount", 0) or 0)
            if lang == "ja":
                labels.append(f"{token_amount}トークン + {ticket_amount}チケット")
            else:
                labels.append(f"{token_amount} Tokens + {ticket_amount} Tickets")
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    if all(i.get("type") in {"token", "ad_skip_ticket"} for i in items):
        return " + ".join(labels)
    return " / ".join(labels)


def build_howto(campaign: dict[str, Any], lang: str, main_account_name: str) -> dict[str, Any]:
    """画面用の参加方法・景品対応表を構築する。"""
    game_type = campaign["game_type"]
    tiers = campaign["prizes"]["tiers"]
    tier_count = len(tiers)
    assets = CARD_ASSETS[(game_type, tier_count)]
    ranks = assets.get("ranks", {})
    mystery = static_url_path(MYSTERY_IMAGE)

    if game_type == "card_flip_single":
        lead = _message(
            lang,
            "カードを1枚めくって、出た<b>絵柄</b>によって景品を獲得！",
            "Flip 1 card and win a prize based on the <b>face</b>!",
        )
        note = ""
    elif game_type == "card_flip_multi1":
        lead = _message(
            lang,
            "カードを3枚めくって、出た<b>絵柄</b>のうち最も等級の高い景品を獲得！",
            "Flip 3 cards and win the highest-rank prize among the <b>faces</b>!",
        )
        note = _message(
            lang,
            "3枚中、上記の絵柄のいずれかが少なくとも1つは出現します。最も高いものが景品です！",
            "At least one prize face always appears. The highest one is your prize!",
        )
    elif game_type == "scratch1":
        lead = _message(
            lang,
            "好きなマスを3個削って、出た<b>組み合わせ</b>によって景品を獲得！",
            "Scratch 3 tiles and win prizes based on the <b>combination</b>!",
        )
        if tier_count <= 3:
            note = _message(
                lang,
                "任意の絵柄が3つ揃えば<b>1等</b>、2つ揃えば<b>2等</b>！",
                "Any 3-of-a-kind is <b>1st</b>, any pair is <b>2nd</b>!",
            )
        else:
            note = _message(
                lang,
                f'<img src="{mystery}" alt="" class="minigame-inline-icon">は任意の絵柄が2つ揃えばOK！⬚はなんでもOK。',
                f'<img src="{mystery}" alt="" class="minigame-inline-icon"> = any matching pair is OK. ⬚ = anything is OK.',
            )
    else:
        lead = _message(
            lang,
            "カードを3枚めくって、出た<b>組み合わせ</b>によって景品を獲得！",
            "Flip 3 cards and win prizes based on the <b>combination</b>!",
        )
        if tier_count <= 2:
            note = _message(
                lang,
                "任意の絵柄が3つ揃えば<b>1等</b>、2つ揃えば<b>2等</b>！",
                "Any 3-of-a-kind is <b>1st</b>, any pair is <b>2nd</b>!",
            )
        elif tier_count == 3:
            note = _message(
                lang,
                "任意の絵柄が3つ揃えば<b>1等</b>、2つ揃えば<b>2等</b>！",
                "Any 3-of-a-kind is <b>1st</b>, any pair is <b>2nd</b>!",
            )
        else:
            note = _message(
                lang,
                f'<img src="{mystery}" alt="" class="minigame-inline-icon">は任意の絵柄が2つ揃えばOK！⬚はなんでもOK。',
                f'<img src="{mystery}" alt="" class="minigame-inline-icon"> = any matching pair is OK. ⬚ = anything is OK.',
            )

    legend: list[dict[str, Any]] = []
    for tier in sorted(tiers, key=lambda t: t["rank"]):
        rank = tier["rank"]
        label = format_prize_label(tier["items"], lang)
        if game_type in {"card_flip_single", "card_flip_multi1"}:
            faces = [static_url_path(ranks[rank])]
            empties = 0
            mysteries = 0
        else:
            faces, mysteries, empties = _multi2_legend_faces(tier_count, rank, ranks, mystery)
        allocation = tier.get("allocation")
        quantity = tier.get("quantity")
        if allocation == "stock":
            detail_html = _message(
                lang,
                f"※在庫数は期間中で<b>{quantity}個</b>です。企画終了までに全在庫が適切に当選するよう、過去の参加状況に基づいて当選確率は自動で調整されています。",
                f"※Stock during the event: <b>{quantity}</b>. Win rates are adjusted automatically from past participation so stock is distributed by the end.",
            )
        elif allocation == "weight":
            detail_html = _message(
                lang,
                f"※この景品の当選確率を決める比重は<b>{quantity}</b>です。各景品の比重の値に基づいて、当選確率が調整されています。",
                f"※This prize’s relative weight is <b>{quantity}</b>. Win rates are adjusted based on each prize’s weight.",
            )
        else:
            detail_html = ""
        legend.append({
            "rank": rank,
            "faces": faces,
            "mysteries": mysteries,
            "empties": empties,
            "slot_count": len(faces) + mysteries + empties,
            "label": label,
            "rank_label": _message(lang, f"{rank}等", f"{rank}"),
            "allocation": allocation,
            "quantity": quantity,
            "detail_html": detail_html,
        })

    main_note = ""
    needs_main = any(
        item.get("type") in {"auto_track_extend", "battle_log_retention"}
        for tier in tiers
        for item in tier.get("items", [])
    )
    if needs_main:
        main_note = _message(
            lang,
            f"プレイヤー自動追跡／バトル履歴保存期間延長の報酬は、あなたのメインアカウント(<b>{main_account_name}</b>)に付与されます。",
            f"Auto-tracking / battle log retention rewards are granted to your main account (<b>{main_account_name}</b>).",
        )

    return {"lead": lead, "legend": legend, "note": note, "main_account_note": main_note}


def _multi2_legend_faces(
    tier_count: int, rank: int, ranks: dict[int, str], mystery: str
) -> tuple[list[str], int, int]:
    """マルチ2のlegend用。戻り値: (画像URLリスト, mystery枚数, 空スロット数)。"""
    if tier_count == 2:
        return ([], 3, 0) if rank == 1 else ([], 2, 1)
    if tier_count == 3:
        if rank == 1:
            return ([], 3, 0)
        if rank == 2:
            return ([], 2, 1)
        return ([], 0, 3)
    if tier_count == 4:
        if rank == 1:
            return ([static_url_path(ranks[1])] * 3, 0, 0)
        if rank == 2:
            return ([static_url_path(ranks[2])] * 3, 0, 0)
        if rank == 3:
            return ([], 2, 1)
        return ([], 0, 3)
    if tier_count == 5:
        if rank <= 3:
            return ([static_url_path(ranks[rank])] * 3, 0, 0)
        if rank == 4:
            return ([], 2, 1)
        return ([], 0, 3)
    # 6
    if rank <= 3:
        return ([static_url_path(ranks[rank])] * 3, 0, 0)
    if rank == 4:
        return ([static_url_path(ranks[1]), static_url_path(ranks[1])], 0, 1)
    if rank == 5:
        return ([], 2, 1)
    return ([], 0, 3)


async def sync_prize_stocks(
    db: asyncpg.Connection,
    campaign_id: int,
    prizes: dict[str, Any],
    *,
    overwrite_remaining: bool = True,
) -> None:
    """stock 配分の在庫行を作成・更新し、不要行を削除する。

    overwrite_remaining=False のときは既存行の remaining を触らない
   （開始後の企画情報編集で消費済み在庫が復活するのを防ぐ）。
    """
    stock_tiers = [tier for tier in prizes["tiers"] if tier["allocation"] == "stock"]
    ranks = [tier["rank"] for tier in stock_tiers]
    for tier in stock_tiers:
        if overwrite_remaining:
            await db.execute(
                """INSERT INTO minigame_prize_stocks (campaign_id, rank, remaining) VALUES ($1, $2, $3)
                   ON CONFLICT (campaign_id, rank) DO UPDATE SET remaining = EXCLUDED.remaining""",
                campaign_id, tier["rank"], tier["quantity"],
            )
        else:
            await db.execute(
                """INSERT INTO minigame_prize_stocks (campaign_id, rank, remaining) VALUES ($1, $2, $3)
                   ON CONFLICT (campaign_id, rank) DO NOTHING""",
                campaign_id, tier["rank"], tier["quantity"],
            )
    if overwrite_remaining:
        await db.execute(
            "DELETE FROM minigame_prize_stocks WHERE campaign_id = $1 AND NOT (rank = ANY($2::smallint[]))",
            campaign_id, ranks,
        )


async def create_campaign(db: asyncpg.Connection, data: dict[str, Any]) -> dict[str, Any]:
    """管理画面から企画を作成する。"""
    errors = validate_prizes(data.get("prizes", {}))
    if errors: raise ValueError(" ".join(errors))
    if data.get("game_type") not in GAME_TYPES or (data["game_type"], len(data["prizes"]["tiers"])) not in CARD_ASSETS:
        raise ValueError("ゲーム種別または景品階層数が不正です。")
    columns = ("name_ja", "name_en", "game_type", "starts_at", "ends_at", "prizes", "price_ad_tokens", "price_token_tokens", "ad_daily_limit", "expected_total_plays", "terms_extra_ja", "terms_extra_en")
    async with db.transaction():
        row = await db.fetchrow(
            f"INSERT INTO minigame_campaigns ({', '.join(columns)}) VALUES ({', '.join(f'${i}' for i in range(1, len(columns)+1))}) RETURNING *",
            *(
                json.dumps(data["prizes"]) if column == "prizes" else data.get(column)
                for column in columns
            ),
        )
        await sync_prize_stocks(db, row["id"], data["prizes"], overwrite_remaining=True)
    await delete_cache("minigame:display_campaign:user"); await delete_cache("minigame:display_campaign:admin")
    return _record(row)


async def update_campaign(db: asyncpg.Connection, campaign_id: int, data: dict[str, Any]) -> dict[str, Any]:
    """管理画面から企画を更新する。"""
    errors = validate_prizes(data.get("prizes", {}))
    if errors: raise ValueError(" ".join(errors))
    if data.get("game_type") not in GAME_TYPES or (data["game_type"], len(data["prizes"]["tiers"])) not in CARD_ASSETS:
        raise ValueError("ゲーム種別または景品階層数が不正です。")
    fields = ("name_ja", "name_en", "game_type", "starts_at", "ends_at", "prizes", "price_ad_tokens", "price_token_tokens", "ad_daily_limit", "expected_total_plays", "is_invalid", "terms_extra_ja", "terms_extra_en")
    async with db.transaction():
        row = await db.fetchrow(
            f"UPDATE minigame_campaigns SET {', '.join(f'{field} = ${i}' for i, field in enumerate(fields, 1))}, updated_at = now() WHERE id = ${len(fields)+1} RETURNING *",
            *(
                json.dumps(data["prizes"]) if field == "prizes" else data.get(field)
                for field in fields
            ), campaign_id,
        )
        if not row: raise ValueError("企画が見つかりません。")
        # 開始前のみ残在庫を景品定義に合わせて上書き。開始後は消費済み在庫を維持する。
        starts_at = _as_utc(row["starts_at"])
        has_started = starts_at <= datetime.datetime.now(datetime.timezone.utc)
        await sync_prize_stocks(
            db,
            campaign_id,
            data["prizes"],
            overwrite_remaining=not has_started,
        )
    await delete_cache("minigame:display_campaign:user"); await delete_cache("minigame:display_campaign:admin")
    return _record(row)


async def list_campaign_plays(
    db: asyncpg.Connection, campaign_id: int, *, status: str | None = None, gift_status: str | None = None,
    limit: int = 100, offset: int = 0,
) -> list[dict[str, Any]]:
    """管理画面用に参加履歴を取得する。"""
    rows = await db.fetch(
        """SELECT p.*, u.name AS user_name FROM minigame_plays p JOIN users u ON u.id = p.user_id
           WHERE p.campaign_id = $1 AND ($2::text IS NULL OR p.status = $2)
           AND ($3::text IS NULL OR p.gift_fulfillment_status = $3)
           ORDER BY p.created_at DESC LIMIT $4 OFFSET $5""",
        campaign_id, status, gift_status, max(1, min(limit, 500)), max(0, offset),
    )
    return [_record(row) for row in rows]
