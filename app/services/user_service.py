import asyncio
import asyncpg
import datetime
import json
import math
import re
from typing import Any, TypedDict
import bcrypt

from app.exceptions.custom_exceptions import DataBaseError
from app.core.config import settings
from app.core.logger import logger
from app.core.cache import get_cache, set_cache, delete_cache, get_redis, is_transient_redis_error, log_transient_redis_warning
from app.utils.utils import format_utc_date, parse_utc_datetime, format_utc_datetime, is_expired, parse_utc_date
from app.models.missing import MISSING
from app.services.admin_notification_service import (
    clip_admin_notification_text,
    emit_admin_notification,
    format_admin_user_label,
)


def _current_token_claim_date() -> datetime.date:
    """デイリー報酬カウント判定に使う現在日付(UTC)を返す。"""
    return datetime.datetime.now(datetime.timezone.utc).date()


def _normalize_daily_claim_count(last_claim_date: datetime.date | None, claim_count: int | None) -> int:
    """最終受取日が今日でない場合、日次カウントを0として扱う。"""
    today: datetime.date = _current_token_claim_date()
    if today != last_claim_date:
        return 0
    return claim_count if claim_count is not None else 0


async def _clear_user_caches(user_id: int) -> None:
    await delete_cache(f"user:{user_id}")
    await delete_cache(f"user_include_invalid:{user_id}")


APP_LOGIN_MISSION_REWARD = 100
APP_LOGIN_MISSION_COLUMNS = {
    "ios": "is_ios_app_login_cleared",
    "android": "is_android_app_login_cleared",
}

TICKET_SELL_TOKEN_RATE = 6
_TICKET_SELL_PRESETS = (1, 2, 3, 4, 5, 7, 10, 15, 20, 25, 30)
ELIXIR_SELL_DIVISOR = 3
_ELIXIR_SELL_PRESETS = (3, 6, 9, 12, 15, 21, 30, 45, 60, 75, 90, 120, 150, 180, 210, 240)


def get_ticket_sell_options(held: int) -> list[int]:
    """売却セレクト用の枚数候補を返す。所持0なら空リスト。"""
    if held < 1:
        return []
    options = [n for n in _TICKET_SELL_PRESETS if n <= held]
    n = 40
    while n <= held:
        options.append(n)
        n += 10
    if held not in options:
        options.append(held)
    return options


def get_elixir_sell_options(held: int) -> list[int]:
    """エリクサー売却セレクト用の個数候補を返す。3未満なら空リスト。"""
    held = int(held or 0)
    if held < ELIXIR_SELL_DIVISOR:
        return []
    options = [n for n in _ELIXIR_SELL_PRESETS if n <= held]
    n = 270
    while n <= held:
        options.append(n)
        n += 30
    sellable_held = held - (held % ELIXIR_SELL_DIVISOR)
    if sellable_held >= ELIXIR_SELL_DIVISOR and sellable_held not in options:
        options.append(sellable_held)
    return options

