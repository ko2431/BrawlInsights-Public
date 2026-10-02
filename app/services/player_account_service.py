"""ログインユーザーが登録しているメイン・サブアカウント(プレイヤーアカウント)を、ツール等で切り替えて使うための共通処理。"""
import asyncpg

from app.core.logger import logger
from app.services.brawl_service import get_player_summaries_from_db
from app.services.user_service import User


def resolve_user_player_tag(user: User | None, tag: str | None) -> str | None:
    """指定タグがユーザーの登録アカウントならそのタグを、そうでなければメインアカウントのタグを返す。"""
    if not user:
        return None
    if tag and user.is_own_player_tag(tag):
        return tag
    return user.main_account


async def build_player_account_options(user: User | None, db: asyncpg.Connection) -> list[dict]:
    """セレクトメニュー用の、登録アカウント一覧(メインが先頭)。名前は1回のクエリでまとめて取得する。"""
    if not user or not user.main_account:
        return []
    tags = user.player_tags
    summaries = await get_player_summaries_from_db(tags, db)
    return [
        {
            "tag": tag,
            "name": (summaries.get(tag) or {}).get("name") or tag,
            "is_main": index == 0,
        }
        for index, tag in enumerate(tags)
    ]


async def get_player_accounts_brawler_data(tags: list[str], db: asyncpg.Connection) -> dict[str, dict]:
    """複数アカウントの所持キャラ・アクセサリー数を、playersとplayer_brawlersへの2回のクエリでまとめて取得する。

    Returns:
        dict[str, dict]: タグ → {
            "name", "gadgets", "star_powers", "gears", "hyper_charges", "buffies",
            "brawlers": [{"id", "power", "gadget", "star_power", "gear"}, ...]
        }。DBに存在しないタグは含まれない。
    """
    if not tags:
        return {}
    try:
        player_rows = await db.fetch(
            """SELECT tag, name, gadgets, star_powers, gears, hyper_charges, buffies
               FROM players WHERE tag = ANY($1::text[])""",
            list(tags),
        )
        brawler_rows = await db.fetch(
            """SELECT tag, brawler_id, power,
                      jsonb_array_length(gadget_ids) AS gadget,
                      jsonb_array_length(star_power_ids) AS star_power,
                      jsonb_array_length(gear_ids) AS gear
               FROM player_brawlers WHERE tag = ANY($1::text[])""",
            list(tags),
        )
    except asyncpg.PostgresError as e:
        logger.warning(f"登録アカウントの所持キャラデータの一括取得中にエラー (tags: {tags}): {e}")
        return {}

    result: dict[str, dict] = {}
    for row in player_rows:
        result[row["tag"]] = {
            "name": row["name"] or row["tag"],
            "gadgets": row["gadgets"] or 0,
            "star_powers": row["star_powers"] or 0,
            "gears": row["gears"] or 0,
            "hyper_charges": row["hyper_charges"] or 0,
            "buffies": row["buffies"] or 0,
            "brawlers": [],
        }
    for row in brawler_rows:
        account = result.get(row["tag"])
        if account is None:
            continue
        account["brawlers"].append({
            "id": row["brawler_id"],
            "power": row["power"] or 0,
            "gadget": row["gadget"] or 0,
            "star_power": row["star_power"] or 0,
            "gear": row["gear"] or 0,
        })
    return result
