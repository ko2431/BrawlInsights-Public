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
from app.services.user_service import User, _current_token_claim_date, try_claim_tutorial_mission
from app.services.admin_notification_service import (
    clip_admin_notification_text,
    emit_admin_notification,
    format_admin_user_label,
)

DEFAULT_PRICE_AD_TOKENS = 7
DEFAULT_PRICE_TOKEN_TOKENS = 20
DEFAULT_AD_DAILY_LIMIT = 5
AD_SKIP_TICKET_COST = 2
MINIGAME_AD_PLAY_CUTOFF_SECONDS = 60
USER_HISTORY_LIMIT = 10
MAX_PRIZE_TIERS = 8
MIN_PRIZE_TIERS = 2
RETENTION_COMPENSATION_TOKENS_PER_MONTH = 50
MAX_BATTLE_LOG_RETENTION_MONTHS = 120
DEFAULT_EXPECTED_TOTAL_PLAYS = 500
GAME_TYPES = ("card_flip_single", "card_flip_multi1", "card_flip_multi2", "scratch1", "bingo1")
# [この部分は公開用リポジトリでは非公開にされています]
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
                    "未完了の参加があります。先に完了してください。",
                    "Please finish your pending entry first.",
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
        has_special_reward = any(
            item.get("type") == "special_reward_link" for item in prizes.get("items", [])
        )
        # asyncpg の jsonb codec があるため dict をそのまま渡す（json.dumps すると二重エンコードになる）
        row = await db.fetchrow(
            """INSERT INTO minigame_plays (
                   campaign_id, user_id, play_method, tokens_spent, tickets_spent, result_rank,
                   result_prizes, animation_payload, is_admin_play, gift_fulfillment_status
               ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
               RETURNING *""",
            campaign["id"],
            user.id,
            method,
            price,
            tickets_spent,
            rank,
            prizes,
            animation,
            bool(user.is_admin),
            "pending" if has_gift else None,
        )
    await delete_cache(f"user:{user.id}")
    await try_claim_tutorial_mission(user, db, "spend_tokens")
    play = _record(row)
    campaign_name = campaign.get("name_ja") or campaign.get("name_en") or f"ID {campaign['id']}"
    if has_gift:
        await emit_admin_notification(
            db,
            "minigame_gift_won",
            title="ギフト景品が当選しました",
            summary=(
                f"ユーザー: {format_admin_user_label(user.name, user.id)} が企画"
                f"「{clip_admin_notification_text(str(campaign_name), 60)}」で{rank}等に当選。"
            ),
            payload={"play_id": play.get("id"), "campaign_id": campaign["id"], "user_id": user.id, "rank": rank},
        )
    elif has_special_reward:
        await emit_admin_notification(
            db,
            "minigame_special_reward_won",
            title="特別報酬リンク景品が当選しました",
            summary=(
                f"ユーザー: {format_admin_user_label(user.name, user.id)} が企画"
                f"「{clip_admin_notification_text(str(campaign_name), 60)}」で{rank}等に当選。"
            ),
            payload={"play_id": play.get("id"), "campaign_id": campaign["id"], "user_id": user.id, "rank": rank},
        )
    else:
        await emit_admin_notification(
            db,
            "minigame_play",
            title="ミニゲームに参加しました",
            summary=(
                f"ユーザー: {format_admin_user_label(user.name, user.id)} / "
                f"企画「{clip_admin_notification_text(str(campaign_name), 60)}」 / {rank}等"
            ),
            payload={"play_id": play.get("id"), "campaign_id": campaign["id"], "user_id": user.id, "rank": rank},
        )
    return play


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
        elif kind == "auto_track_elixir":
            before_elixirs = user.auto_track_elixirs or 0
            await user.claim_elixirs(db, int(item["amount"]))
            granted["before_elixirs"] = before_elixirs
            granted["after_elixirs"] = user.auto_track_elixirs
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
        elif kind not in {"gift", "special_reward_link"}:
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
                """UPDATE minigame_plays SET status = 'skipped', grant_log = $1,
                   completed_at = now(), gift_fulfillment_status = NULL WHERE id = $2 RETURNING *""",
                {"items": [], "skipped": True},
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
            """UPDATE minigame_plays SET status = 'completed', grant_log = $1, granted_at = now(),
               completed_at = now(), gift_fulfillment_status = $2 WHERE id = $3 RETURNING *""",
            {"items": grants, "skipped": False},
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