# [この部分は公開用リポジトリでは非公開にされています]

        # ギフトコードを適用
        #* 複数の特典のあるギフトコードの場合、このコードに書いてある順番で特典を記述することを想定。
        msg_ja, msg_en = "", ""
        for key, value in self.reward.items():
            if key == "user_id": # これが指定されている場合、指定されたユーザーID以外の使用は拒絶する
                try:
                    if user.id != int(value):
                        return False, "このコードを使用する権限がありません。", "You are not authorized to use this code."
                except (TypeError, ValueError) as e:
                    logger.warning(f"ギフトコード: {self.code}の報酬: {key} - {value}の値が不正(エラー: {e})です。この報酬の付与をスキップします。")
                    continue
            elif key == "user_ids": # これが指定されている場合、指定されたユーザーID以外の使用は拒絶する
                try:
                    if user.id not in [int(id) for id in value.split(",")]:
                        return False, "このコードを使用する権限がありません。", "You are not authorized to use this code."
                except (TypeError, ValueError) as e:
                    logger.warning(f"ギフトコード: {self.code}の報酬: {key} - {value}の値が不正(エラー: {e})です。この報酬の付与をスキップします。")
                    continue
            elif key == "user_name": # これが指定されている場合、指定されたユーザー名以外の使用は拒絶する
                if user.name != str(value):
                    return False, "このコードを使用する権限がありません。", "You are not authorized to use this code."
            elif key == "giveaway": # これが指定されている場合、プレゼント企画参加コードとみなす
                count = self.usage_log.count(user_id)
                msg_ja = f"プレゼント企画への<b>{count + 1}口目</b>の応募が完了しました。最大で{self.usage_limit_per_user}口まで応募できます！"
                msg_en = f"Your <b>{count + 1} entry</b> for the giveaway has been submitted. You can submit up to {self.usage_limit_per_user} entries!"
            elif key == "msg": # メッセージを出力する(両方の言語に同じものを入れる) メッセージ系は言語指定なしと言語指定ありの両方がある場合上書きする。
                msg_ja, msg_en = str(value), str(value)
            elif key == "msg_ja":
                msg_ja = str(value)
            elif key == "msg_en":
                msg_en = str(value)
            elif key == "claim_tokens": # トークンを受け取る(デイリー上限考慮なし)
                try:
                    success = await user.claim_tokens(db, int(value))
                except ValueError as e:
                    logger.warning(f"ギフトコード: {self.code}の報酬: {key} - {value}の値が不正(エラー: {e})です。この報酬の付与をスキップします。")
                    continue
                except DataBaseError as e:
                    raise DataBaseError(e) from e
                if not success:
                    return False, "トークン所持上限に達しているため、このコードは使用できません。トークンを消費してから使用してください。", "This code cannot be used because the token holding limit has been reached. Consume the token before using it."
                else:
                    msg_ja += f"<br>トークンを{value}個受け取りました ({user.tokens - int(value)} → {user.tokens})"
                    msg_en += f"<br>Received {value} Token(s) ({user.tokens - int(value)} → {user.tokens})"
            elif key == "spend_tokens": # トークンを消費する
                try:
                    success = await user.spend_tokens(db, int(value))
                except ValueError as e:
                    logger.warning(f"ギフトコード: {self.code}の報酬: {key} - {value}の値が不正(エラー: {e})です。この報酬の付与をスキップします。")
                    continue
                except DataBaseError as e:
                    raise DataBaseError(e) from e
                if not success:
                    return False, "トークンが足りないため、このコードは使用できません。トークンを獲得してから使用してください。トークンの入手方法はこの上の<b>トークン</b>セクションをご確認ください。", "This code cannot be used due to insufficient tokens. Earn tokens before using them."
                else:
                    msg_ja += f"<br>トークンを{value}個消費しました ({user.tokens + int(value)} → {user.tokens})"
                    msg_en += f"<br>Consumed {value} Token(s) ({user.tokens + int(value)} → {user.tokens})"
            elif key == "claim_elixirs": # 自動追跡エリクサーを受け取る(広告削除ユーザーでも変換しない)
                try:
                    elixir_count = int(value)
                    before_elixirs = user.auto_track_elixirs or 0
                    success = await user.claim_elixirs(db, elixir_count)
                except ValueError as e:
                    logger.warning(f"ギフトコード: {self.code}の報酬: {key} - {value}の値が不正(エラー: {e})です。この報酬の付与をスキップします。")
                    continue
                except DataBaseError as e:
                    raise DataBaseError(e) from e

                if not success:
                    return False, "報酬の受け取り処理に失敗しました。しばらく待ってから再度お試しください。", "Failed to claim the reward. Please try again later."

                msg_ja += f"<br>{elixir_count}自動追跡エリクサーを受け取りました ({before_elixirs} → {user.auto_track_elixirs})"
                msg_en += f"<br>Received {elixir_count} Auto-Tracking Elixir(s) ({before_elixirs} → {user.auto_track_elixirs})"
            elif key == "claim_tickets": # チケットを受け取る(広告削除ユーザーは即座にトークンへ変換)
                try:
                    ticket_count = int(value)
                    before_tickets = user.ad_skip_tickets
                    before_tokens = user.tokens
                    success = await user.claim_tickets(db, ticket_count)
                except ValueError as e:
                    logger.warning(f"ギフトコード: {self.code}の報酬: {key} - {value}の値が不正(エラー: {e})です。この報酬の付与をスキップします。")
                    continue
                except DataBaseError as e:
                    raise DataBaseError(e) from e

                if not success:
                    return False, "報酬の受け取り処理に失敗しました。しばらく待ってから再度お試しください。", "Failed to claim the reward. Please try again later."

                if user.is_delete_ads:
                    converted_tokens = ticket_count * 10
                    msg_ja += f"<br>チケット{ticket_count}枚の代替報酬として、トークンを{converted_tokens}個受け取りました ({before_tokens} → {user.tokens})"
                    msg_en += f"<br>Received {converted_tokens} Token(s) instead of {ticket_count} Ticket(s) ({before_tokens} → {user.tokens})"
                else:
                    msg_ja += f"<br>チケットを{ticket_count}枚受け取りました ({before_tickets} → {user.ad_skip_tickets})"
                    msg_en += f"<br>Received {ticket_count} Ticket(s) ({before_tickets} → {user.ad_skip_tickets})"
            elif key in _GIFT_MAIN_ACCOUNT_REWARD_KEYS:
                try:
                    applied = await _apply_main_account_gift_reward(db, user, self.code, key, value)
                except (TypeError, ValueError) as e:
                    logger.warning(f"ギフトコード: {self.code}の報酬: {key} - {value}の値が不正(エラー: {e})です。この報酬の付与をスキップします。")
                    continue
                except DataBaseError as e:
                    raise DataBaseError(e) from e
                if applied:
                    applied_ja, applied_en = applied
                    msg_ja += f"<br>{applied_ja}"
                    msg_en += f"<br>{applied_en}"
            else:
                try:
                    # 上限を増やす系のギフトは、もともと上限がない(無制限)の場合は完全にスキップする。
                    if key == "saved_accounts_limit" and user.saved_accounts_limit is not None: # ブックマーク上限をvalueだけ増やす(上限考慮なし)
                        user.saved_accounts_limit += int(value)
                        msg_ja += f"<br>ブックマーク上限を{value}増やしました (現在の上限: {user.saved_accounts_limit})"
                        msg_en += f"<br>Increased Bookmark Limit (Current Limit: {user.saved_accounts_limit})"
                    elif key == "saved_accounts_limit_max24" and user.saved_accounts_limit is not None: # ブックマーク上限をvalueだけ増やす(上限24)
                        user.saved_accounts_limit += int(value)
                        if user.saved_accounts_limit > 24:
                            user.saved_accounts_limit = 24
                        msg_ja += f"<br>ブックマーク上限を{value}増やしました (現在の上限: {user.saved_accounts_limit})"
                        msg_en += f"<br>Increased Bookmark Limit (Current Limit: {user.saved_accounts_limit})"
                    elif key == "viewed_accounts_limit" and user.viewed_accounts_limit is not None: # 閲覧履歴上限をvalueだけ増やす(上限考慮なし)
                        user.viewed_accounts_limit += int(value)
                        msg_ja += f"<br>閲覧履歴上限を{value}増やしました (現在の上限: {user.viewed_accounts_limit})"
                        msg_en += f"<br>Increased Viewing History Limit (Current Limit: {user.viewed_accounts_limit})"
                    elif key == "viewed_accounts_limit_max25" and user.viewed_accounts_limit is not None: # 閲覧履歴上限をvalueだけ増やす(上限25)
                        user.viewed_accounts_limit += int(value)
                        if user.viewed_accounts_limit > 25:
                            user.viewed_accounts_limit = 25
                        msg_ja += f"<br>閲覧履歴上限を{value}増やしました (現在の上限: {user.viewed_accounts_limit})"
                        msg_en += f"<br>Increased Viewing History Limit (Current Limit: {user.viewed_accounts_limit})"
                    elif key == "is_delete_ads": # 広告を削除するかどうかの設定を上書きする
                        was_delete_ads = bool(user.is_delete_ads)
                        user.is_delete_ads = bool(value)
                        msg_ja += f"<br>広告の非表示設定を{"有効" if user.is_delete_ads else "無効"}に変更しました"
                        msg_en += f"<br>Changed AD Hiding Settings (Current Value: {user.is_delete_ads})"
                    elif key == "is_admin": # 管理者どうかの設定を上書きする
                        user.is_admin = bool(value)
                        msg_ja += f"<br>管理者かどうかの設定を{user.is_admin}に変更しました"
                        msg_en += f"<br>Changed Admin Settings (Current Value: {user.is_admin})"
                    elif key == "is_invalid": # 無効なアカウントかどうかの設定を上書きする
                        user.is_invalid = bool(value)
                        msg_ja += f"<br>無効なアカウントかどうかの設定を{user.is_invalid}に変更しました"
                        msg_en += f"<br>Changed Invalid Settings (Current Value: {user.is_invalid})"
                    elif key == "is_prohibit_posting": # 投稿禁止かどうかの設定を上書きする
                        user.is_prohibit_posting = bool(value)
                        msg_ja += f"<br>投稿禁止設定を{user.is_prohibit_posting}に変更しました"
                        msg_en += f"<br>Changed Prohibit_Posting Settings (Current Value: {user.is_prohibit_posting})"
                    elif key == "custom_settings": # カスタム設定を上書きする
                        user.custom_settings = dict(value)
                        msg_ja += f"<br>カスタム設定を{user.custom_settings}に上書きしました"
                        msg_en += f"<br>Changed Custom Settings (Current Value: {user.custom_settings})"
                    elif key == "custom_settings_merge": # カスタム設定を結合(被った項目は上書き)する
                        user.custom_settings = user.custom_settings | dict(value)
                        msg_ja += f"<br>カスタム設定を{user.custom_settings}に変更しました"
                        msg_en += f"<br>Changed Custom Settings (Current Value: {user.custom_settings})"
                    elif key == "token_limit" and user.token_limit is not None: # トークン所持上限をvalueだけ増やす
                        user.token_limit += int(value)
                        msg_ja += f"<br>トークン所持上限を{value}増やしました ({user.token_limit - int(value)} → {user.token_limit})"
                        msg_en += f"<br>Increased Token Limit ({user.token_limit - int(value)} → {user.token_limit})"
                    elif key == "token_limit_overwrite": # トークン所持上限を上書きする
                        user.token_limit = int(value) if value is not None else None
                        msg_ja += f"<br>トークン所持上限を{user.token_limit}に上書きしました"
                        msg_en += f"<br>Changed Token Limit (Current Limit: {user.token_limit})"
                    else:
                        continue
                except (TypeError, ValueError) as e:
                    logger.warning(f"ギフトコード: {self.code}の報酬: {key} - {value}の値が不正(エラー: {e})です。この報酬の付与をスキップします。")
                    continue
                try:
                    await user.update(db)
                except DataBaseError as e:
                    raise DataBaseError(e) from e
                if key == "is_delete_ads" and user.is_delete_ads and not was_delete_ads:
                    try:
                        sold_tickets, converted_tokens = await user.convert_all_tickets_to_tokens(db, token_rate=6)
                    except DataBaseError as e:
                        raise DataBaseError(e) from e
                    if sold_tickets > 0:
                        msg_ja += f"<br>所持チケット{sold_tickets}枚を自動売却し、トークンを{converted_tokens}個受け取りました"
                        msg_en += f"<br>Automatically sold {sold_tickets} Ticket(s) and received {converted_tokens} Token(s)"
                
        # 利用ログと利用回数を更新
        self.num_of_uses += 1
        self.usage_log.append(user_id)
        query = "UPDATE gift_codes SET num_of_uses = $1, usage_log = $2 WHERE code = $3"
        params = (self.num_of_uses, self.usage_log, self.code)
        try:
            await db.execute(query, *params)
        except asyncpg.PostgresError as e:
            raise DataBaseError(e) from e
            
        # キャッシュの削除 (利用状況チェック用)
        await delete_cache(f"giftcode_used:{self.code}:{user_id}")
            
        logger.info(f"{user.name} (ID: {user.id})がギフトコード: {self.code}を利用しました。総利用回数: {self.num_of_uses - 1} -> {self.num_of_uses}/{self.usage_limit_total}回 ユーザーごと利用回数: {self.usage_log.count(user_id) - 1} -> {self.usage_log.count(user_id)}/{self.usage_limit_per_user}回")
        if self.usage_limit_total and self.num_of_uses >= self.usage_limit_total:
            await emit_admin_notification(
                db,
                "giftcode_limit_reached",
                title="ギフトコードが総利用上限に達しました",
                summary=f"コード {self.code} の利用回数が上限（{self.usage_limit_total}）に達しました。",
                payload={"code": self.code, "usage_limit_total": self.usage_limit_total},
                dedupe_key=f"giftcode_limit:{self.code}",
            )
        return True, msg_ja, msg_en
    
    async def update(self, db: asyncpg.Connection, new_reward: dict = MISSING, new_is_admin_only: bool = MISSING, new_usage_limit_per_user: int = MISSING,
                     new_usage_limit_total: int = MISSING, new_is_invalid: bool = MISSING, new_start_datetime: datetime.datetime | None = MISSING,
                     new_expiration_datetime: datetime.datetime | None = MISSING) -> None:
        """ギフトコードを編集します。GiftCodeオブジェクトの各値の更新もこの関数内で行われます。

        Args:
            db (asyncpg.Connection): データベース接続
            new_reward (dict): 新しい報酬。省略可能。
            new_is_admin_only (bool): 新しい管理者のみかどうか。省略可能。
            new_usage_limit_per_user (int): 新しいユーザーあたり利用回数上限。省略可能。
            new_usage_limit_total (int): 新しい総利用回数上限。省略可能。
            new_is_invalid (bool): 新しい有効or無効設定。省略可能。
            new_start_datetime (datetime.datetime): 新しい利用開始日時。省略可能。Noneの場合は即時利用可。
            new_expiration_datetime (datetime.datetime): 新しい有効期限。省略可能。Noneの場合は期限なし。

        Raises:
            DataBaseError: データベースエラー
        """
        updates = {}
        log = f"ギフトコード: {self.code}を"
        
        if new_reward is not MISSING and new_reward != self.reward:
            updates['reward'] = new_reward
            log += f"報酬: {json.dumps(self.reward)} -> {json.dumps(new_reward)} "
        if new_is_admin_only is not MISSING and new_is_admin_only != self.is_admin_only:
            updates['is_admin_only'] = new_is_admin_only
            log += f"管理者限定: {"オン" if self.is_admin_only else "オフ"} -> {"オン" if new_is_admin_only else "オフ"} "
        if new_usage_limit_per_user is not MISSING and new_usage_limit_per_user != self.usage_limit_per_user:
            updates['usage_limit_per_user'] = new_usage_limit_per_user
            log += f"ユーザーあたり利用回数上限: {self.usage_limit_per_user} -> {new_usage_limit_per_user}回 "
        if new_usage_limit_total is not MISSING and new_usage_limit_total != self.usage_limit_total:
            updates['usage_limit_total'] = new_usage_limit_total
            log += f"総利用回数上限: {self.usage_limit_total} -> {new_usage_limit_total}回 "
        if new_is_invalid is not MISSING and new_is_invalid != self.is_invalid:
            updates['is_invalid'] = new_is_invalid
            log += f"状態: {"有効" if self.is_invalid else "無効"} -> {"有効" if new_is_invalid else "無効"} "
        if new_start_datetime is not MISSING and new_start_datetime != self.start_datetime:
            updates['start_datetime'] = _to_utc(new_start_datetime)
            log += f"利用開始: {format_utc_datetime(self.start_datetime) if self.start_datetime else "なし"} -> {format_utc_datetime(new_start_datetime) if new_start_datetime else "なし"} "
        if new_expiration_datetime is not MISSING and new_expiration_datetime != self.expiration_datetime:
            updates['expiration_datetime'] = _to_utc(new_expiration_datetime)
            log += f"期限: {format_utc_datetime(self.expiration_datetime) if self.expiration_datetime else "なし"} -> {format_utc_datetime(new_expiration_datetime) if new_expiration_datetime else "なし"} "

        # ここまで更新すべきカラムがない場合は更新をスキップ
        if not updates:
            return
        
        log += "に更新しました"
        
        set_clauses = [f"{key} = ${i+2}" for i, key in enumerate(updates.keys())]
        query = f"UPDATE gift_codes SET {', '.join(set_clauses)} WHERE code = $1"
        params = [self.code] + list(updates.values())
        
        try:
            await db.execute(query, *params)
            # オブジェクトの属性も更新
            for key, value in updates.items():
                setattr(self, key, value)
        except asyncpg.PostgresError as e:
            raise DataBaseError(e) from e

        # giveaway表示キャッシュを無効化（開始/期限/無効/報酬変更が反映されるように）
        await delete_cache("giveaway:active_code")

        logger.info(log)

