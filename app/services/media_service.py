"""アップロードした画像（共通の土台。検討メモ notes_private/push_image_plan_2026-10-27.md の 6章）。

受け取った画像は必ず開き直して作り直し、元のファイルは保存しない（6.2）:
形式の確認 → 画素数の上限（解凍爆弾の対策）→ EXIF の向きを反映 → sRGB にそろえる → 縮小 → WebP（位置情報などのメタデータは入れない）。
変換は CPU を使うので、スレッドで行い、プロセスごとの同時数を絞る。ファイルの置き場所は media_storage.py。

種類（kind）ごとの決まり（上げられる人・大きさ・サイズ違い・回数の上限）は KINDS。お知らせ（announcement）と
フィードバックの添付（feedback。公開しない。検討メモ notes_private/feedback_plan_2026-10-31.md の 5章）。
"""
from __future__ import annotations

import asyncio
import base64
import datetime
import hashlib
import io
import math
import secrets
from dataclasses import dataclass
from typing import Any

import asyncpg
from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError

from app.core.admin_permissions import PERM_ANNOUNCEMENTS_EDIT, PERM_FEEDBACKS_VIEW
from app.core.cache import get_redis
from app.core.logger import logger
from app.services import media_storage

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_SOURCE_PIXELS = 40_000_000  # 元の画像の画素数の上限（8000×5000 程度）
ALLOWED_FORMATS = {"JPEG", "MPO", "PNG", "WEBP", "GIF"}  # MPO は iPhone などの JPEG
THUMB_SIDE = 480
OG_SIDE = 1200
MAIN_QUALITY = 88
THUMB_QUALITY = 80
OG_QUALITY = 85
# 文字の多い画像（スクリーンショット・告知の画像）は非可逆だと文字の縁がにじむので、
# PNG・GIF から作るときは可逆も試し、非可逆の何倍までなら可逆にするか
LOSSLESS_MAX_RATIO = 1.6
MAX_NAME_LENGTH = 200
LIST_LIMIT = 60
JST = datetime.timezone(datetime.timedelta(hours=9))

_process_semaphore = asyncio.Semaphore(2)
_SRGB = ImageCms.createProfile("sRGB")


@dataclass(frozen=True)
class KindRule:
    perm: str | None  # 上げられる人（権限のキー。None はログインしている人なら誰でも）
    max_side: int  # 大きいサイズの長い辺
    variants: tuple[str, ...]  # 作るサイズ違い（media_storage.VARIANT_SUFFIXES のキー）
    per_minute: int
    per_day: int
    # 同じファイルをもう一度上げたときに使い回すか（本人だけが使う種類では、他の人の画像を返さないよう使い回さない）
    dedupe: bool = True


KINDS: dict[str, KindRule] = {
    # お知らせ: 管理者・編集の権限のある副管理者。審査なし（6.7）
    "announcement": KindRule(perm=PERM_ANNOUNCEMENTS_EDIT, max_side=2048, variants=("s", "og"), per_minute=30, per_day=300),
    # フィードバックの添付: ログインしている人。公開しない（media_storage.PRIVATE_KINDS）。
    # スマホのスクリーンショット（長い辺 2,556 など）の文字が読めるよう、大きいサイズは縮めすぎない
    "feedback": KindRule(perm=None, max_side=2560, variants=("s",), per_minute=10, per_day=10, dedupe=False),
}
UNATTACHED_TTL = datetime.timedelta(days=1)  # 上げたまま添付されなかった画像を消すまで


class MediaError(Exception):
    def __init__(self, code: str, message_ja: str, message_en: str, status: int = 400):
        super().__init__(message_ja)
        self.code = code
        self.message_ja = message_ja
        self.message_en = message_en
        self.status = status

    def message(self, lang: str) -> str:
        return self.message_ja if lang == "ja" else self.message_en


@dataclass
class ProcessedImage:
    width: int
    height: int
    files: dict[str, bytes]  # サイズ違い → 中身
    sizes: dict[str, tuple[int, int]]  # サイズ違い（"" 以外）→ 幅・高さ