def _prize_icon_url(items: list[dict[str, Any]]) -> str:
    """特別報酬リンク景品の任意アイコンURLを返す。未設定時は空文字。"""
    from app.services.special_reward_link_service import SpecialRewardLinkError, normalize_icon_path

    for item in items:
        if not isinstance(item, dict) or item.get("type") != "special_reward_link":
            continue
        try:
            path = normalize_icon_path(item.get("icon_path"))
        except SpecialRewardLinkError:
            continue
        if path:
            return static_url_path(path)
    return ""


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
        elif kind == "auto_track_elixir":
            amount = int(item["amount"])
            labels.append(
                f"{amount}自動追跡エリクサー" if lang == "ja" else f"{amount} Auto-Tracking Elixir{'s' if amount != 1 else ''}"
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
        elif kind == "special_reward_link":
            labels.append(item.get(f"name_{lang}") or item.get("name_ja") or ("特別報酬" if lang == "ja" else "Special reward"))
        elif kind == "token_and_ticket":
            token_amount = int(item.get("token_amount", 0) or 0)
            ticket_amount = int(item.get("ticket_amount", 0) or 0)
            if lang == "ja":
                labels.append(f"{token_amount}トークン + {ticket_amount}チケット")
            else:
                labels.append(f"{token_amount} Tokens + {ticket_amount} Tickets")
        elif kind == "token_and_elixir":
            token_amount = int(item.get("token_amount", 0) or 0)
            elixir_amount = int(item.get("elixir_amount", 0) or 0)
            if lang == "ja":
                labels.append(f"{token_amount}トークン + {elixir_amount}自動追跡エリクサー")
            else:
                labels.append(f"{token_amount} Tokens + {elixir_amount} Auto-Tracking Elixir{'s' if elixir_amount != 1 else ''}")
        elif kind == "ticket_and_elixir":
            ticket_amount = int(item.get("ticket_amount", 0) or 0)
            elixir_amount = int(item.get("elixir_amount", 0) or 0)
            if lang == "ja":
                labels.append(f"{ticket_amount}広告スキップチケット + {elixir_amount}自動追跡エリクサー")
            else:
                labels.append(
                    f"{ticket_amount} Ad Skip Ticket{'s' if ticket_amount != 1 else ''} + {elixir_amount} Auto-Tracking Elixir{'s' if elixir_amount != 1 else ''}"
                )
        elif kind == "token_and_ticket_and_elixir":
            token_amount = int(item.get("token_amount", 0) or 0)
            ticket_amount = int(item.get("ticket_amount", 0) or 0)
            elixir_amount = int(item.get("elixir_amount", 0) or 0)
            if lang == "ja":
                labels.append(f"{token_amount}トークン + {ticket_amount}広告スキップチケット + {elixir_amount}自動追跡エリクサー")
            else:
                labels.append(
                    f"{token_amount} Tokens + {ticket_amount} Ad Skip Ticket{'s' if ticket_amount != 1 else ''} + {elixir_amount} Auto-Tracking Elixir{'s' if elixir_amount != 1 else ''}"
                )
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    if all(i.get("type") in {"token", "ad_skip_ticket", "auto_track_elixir"} for i in items):
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
    elif game_type == "bingo1":
        lead = _message(
            lang,
            "マスを全て開けて、任意の絵柄がビンゴした<b>ライン数</b>によって景品を獲得！",
            "Open all tiles and win a prize based on the number of <b>bingo lines</b>!",
        )
        note = ""
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
        condition_text = ""
        if game_type == "bingo1":
            faces = []
            empties = 0
            mysteries = 0
            condition_text = bingo_condition_text(tier_count, rank, lang)
        elif game_type in {"card_flip_single", "card_flip_multi1"}:
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
            "condition_text": condition_text,
            "label": label,
            "icon_url": _prize_icon_url(tier.get("items") or []),
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
    needs_elixir = any(
        item.get("type") == "auto_track_elixir"
        for tier in tiers
        for item in tier.get("items", [])
    )
    if needs_main:
        main_note = _message(
            lang,
            f'プレイヤー自動追跡／バトル履歴保存期間延長の報酬は、あなたのメインアカウント(<b>{main_account_name}</b>)に付与されます。<br>'
            f'<a href="/{lang}/help/automatic_acquisition" class="info-block__link">プレイヤー自動追跡機能とは？</a>',
            f'Auto-tracking / battle log retention rewards are granted to your main account (<b>{main_account_name}</b>).<br>'
            f'<a href="/{lang}/help/automatic_acquisition" class="info-block__link">What is player auto-tracking?</a>',
        )
    if needs_elixir:
        elixir_link = _message(
            lang,
            f'<a href="#elixir" class="info-block__link">自動追跡エリクサーとは？</a>',
            f'<a href="#elixir" class="info-block__link">What is Auto-Tracking Elixir?</a>',
        )
        main_note = f"{main_note}<br>{elixir_link}" if main_note else elixir_link

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
    if tier_count == 6:
        if rank <= 3:
            return ([static_url_path(ranks[rank])] * 3, 0, 0)
        if rank == 4:
            return ([static_url_path(ranks[1]), static_url_path(ranks[1])], 0, 1)
        if rank == 5:
            return ([], 2, 1)
        return ([], 0, 3)
    if tier_count == 7:
        if rank <= 3:
            return ([static_url_path(ranks[rank])] * 3, 0, 0)
        if rank == 4:
            return ([static_url_path(ranks[1]), static_url_path(ranks[1])], 0, 1)
        if rank == 5:
            return ([static_url_path(ranks[2]), static_url_path(ranks[2])], 0, 1)
        if rank == 6:
            return ([], 2, 1)
        return ([], 0, 3)
    # 8
    if rank <= 3:
        return ([static_url_path(ranks[rank])] * 3, 0, 0)
    if rank == 4:
        return ([static_url_path(ranks[1]), static_url_path(ranks[1])], 0, 1)
    if rank == 5:
        return ([static_url_path(ranks[2]), static_url_path(ranks[2])], 0, 1)
    if rank == 6:
        return ([static_url_path(ranks[3]), static_url_path(ranks[3])], 0, 1)
    if rank == 7:
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
    from app.services.special_reward_link_service import validate_minigame_special_reward_prizes
    errors.extend(await validate_minigame_special_reward_prizes(
        db, data.get("prizes", {}), starts_at=data["starts_at"], ends_at=data["ends_at"],
    ))
    if errors: raise ValueError(" ".join(errors))
    if data.get("game_type") not in GAME_TYPES or (data["game_type"], len(data["prizes"]["tiers"])) not in CARD_ASSETS:
        raise ValueError("ゲーム種別または景品階層数が不正です。")
    columns = ("name_ja", "name_en", "game_type", "starts_at", "ends_at", "prizes", "price_ad_tokens", "price_token_tokens", "ad_daily_limit", "expected_total_plays", "terms_extra_ja", "terms_extra_en")
    async with db.transaction():
        # asyncpg の jsonb codec があるため prizes は dict のまま渡す
        row = await db.fetchrow(
            f"INSERT INTO minigame_campaigns ({', '.join(columns)}) VALUES ({', '.join(f'${i}' for i in range(1, len(columns)+1))}) RETURNING *",
            *(data.get(column) for column in columns),
        )
        await sync_prize_stocks(db, row["id"], data["prizes"], overwrite_remaining=True)
    await delete_cache("minigame:display_campaign:user"); await delete_cache("minigame:display_campaign:admin")
    return _record(row)


async def update_campaign(db: asyncpg.Connection, campaign_id: int, data: dict[str, Any]) -> dict[str, Any]:
    """管理画面から企画を更新する。"""
    errors = validate_prizes(data.get("prizes", {}))
    from app.services.special_reward_link_service import validate_minigame_special_reward_prizes
    errors.extend(await validate_minigame_special_reward_prizes(
        db, data.get("prizes", {}), starts_at=data["starts_at"], ends_at=data["ends_at"],
        exclude_campaign_id=campaign_id,
    ))
    if errors: raise ValueError(" ".join(errors))
    if data.get("game_type") not in GAME_TYPES or (data["game_type"], len(data["prizes"]["tiers"])) not in CARD_ASSETS:
        raise ValueError("ゲーム種別または景品階層数が不正です。")
    fields = ("name_ja", "name_en", "game_type", "starts_at", "ends_at", "prizes", "price_ad_tokens", "price_token_tokens", "ad_daily_limit", "expected_total_plays", "is_invalid", "terms_extra_ja", "terms_extra_en")
    async with db.transaction():
        # asyncpg の jsonb codec があるため prizes は dict のまま渡す
        row = await db.fetchrow(
            f"UPDATE minigame_campaigns SET {', '.join(f'{field} = ${i}' for i, field in enumerate(fields, 1))}, updated_at = now() WHERE id = ${len(fields)+1} RETURNING *",
            *(data.get(field) for field in fields), campaign_id,
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