async def create_gift_code(db: asyncpg.Connection, code: str, reward: dict, is_admin_only: bool, usage_limit_per_user: int = 1,
                           usage_limit_total: int = 1, is_invalid: bool = False, start_datetime: datetime.datetime | None = None,
                           expiration_datetime: datetime.datetime | None = None) -> None:
    """新しいギフトコードを追加する。

    Args:
        db (asyncpg.Connection): データベース接続
        code (str): コード
        reward (dict): 報酬
        is_admin_only (bool): 管理者のみ使用可能かどうか
        usage_limit_per_user (int, optional): 1ユーザーあたり利用回数上限。デフォルトは1。
        usage_limit_total (int, optional): 総利用回数上限。デフォルトは1。
        is_invalid (bool, optional): 無効かどうか。デフォルトはFalse(有効)
        start_datetime (datetime.datetime, optional): 利用開始日時。Noneにすると即時利用可。
        expiration_datetime (datetime.datetime, optional): コードの有効期限。Noneにすると有効期限なし。

    Raises:
        DataBaseError: データベース
        ValueError: すでに使用済みのコードが指定された場合
    """
    code = code.strip().upper() # 大文字にして保存
    start_dt = _to_utc(start_datetime)
    expiration_dt = _to_utc(expiration_datetime)
    query = """
        INSERT INTO gift_codes (
            code, reward, is_admin_only, usage_limit_per_user, 
            usage_limit_total, is_invalid, start_datetime, expiration_datetime
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
    """
    params = (
        code, reward, is_admin_only, usage_limit_per_user, 
        usage_limit_total, is_invalid, start_dt, expiration_dt
    )
    try:
        await db.execute(query, *params)
    except asyncpg.UniqueViolationError as e:
        raise ValueError("すでに使用されているコードは追加できません") from e
    except asyncpg.PostgresError as e:
        raise DataBaseError(e) from e

    await delete_cache("giveaway:active_code")

    logger.info(
        f"新しいギフトコード: {code} 報酬: {json.dumps(reward)} "
        f"{'管理者のみ' if is_admin_only else ''} 回数制限: {usage_limit_total}回 ({usage_limit_per_user}回/ユーザー) "
        f"{'無効' if is_invalid else ''} "
        f"利用開始: {format_utc_datetime(start_dt) if start_dt else '即時'} "
        f"有効期限: {format_utc_datetime(expiration_dt) if expiration_dt else '期限なし'} を追加しました"
    )