# --- 変換 -------------------------------------------------------------------------------
def _to_srgb(im: Image.Image, has_alpha: bool) -> Image.Image:
    """色の情報（ICC プロファイル）があれば sRGB に変えてから、RGB か RGBA にそろえる。"""
    icc = im.info.get("icc_profile")
    if icc:
        try:
            if im.mode not in ("RGB", "RGBA", "CMYK"):
                im = im.convert("RGBA" if has_alpha else "RGB")
            out_mode = "RGBA" if im.mode == "RGBA" else "RGB"
            im = ImageCms.profileToProfile(im, ImageCms.ImageCmsProfile(io.BytesIO(icc)), _SRGB, outputMode=out_mode)
        except Exception:
            pass  # 壊れたプロファイルなどは無視してそのまま使う
    return im.convert("RGBA" if has_alpha else "RGB")


def _encode_webp(im: Image.Image, quality: int, *, try_lossless: bool) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=quality, method=4)
    lossy = buf.getvalue()
    if not try_lossless:
        return lossy
    buf = io.BytesIO()
    im.save(buf, "WEBP", lossless=True, quality=80, method=4)
    lossless = buf.getvalue()
    return lossless if len(lossless) <= len(lossy) * LOSSLESS_MAX_RATIO else lossy


def _fit(im: Image.Image, side: int) -> Image.Image:
    if max(im.size) <= side:
        return im
    out = im.copy()
    out.thumbnail((side, side), Image.Resampling.LANCZOS)
    return out


def process_image(data: bytes, *, max_side: int, variants: tuple[str, ...] = ()) -> ProcessedImage:
    """画像を作り直す（スレッドで呼ぶ）。失敗したら MediaError。"""
    try:
        im = Image.open(io.BytesIO(data))
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise MediaError("not_image", "画像のファイルではありません。", "This file is not an image.")
    try:
        source_format = im.format
        if source_format not in ALLOWED_FORMATS:
            raise MediaError("format", "JPEG・PNG・WebP・GIF の画像を選んでください。", "Please choose a JPEG, PNG, WebP or GIF image.")
        if im.width * im.height > MAX_SOURCE_PIXELS:
            raise MediaError("pixels", "画像が大きすぎます（4,000万画素まで）。", "The image is too large (up to 40 megapixels).")
        if getattr(im, "is_animated", False) and source_format != "MPO":
            raise MediaError("animated", "動く画像（アニメーション）には対応していません。", "Animated images are not supported.")
        if source_format in ("JPEG", "MPO") and max(im.size) > max_side:
            # 大きい JPEG は縮めながら読む（速い。仕上がりの大きさより小さくはならない）
            ratio = max_side / max(im.size)
            im.draft(im.mode, (math.ceil(im.width * ratio), math.ceil(im.height * ratio)))
        im = ImageOps.exif_transpose(im)
        has_alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
        im = _to_srgb(im, has_alpha)
        if has_alpha and im.getchannel("A").getextrema() == (255, 255):
            im = im.convert("RGB")  # 透明な所がなければ RGB にする（小さくなる）
        im.info = {}  # EXIF（位置情報を含む）・ICC などを書き出さない
        main = _fit(im, max_side)
        files = {"": _encode_webp(main, MAIN_QUALITY, try_lossless=source_format in ("PNG", "GIF"))}
        sizes: dict[str, tuple[int, int]] = {}
        if "s" in variants:
            thumb = _fit(main, THUMB_SIDE)
            files["s"] = _encode_webp(thumb, THUMB_QUALITY, try_lossless=False)
            sizes["s"] = thumb.size
        if "og" in variants:
            og = _fit(main, OG_SIDE)
            if og.mode == "RGBA":
                background = Image.new("RGB", og.size, (255, 255, 255))
                background.paste(og, mask=og.getchannel("A"))
                og = background
            buf = io.BytesIO()
            og.save(buf, "JPEG", quality=OG_QUALITY, optimize=True, progressive=True)
            files["og"] = buf.getvalue()
            sizes["og"] = og.size
        return ProcessedImage(width=main.width, height=main.height, files=files, sizes=sizes)
    except MediaError:
        raise
    except Exception as e:
        logger.info(f"画像の変換に失敗しました（{type(e).__name__}: {e}）")
        raise MediaError("process", "画像を読み込めませんでした。別の画像で試してください。", "Could not read the image. Please try another one.")
    finally:
        im.close()


# --- 上げる -----------------------------------------------------------------------------
def get_rule(kind: str) -> KindRule:
    rule = KINDS.get(kind)
    if rule is None:
        raise MediaError("kind", "画像の種類が正しくありません。", "Invalid image type.")
    return rule


