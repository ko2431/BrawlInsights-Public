"""チャットのリアクション（掲示板のチャット・大会のチャット・大会のお知らせ）で共通のルール。

- 使える絵文字は、絵文字ピッカーに渡す絵文字データ（app/static/reaction_emojis/* [この部分は公開用リポジトリでは非公開にされています] */


def emoji_key(value: str) -> str:
    """表記の揺れ（異体字セレクタの有無）をそろえて比べるためのキー。"""
    return value.replace(VARIATION_SELECTOR, "")


@functools.cache
def _allowed_emojis() -> dict[str, str]:
    """キー → データの表記。データが読めないときは空（すべて拒否される）。"""
    try:
        items = json.loads((DATA_DIR / "en.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.error(f"リアクションの絵文字データを読み込めませんでした: {e}", exc_info=True)
        return {}
    ng_keys = {emoji_key(e) for e in NG_EMOJIS}
    return {emoji_key(item["emoji"]): item["emoji"] for item in items if emoji_key(item["emoji"]) not in ng_keys}


@functools.cache
def data_version() -> str:
    """絵文字データの版（ピッカーがデータを取り直すかどうかの判定に、URL の ?v= として使う）。"""
    try:
        return hashlib.md5((DATA_DIR / "en.json").read_bytes() + (DATA_DIR / "ja.json").read_bytes()).hexdigest()[:10]
    except OSError:
        return "0"


def normalize_emoji(value: str | None) -> str | None:
    """使える絵文字ならデータの表記にそろえて返す。使えなければ None。"""
    if not value or len(value) > 32:
        return None
    return _allowed_emojis().get(emoji_key(value.strip()))


async def has_room_for(db: asyncpg.Connection, message_id: int, emoji: str) -> bool:
    """この絵文字を付けられるか（すでに付いている種類か、まだ上限に達していない）。"""
    row = await db.fetchrow(
        "SELECT COUNT(DISTINCT emoji) AS kinds, BOOL_OR(emoji = $2) AS present FROM reactions WHERE message_id = $1",
        message_id, emoji,
    )
    if not row:
        return True
    return bool(row["present"]) or (row["kinds"] or 0) < MAX_KINDS_PER_MESSAGE


async def consume_rate_limit(user_id: int) -> bool:
    """付け外しを1回数える。上限を超えていれば False。Redis が使えないときは止めない。"""
    # [この部分は公開用リポジトリでは非公開にされています]