async def get_gift_code(db: asyncpg.Connection, code: str, *, allow_not_yet_started: bool = False) -> GiftCode:
    """ギフトコードを取得する。

    Args:
        db (asyncpg.Connection): データベース接続
        code (str): コード
        allow_not_yet_started (bool): Trueの場合、利用開始前のコードも取得する（管理画面用）。

    Raises:
        DataBaseError: データベースエラー
        ValueError: 無効なコードが指定された場合（利用開始前を含む）

    Returns:
        GiftCode: ギフトコード
    """
    try:
        # 大文字小文字を区別しないようにUPPER関数を使用
        result = await db.fetchrow("SELECT * FROM gift_codes WHERE UPPER(code) = UPPER($1)", code)
    except asyncpg.PostgresError as e:
        raise DataBaseError(e) from e
    if not result:
        raise ValueError("存在しないギフトコードです")
    gift_code = GiftCode(result)
    if not allow_not_yet_started and gift_code.is_not_yet_started():
        raise ValueError("存在しないギフトコードです")
    return gift_code

async def get_all_gift_codes(db: asyncpg.Connection, filter: str = "all") -> list[GiftCode]:
    """すべてのギフトコードを取得する。(管理画面用)

    Args:
        db (asyncpg.Connection): データベース接続
        filter (str): 絞り込み種別。
            - all: すべて
            - upcoming: 今後追加（利用開始が未来かつ無効でない）
            - valid: 有効（今使えるもの：開始済み・未期限切れ・無効でない）
            - invalid: 無効（手動無効 or 期限切れ。今後追加は除外）

    Raises:
        DataBaseError: データベースエラー

    Returns:
        list[GiftCode]: ギフトコードのリスト
    """
    query = "SELECT * FROM gift_codes"
    where_clauses: list[str] = []

    if filter == "upcoming":
        where_clauses.append(
            "is_invalid = false AND start_datetime IS NOT NULL AND start_datetime > NOW()"
        )
    elif filter == "valid":
        where_clauses.append(
            "is_invalid = false"
            " AND (start_datetime IS NULL OR start_datetime <= NOW())"
            " AND (expiration_datetime IS NULL OR expiration_datetime > NOW())"
        )
    elif filter == "invalid":
        where_clauses.append(
            "(is_invalid = true OR (expiration_datetime IS NOT NULL AND expiration_datetime <= NOW()))"
            " AND NOT (is_invalid = false AND start_datetime IS NOT NULL AND start_datetime > NOW())"
        )

    if where_clauses:
        query += " WHERE " + " AND ".join(where_clauses)

    query += " ORDER BY start_datetime IS NULL ASC, start_datetime ASC, expiration_datetime IS NULL ASC, expiration_datetime ASC"
    
    try:
        results = await db.fetch(query)
    except asyncpg.PostgresError as e:
        raise DataBaseError(e) from e
        
    return [GiftCode(row) for row in results]