def check_permission(user: Any, rule: KindRule) -> None:
    if not user or (rule.perm is not None and not user.has_perm(rule.perm)):
        raise MediaError("perm", "画像を上げる権限がありません。", "You don't have permission to upload images.", status=403)


async def consume_rate_limit(user_id: int, kind: str, rule: KindRule) -> None:
    """1回数える。上限を超えていれば MediaError。Redis が使えないときは止めない。"""
    # [この部分は公開用リポジトリでは非公開にされています]


def _new_key() -> str:
    # 120 ビットのランダムな名前（小文字と数字の24文字。大文字と小文字を区別しないディスクでも重ならない）
    return base64.b32encode(secrets.token_bytes(15)).decode().lower()


def _clean_name(name: str | None) -> str:
    name = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    return "".join(ch for ch in name if ch.isprintable()).strip()[:MAX_NAME_LENGTH]


def to_item(row: asyncpg.Record | dict[str, Any], *, lang: str = "ja") -> dict[str, Any]:
    kind, key = row["kind"], row["key"]
    variants = row["variants"] or {}
    url = (lambda variant="": media_storage.private_url(lang, key, variant)) if media_storage.is_private(kind) else (
        lambda variant="": media_storage.public_url(kind, key, variant))
    return {
        "id": row["id"],
        "src": url(),
        "thumb": url("s") if "s" in variants else url(),
        "width": row["width"],
        "height": row["height"],
        "name": row["name"],
        "bytes": row["bytes"],
        "created_at": row["created_at"].astimezone(JST).strftime("%Y-%m-%d %H:%M") if row.get("created_at") else "",
        "owner_name": row.get("owner_name"),
    }


async def upload(db: asyncpg.Connection, *, kind: str, data: bytes, filename: str | None, user_id: int,
                 lang: str = "ja") -> dict[str, Any]:
    """画像を作り直して保存し、media に入れる。同じファイルを前に上げていれば、それを返す（rule.dedupe のとき）。"""
    rule = get_rule(kind)
    if not media_storage.is_enabled():
        logger.warning(f"画像のアップロードを、保存しないサーバーで受けました（kind={kind}）。Nginx の設定を確認してください")
        raise MediaError(
            "storage", "今は画像を上げられません。少し待ってからもう一度試してください。",
            "Images can't be uploaded right now. Please try again later.", status=503,
        )
    if len(data) > MAX_UPLOAD_BYTES:
        raise MediaError("too_large", "画像は 10MB までです。", "Images must be 10 MB or smaller.", status=413)
    sha256 = hashlib.sha256(data).hexdigest()
    existing = None
    if rule.dedupe:
        existing = await db.fetchrow(
            "SELECT * FROM media WHERE kind = $1 AND sha256 = $2 AND status IN ('active', 'removed') ORDER BY id DESC LIMIT 1",
            kind, sha256,
        )
    if existing is not None:
        if existing["status"] == "removed":
            existing = await db.fetchrow(
                "UPDATE media SET status = 'active', removed_at = NULL, removed_by = NULL WHERE id = $1 RETURNING *",
                existing["id"],
            )
        return {**to_item(existing, lang=lang), "reused": True}

    async with _process_semaphore:
        processed = await asyncio.to_thread(process_image, data, max_side=rule.max_side, variants=rule.variants)
    key = _new_key()
    await asyncio.to_thread(media_storage.save_files, kind, key, processed.files)
    try:
        row = await db.fetchrow(
            """
            INSERT INTO media (key, kind, status, owner_user_id, name, width, height, bytes, sha256, variants)
            VALUES ($1, $2, 'active', $3, $4, $5, $6, $7, $8, $9)
            RETURNING *
            """,
            key, kind, user_id, _clean_name(filename), processed.width, processed.height,
            len(processed.files[""]), sha256, {name: list(size) for name, size in processed.sizes.items()},
        )
    except BaseException:
        await asyncio.to_thread(media_storage.delete_files, kind, key)
        raise
    logger.info(
        f"画像をアップロードしました（kind={kind}, ID: {row['id']}, ユーザー ID: {user_id}, "
        f"{processed.width}x{processed.height}, {len(data):,} → {len(processed.files['']):,} バイト）"
    )
    return {**to_item(row, lang=lang), "reused": False}


