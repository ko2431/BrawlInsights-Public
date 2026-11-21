"""ホームのブックマーク・閲覧履歴の一覧に使う共通の処理。

ホームを開いたとき (main.py の index) と、ホームでブックマーク・閲覧履歴を保存したとき (routers/account.py) の
両方で使う。保存はリロードせずに画面を更新するため、保存のAPIが最新の閲覧履歴と空きスロットの数を返す
（ブックマークから外したプレイヤーは閲覧履歴に出る、履歴を消すと表示枠の外の履歴が繰り上がる、など）。
"""
import asyncpg

from app.core.logger import logger
from app.services.brawl_service import get_player_summaries_from_db

# 表示する件数の上限（ブックマークはメインアカウントの分を除いて24件）
BOOKMARK_DISPLAY_LIMIT = 25
VIEWED_DISPLAY_LIMIT = 25
CLUB_BOOKMARK_DISPLAY_LIMIT = 25
CLUB_VIEWED_DISPLAY_LIMIT = 25


def visible_viewed_player_tags(user) -> list[str]:
    """閲覧履歴に表示するプレイヤーのタグ。メイン・サブアカウントとブックマーク済みのプレイヤーは除く。"""
    tags = [
        tag for tag in (user.viewed_accounts or [])
        if not user.is_own_player_tag(tag) and tag not in (user.saved_accounts or [])
    ]
    limit = min(VIEWED_DISPLAY_LIMIT, user.viewed_accounts_limit or VIEWED_DISPLAY_LIMIT)
    return tags[:limit]


def visible_viewed_club_tags(user) -> list[str]:
    """閲覧履歴に表示するクラブのタグ。ブックマーク済みのクラブは除く。"""
    tags = [tag for tag in (user.viewed_clubs or []) if tag not in (user.saved_clubs or [])]
    limit = min(CLUB_VIEWED_DISPLAY_LIMIT, user.viewed_clubs_limit or CLUB_VIEWED_DISPLAY_LIMIT)
    return tags[:limit]


def available_bookmark_slots(user) -> int:
    return max(0, (user.saved_accounts_limit or 0) - len(user.saved_accounts or []))


def available_club_bookmark_slots(user) -> int:
    return max(0, (user.saved_clubs_limit or 0) - len(user.saved_clubs or []))


async def viewed_players_for_response(user, db: asyncpg.Connection) -> list[dict]:
    """保存後に返す閲覧履歴（プレイヤー）。名前とアイコンは DB から1回のクエリでまとめて取る（API は呼ばない）。"""
    tags = visible_viewed_player_tags(user)
    summaries = await get_player_summaries_from_db(tags, db)
    return [
        {
            "tag": tag,
            "name": (summaries.get(tag) or {}).get("name") or tag,
            "icon_id": (summaries.get(tag) or {}).get("icon_id") or 0,
        }
        for tag in tags
    ]


async def viewed_clubs_for_response(user, db: asyncpg.Connection) -> list[dict]:
    """保存後に返す閲覧履歴（クラブ）。名前とバッジは DB から1回のクエリでまとめて取る（API は呼ばない）。"""
    tags = visible_viewed_club_tags(user)
    rows_by_tag = {}
    if tags:
        try:
            rows = await db.fetch("SELECT tag, name, badge_id FROM clubs WHERE tag = ANY($1::text[])", tags)
            rows_by_tag = {row["tag"]: row for row in rows}
        except asyncpg.PostgresError as e:
            logger.warning(f"クラブ名・バッジの一括取得中にエラーが発生しました: {e}")
    return [
        {
            "tag": tag,
            "name": (rows_by_tag[tag]["name"] if tag in rows_by_tag else None) or tag,
            "badge_id": rows_by_tag[tag]["badge_id"] if tag in rows_by_tag else None,
        }
        for tag in tags
    ]