async def has_user_used_gift_code(db: asyncpg.Connection, user_id: int, code: str) -> bool:
    """ユーザーが指定のギフトコードを使用済みか確認する（キャッシュ利用）。
    使用済みの場合は7日間、未使用の場合は1時間キャッシュする。
    """
    code = code.strip().upper()
    cache_key = f"giftcode_used:{code}:{user_id}"
    cached_data = await get_cache(cache_key)
    
    if cached_data is not None:
        return bool(cached_data)
        
    try:
        usage_log = await db.fetchval("SELECT usage_log FROM gift_codes WHERE UPPER(code) = $1", code)
    except asyncpg.PostgresError as e:
        logger.error(f"ギフトコード({code})使用状況確認中にDBエラー: {e}")
        return False
        
    if usage_log is None:
        return False
        
    used = user_id in usage_log
    
    # 使用済みなら7日間(604800秒)、未使用なら1時間(3600秒)キャッシュ
    ttl = 604800 if used else 3600
    await set_cache(cache_key, used, ttl=ttl)
    
    return used

async def get_active_giveaway_code(db: asyncpg.Connection) -> dict | None:
    """現在開催中のプレゼント企画（giveaway）コードをピックアップして返す。

    gift_codesテーブルから、reward に "giveaway": true を含み、
    is_invalid が false で、利用開始済みかつ期限切れでないコードを抽出する。
    複数ある場合は期限が早いもの → コード昇順で優先。

    結果はRedisに60秒間キャッシュされる（usage_logは含めない）。

    Returns:
        dict | None: {"code": str, "reward": dict, "usage_limit_per_user": int}
                     該当なしの場合は None
    """
    cache_key = "giveaway:active_code"
    cached = await get_cache(cache_key)
    if cached is not None:
        # キャッシュに空辞書が入っている場合は該当なしを意味する
        return cached if cached else None

    try:
        query = """
            SELECT code, reward, usage_limit_per_user, expiration_datetime
            FROM gift_codes
            WHERE is_invalid = false
              AND reward->>'giveaway' = 'true'
              AND (start_datetime IS NULL OR start_datetime <= NOW())
              AND (expiration_datetime IS NULL OR expiration_datetime > NOW())
            ORDER BY expiration_datetime IS NULL ASC, expiration_datetime ASC, code ASC
            LIMIT 1
        """
        result = await db.fetchrow(query)
    except asyncpg.PostgresError as e:
        logger.error(f"アクティブなgiveawayコードの取得中にエラー: {e}", exc_info=True)
        return None

    if result:
        data = {
            "code": result["code"],
            "reward": result["reward"],
            "usage_limit_per_user": result["usage_limit_per_user"],
            "expiration_datetime": result["expiration_datetime"].isoformat() if result.get("expiration_datetime") else None,
        }
        await set_cache(cache_key, data, ttl=60)
        return data
    else:
        # 該当なしも短時間キャッシュ（毎回DBを叩かないようにする）
        await set_cache(cache_key, {}, ttl=60)
        return None