# --- 一覧・一覧から外す ----------------------------------------------------------------------
async def list_media(db: asyncpg.Connection, kind: str, *, q: str = "", offset: int = 0, limit: int = LIST_LIMIT) -> dict[str, Any]:
    words = [w for w in q.split() if w][:5]
    conditions = ["m.kind = $1", "m.status = 'active'"]
    params: list[Any] = [kind]
    for word in words:
        escaped = word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.append(f"%{escaped}%")
        conditions.append(f"m.name ILIKE ${len(params)}")
    params += [limit + 1, max(offset, 0)]
    rows = await db.fetch(
        f"""
        SELECT m.*, u.name AS owner_name
        FROM media m LEFT JOIN users u ON u.id = m.owner_user_id
        WHERE {" AND ".join(conditions)}
        ORDER BY m.id DESC
        LIMIT ${len(params) - 1} OFFSET ${len(params)}
        """,
        *params,
    )
    return {"items": [to_item(row) for row in rows[:limit]], "has_more": len(rows) > limit}


async def remove(db: asyncpg.Connection, kind: str, media_id: int, *, user_id: int) -> bool:
    """一覧から外す。本文・履歴で使っているかもしれないので、ファイルは消さない。"""
    result = await db.execute(
        "UPDATE media SET status = 'removed', removed_at = now(), removed_by = $3 WHERE id = $1 AND kind = $2 AND status = 'active'",
        media_id, kind, user_id,
    )
    return result.endswith(" 1")


# --- 公開しない画像（フィードバックの添付） ----------------------------------------------------
def can_view_private(user: Any, row: asyncpg.Record | dict[str, Any]) -> bool:
    """本人（上げた人）と、その種類を見る権限のある管理者だけ。"""
    if not user:
        return False
    if row["owner_user_id"] is not None and row["owner_user_id"] == user.id:
        return True
    if row["kind"] == "feedback":
        return row["ref_type"] == "feedback" and user.has_perm(PERM_FEEDBACKS_VIEW)
    return False


async def get_private(db: asyncpg.Connection, key: str) -> asyncpg.Record | None:
    row = await db.fetchrow(
        "SELECT id, key, kind, status, owner_user_id, variants, ref_type, ref_id FROM media WHERE key = $1", key,
    )
    if row is None or row["status"] != "active" or not media_storage.is_private(row["kind"]):
        return None
    return row


async def attach(db: asyncpg.Connection, kind: str, media_ids: list[int], *, owner_user_id: int,
                 ref_type: str, ref_id: str) -> list[int]:
    """本人が上げて、まだどこにも付けていない画像を付ける。付けられた id を返す（呼ぶ側で数を確かめる）。"""
    if not media_ids:
        return []
    rows = await db.fetch(
        """
        UPDATE media SET ref_type = $4, ref_id = $5
        WHERE id = ANY($1::int[]) AND kind = $2 AND owner_user_id = $3 AND status = 'active' AND ref_type IS NULL
        RETURNING id
        """,
        media_ids, kind, owner_user_id, ref_type, ref_id,
    )
    return [row["id"] for row in rows]


async def _delete_rows_files(rows: list[asyncpg.Record]) -> None:
    for row in rows:
        await asyncio.to_thread(media_storage.delete_files, row["kind"], row["key"])


async def cleanup_unattached(db: asyncpg.Connection, kind: str, *, limit: int = 500) -> int:
    """上げたまま添付されなかった画像を、ファイルと行ごと消す（親で動く worker から。ファイルは親のディスクにある）。"""
    rows = await db.fetch(
        """
        SELECT id, kind, key FROM media
        WHERE kind = $1 AND ref_type IS NULL AND created_at < now() - $2::interval
        ORDER BY id LIMIT $3
        """,
        kind, UNATTACHED_TTL, limit,
    )
    if not rows:
        return 0
    await _delete_rows_files(rows)
    await db.execute("DELETE FROM media WHERE id = ANY($1::int[])", [row["id"] for row in rows])
    return len(rows)


async def expire_files(db: asyncpg.Connection, rows: list[asyncpg.Record]) -> int:
    """保持の期間を過ぎた画像のファイルを消し、行は removed にして残す（何枚あったかは分かるように）。"""
    if not rows:
        return 0
    await _delete_rows_files(rows)
    await db.execute(
        "UPDATE media SET status = 'removed', removed_at = now() WHERE id = ANY($1::int[]) AND status = 'active'",
        [row["id"] for row in rows],
    )
    return len(rows)