async def get_giveaway_user_entry_count(db: asyncpg.Connection, code: str, user_id: int) -> int:
    """指定されたgiveawayコードに対するユーザーの応募回数を返す。

    usage_log 配列内でのユーザーIDの出現回数をカウントする。
    結果はRedisに60秒間キャッシュされる。

    Args:
        db: データベース接続
        code: ギフトコード
        user_id: ユーザーID

    Returns:
        int: 応募回数
    """
    cache_key = f"giveaway:entries:{code}:{user_id}"
    cached = await get_cache(cache_key)
    if cached is not None:
        return cached

    try:
        # usage_log はJSONB配列。SQLでユーザーIDの出現回数をカウントする。
        query = """
            SELECT COALESCE(
                (SELECT COUNT(*) FROM jsonb_array_elements_text(usage_log) AS elem WHERE elem::int = $2),
                0
            )::int AS entry_count
            FROM gift_codes
            WHERE UPPER(code) = UPPER($1)
        """
        result = await db.fetchval(query, code, user_id)
    except asyncpg.PostgresError as e:
        logger.error(f"giveaway応募回数の取得中にエラー (code: {code}, user_id: {user_id}): {e}", exc_info=True)
        return 0

    entry_count = result if result is not None else 0
    await set_cache(cache_key, entry_count, ttl=60)
    return entry_count

async def get_giveaway_total_stats(db: asyncpg.Connection, code: str) -> dict[str, int]:
    """指定されたgiveawayコードの全体統計（応募人数、応募口数）を返す。

    Args:
        db: データベース接続
        code: ギフトコード

    Returns:
        dict[str, int]: {"total_users": 応募人数, "total_entries": 応募口数}
    """
    cache_key = f"giveaway:total_stats:{code}"
    cached = await get_cache(cache_key)
    if cached is not None:
        return cached

    try:
        query = """
            SELECT 
                jsonb_array_length(usage_log) AS total_entries,
                (SELECT COUNT(DISTINCT elem) FROM jsonb_array_elements_text(usage_log) AS elem) AS total_users
            FROM gift_codes
            WHERE UPPER(code) = UPPER($1)
        """
        result = await db.fetchrow(query, code)
    except asyncpg.PostgresError as e:
        logger.error(f"giveaway全体統計の取得中にエラー (code: {code}): {e}", exc_info=True)
        return {"total_users": 0, "total_entries": 0}

    stats = {
        "total_users": result["total_users"] if result and result["total_users"] is not None else 0,
        "total_entries": result["total_entries"] if result and result["total_entries"] is not None else 0
    }
    await set_cache(cache_key, stats, ttl=60)
    return stats


#* /---*---*---*---*---*---*---*---*/
#* フィードバック関連
#* /---*---*---*---*---*---*---*---*/
class Feedback:
    def __init__(self, dbrow: asyncpg.Record) -> None:
        self.id: int = dbrow["id"]
        self.user_id: int = dbrow["user_id"]
        self.datetime: datetime.datetime = dbrow["datetime"]
        self.feedback_type: str = dbrow["feedback_type"]
        self.comment: str = dbrow["comment"]
        self.is_checked: bool = dbrow["is_checked"]
        
    async def check(self, db: asyncpg.Connection) -> bool:
        """フィードバックをチェックする。

        Args:
            db (asyncpg.Connection): データベース接続

        Raises:
            DataBaseError: データベースエラー

        Returns:
            bool: 書き換えを行った場合はTrue、すでにチェックされていたためスキップした場合はFalse。
        """
        if self.is_checked:
            return False
            
        query = "UPDATE feedbacks SET is_checked = TRUE WHERE id = $1"
        try:
            await db.execute(query, self.id)
        except asyncpg.PostgresError as e:
            raise DataBaseError(e) from e
            
        self.is_checked = True
        logger.info(f"ID: {self.id}のフィードバックをチェック済みにしました")
        return True
    
    async def uncheck(self, db: asyncpg.Connection) -> bool:
        """フィードバックのチェックを外す。

        Args:
            db (asyncpg.Connection): データベース接続

        Raises:
            DataBaseError: データベースエラー

        Returns:
            bool: 書き換えを行った場合はTrue、もともとチェックされていなかったためスキップした場合はFalse。
        """
        if not self.is_checked:
            return False
            
        query = "UPDATE feedbacks SET is_checked = FALSE WHERE id = $1"
        try:
            await db.execute(query, self.id)
        except asyncpg.PostgresError as e:
            raise DataBaseError(e) from e
            
        self.is_checked = False
        logger.info(f"ID: {self.id}のフィードバックのチェックを外しました")
        return True

async def get_feedback(db: asyncpg.Connection, id: int) -> Feedback:
    """指定したIDのフィードバックをFeedBack型で取得する。

    Args:
        db (asyncpg.Connection): データベース接続
        id (int): フィードバックID

    Raises:
        DataBaseError: データベースエラー
        ValueError: 存在しないIDが指定された場合

    Returns:
        Feedback: フィードバック情報
    """
    query = "SELECT * FROM feedbacks WHERE id = $1"
    try:
        result = await db.fetchrow(query, id)
    except asyncpg.PostgresError as e:
        logger.error(f"フィードバック(ID: {id})の取得中にエラー({e})が発生しました", exc_info=True)
        raise DataBaseError(e) from e
        
    return Feedback(result) if result else None

async def get_feedbacks(db: asyncpg.Connection, page: int = 1, per_page: int = 100, feedback_types: list[str] = MISSING,
                        is_checked: bool = MISSING) -> tuple[list[Feedback], int]:
    """管理者向け。フィードバックを検索する。

    Args:
        db (asyncpg.Connection): データベース接続
        page (int, optional): ページ番号。デフォルトは1。
        per_page (int, optional): 1ページあたりのフィードバック数。デフォルトは100。
        feedback_types (list[str], optional): 検索対象とするfeedback_typeの値のリスト。省略可能。
        is_checked (bool, optional): 検索対象とするis_checkedの値。省略可能。

    Raises:
        DataBaseError: データベースエラー

    Returns:
        tuple[list[Feedback], int]: ページ番号に対応する部分の検索結果のリスト。検索結果がなかった場合は[]。そして検索結果総数。
    """
    where_clauses = []
    params: list[Any] = []
    param_count = 1

    if feedback_types is not MISSING and feedback_types:
        where_clauses.append(f"feedback_type = ANY(${param_count})")
        params.append(feedback_types)
        param_count += 1

    if is_checked is not MISSING and is_checked is not None:
        where_clauses.append(f"is_checked = ${param_count}")
        params.append(is_checked)
        param_count += 1

    # 総件数の取得 ---
    count_query = "SELECT COUNT(*) FROM feedbacks"
    if where_clauses:
        count_query += " WHERE " + " AND ".join(where_clauses)

    try:
        total_count = await db.fetchval(count_query, *params)
    except asyncpg.PostgresError as e:
        logger.error(f"フィードバック検索(カウント)中にエラー: {e}\nクエリ: {count_query}\nパラメータ: {params}", exc_info=True)
        raise DataBaseError(e) from e

    if not total_count or total_count == 0:
        return [], 0

    # 指定ページのデータ取得 ---
    query = "SELECT * FROM feedbacks"
    if where_clauses:
        query += " WHERE " + " AND ".join(where_clauses)

    query += " ORDER BY datetime DESC"
    
    offset = (page - 1) * per_page
    query += f" LIMIT ${param_count} OFFSET ${param_count + 1}"
    params.extend([per_page, offset])

    try:
        rows = await db.fetch(query, *params)
        feedbacks_list = [Feedback(row) for row in rows]
    except asyncpg.PostgresError as e:
        logger.error(f"フィードバック検索(データ取得)中にエラー: {e}\nクエリ: {query}\nパラメータ: {params}", exc_info=True)
        raise DataBaseError(e) from e

    return feedbacks_list, total_count

async def create_feedback(db: asyncpg.Connection, user_id: int, feedback_type: str, comment: str) -> None:
    """新しいフィードバックを追加する。

    Args:
        db (asyncpg.Connection): データベース接続
        user_id (int): ユーザーID
        feedback_type (str): フィードバックのタイプ
        comment (str): 本文
        
    Raises:
        ValueError: 存在しないユーザーID, 投稿が禁止されているユーザーID, 投稿後のクールダウン(3分)がまだ終了していないユーザーIDが指定された場合。
        DataBaseError: データベースエラー
    """
    try:
        is_prohibited = await db.fetchval("SELECT is_prohibit_posting FROM users WHERE id = $1", user_id)
    except asyncpg.PostgresError as e:
        raise DataBaseError(e) from e
        
    if is_prohibited is None:
        raise ValueError("存在しないユーザーIDが指定されました")
    #* 投稿制限ユーザーもフィードバックは送信できるように変更した。再度戻す場合は下の2行を有効化すること
    #-if is_prohibited:
    #-    raise ValueError("このユーザーは現在投稿機能が制限されています。フィードバックを送信できません。 / This user currently has limited posting capabilities. You are unable to send feedback.")
        
    cache_key = f"create_feedback_cooldown:{user_id}"
    cached_data = await get_cache(cache_key)
    if cached_data:
        raise ValueError("投稿後のクールタイム(3分)中です。申し訳ありませんが、少し待ってから再度送信してください。 / You are on cool time (3 minutes) after submission. Sorry, please wait a bit before submitting again.")
        
    query = "INSERT INTO feedbacks (user_id, datetime, feedback_type, comment) VALUES ($1, $2, $3, $4)"
    params = (user_id, datetime.datetime.now(datetime.timezone.utc), feedback_type, comment)
    try:
        await db.execute(query, *params)
    except asyncpg.PostgresError as e:
        raise DataBaseError(e) from e
        
    await set_cache(key=cache_key, value=True, ttl=180)
    logger.info(f"新しいフィードバック ユーザーID: {user_id} タイプ: {feedback_type} 本文: {comment}を追加しました")
    await emit_admin_notification(
        db,
        "feedback_created",
        title="新しいフィードバック",
        summary=f"{feedback_type} / ユーザー: {format_admin_user_label(None, user_id)}「{clip_admin_notification_text(comment, 80)}」",
        payload={"user_id": user_id, "feedback_type": feedback_type},
    )
# PUBLIC_EXCLUDE_END