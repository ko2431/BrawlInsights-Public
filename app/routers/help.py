import json
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Request, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
import asyncpg

from app.core.logger import logger
from app.core.templating import templates
from app.services.brawl_service import calc_num_of_available_brawlers
from app.core.admin_permissions import PERM_ANNOUNCEMENTS_VIEW, user_has_perm
from app.services import announcement_service
from app.exceptions.custom_exceptions import DataBaseError
from app.db.db import PoolAcquireTimeoutError, get_db_connection_for_bg_task, get_shared_db

router = APIRouter(
    prefix="/{lang}/help",
    tags=["Helps"]
)

VERSION_HISTORY_PATH = Path(__file__).resolve().parent.parent / "data" / "version_history.json"
# 1つのバージョンでこの件数を超える項目は「残りを表示」にたたむ
VERSION_HISTORY_VISIBLE_ITEMS = 10
# このメジャーバージョン以前は Discord ボット時代として1つのグループにまとめる
VERSION_HISTORY_BOT_ERA_MAX_MAJOR = 10
_EN_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
# (ファイルの更新時刻, 言語, プラットフォーム) -> 表示用データ。JSON を毎回読まないようにする
_version_history_cache: dict[tuple[float, str, str], list[dict]] = {}


def _vh_text(value: dict | None, lang: str, platform: str) -> str:
    """{"ja", "en", "ios"?} から表示する文言を選ぶ。en がなければ ja を使う"""
    if not value:
        return ""
    if platform in value:
        value = value[platform]
    return value.get(lang) or value.get("ja", "")


def _vh_format_date(d: date, lang: str, with_day: bool = True) -> str:
    if lang == "ja":
        return f"{d.year}年{d.month}月{d.day}日" if with_day else f"{d.year}年{d.month}月"
    month = _EN_MONTHS[d.month - 1]
    return f"{month} {d.day}, {d.year}" if with_day else f"{month} {d.year}"


def _vh_period(dates: list[date], lang: str) -> str:
    start, end = min(dates), max(dates)
    start_text, end_text = _vh_format_date(start, lang, False), _vh_format_date(end, lang, False)
    if start_text == end_text:
        return start_text
    if lang == "ja" and start.year == end.year:
        end_text = f"{end.month}月"
    return f"{start_text} – {end_text}"


def build_version_history(lang: str, platform: str) -> list[dict]:
    """バージョン履歴をメジャーバージョンごとのグループにまとめる(Discord ボット時代は1グループ)"""
    with VERSION_HISTORY_PATH.open("r", encoding="utf-8") as f:
        versions = json.load(f)["versions"]

    groups: list[dict] = []
    for index, row in enumerate(versions):
        major = int(row["version"].split(".")[0])
        key = "bot" if major <= VERSION_HISTORY_BOT_ERA_MAX_MAJOR else str(major)
        if not groups or groups[-1]["key"] != key:
            if key == "bot":
                title = f"Ver 1 – {VERSION_HISTORY_BOT_ERA_MAX_MAJOR}"
                label = "Discordボット" if lang == "ja" else "Discord bot"
            else:
                title, label = f"Ver {major}", ""
            groups.append({"key": key, "title": title, "label": label, "versions": [], "dates": []})
        group = groups[-1]

        released = date.fromisoformat(row["date"])
        items = [_vh_text(item, lang, platform) for item in row.get("items", [])]
        # 残りが1件だけならたたまずに出す
        split_at = VERSION_HISTORY_VISIBLE_ITEMS if len(items) > VERSION_HISTORY_VISIBLE_ITEMS + 1 else len(items)
        group["dates"].append(released)
        group["versions"].append({
            "version": row["version"],
            "anchor": "v" + "-".join(row["version"].replace(" ", "").split(".")),
            "date_iso": row["date"],
            "date_text": _vh_text(row.get("date_label"), lang, platform) or _vh_format_date(released, lang),
            "summary": _vh_text(row.get("summary"), lang, platform),
            "visible_items": items[:split_at],
            "more_items": items[split_at:],
            "note": _vh_text(row.get("note"), lang, platform),
            "is_latest": index == 0,
        })

    for group in groups:
        group["period"] = _vh_period(group.pop("dates"), lang)
    return groups


def get_version_history(lang: str, platform: str) -> list[dict]:
    lang = "ja" if lang == "ja" else "en"
    # 出し分けがあるのは iOS だけなので、それ以外はまとめてキャッシュする
    platform = "ios" if platform == "ios" else "default"
    mtime = VERSION_HISTORY_PATH.stat().st_mtime
    key = (mtime, lang, platform)
    cached = _version_history_cache.get(key)
    if cached is None:
        # JSON が書き換えられたら、古い更新時刻のものを捨てる
        for old_key in [k for k in _version_history_cache if k[0] != mtime]:
            del _version_history_cache[old_key]
        cached = _version_history_cache[key] = build_version_history(lang, platform)
    return cached

@router.get("/announcements", name="announcements")
async def announcements(
    request: Request,
    lang: str,
    category: int | None = Query(None, ge=1, le=5),
    n: int = Query(announcement_service.LIST_PAGE_SIZE, ge=1, le=announcement_service.LIST_MAX_ITEMS),
):
    """お知らせの一覧。最初は20件、「もっと見る」の後に戻ったときは n 件まで出す（検討メモ 7.2）。"""
    platform = getattr(request.state, "platform", "web")
    limit = -(-n // announcement_service.LIST_PAGE_SIZE) * announcement_service.LIST_PAGE_SIZE
    try:
        page = await announcement_service.get_list_page(platform, lang, category, 0, limit)
    except (DataBaseError, PoolAcquireTimeoutError) as e:
        logger.error(f"お知らせの一覧の取得中にエラー: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="データベースエラー")
    return templates.TemplateResponse("announcements.html", {
        "request": request,
        "lang": lang,
        "current_page": "home",
        "items": page["items"],
        "has_more": page["has_more"],
        "next_offset": limit,
        "category": category,
        "categories": announcement_service.CATEGORIES,
    })


@router.get("/announcements/page", name="announcements_page")
async def announcements_page(
    request: Request,
    lang: str,
    category: int | None = Query(None, ge=1, le=5),
    offset: int = Query(0, ge=0, le=announcement_service.LIST_MAX_OFFSET),
):
    """「もっと見る」で足す20件（HTML の断片）。"""
    platform = getattr(request.state, "platform", "web")
    offset -= offset % announcement_service.LIST_PAGE_SIZE
    try:
        page = await announcement_service.get_list_page(platform, lang, category, offset, announcement_service.LIST_PAGE_SIZE)
    except (DataBaseError, PoolAcquireTimeoutError) as e:
        logger.error(f"お知らせの一覧の取得中にエラー: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="データベースエラー")
    return templates.TemplateResponse("announcements/_page.html", {
        "request": request,
        "lang": lang,
        "items": page["items"],
        "has_more": page["has_more"],
        "next_offset": offset + announcement_service.LIST_PAGE_SIZE,
        "category": category,
    })


@router.get("/announcements/legacy/{key}", name="announcement_legacy")
async def announcement_legacy(request: Request, lang: str, key: str):
    """旧 ID（#web1・#G91 などの古いリンク）から個別ページへ。見つからなければ一覧へ。"""
    try:
        post_id = await announcement_service.resolve_legacy_key(key)
    except (DataBaseError, PoolAcquireTimeoutError) as e:
        logger.error(f"お知らせの旧 ID の解決中にエラー: {e}", exc_info=True)
        post_id = None
    if post_id is None:
        return RedirectResponse(request.url_for("announcements", lang=lang), status_code=302)
    return RedirectResponse(request.url_for("announcement_detail", lang=lang, post_id=post_id), status_code=301)


@router.get("/announcements/{post_id:int}", name="announcement_detail")
async def announcement_detail(request: Request, lang: str, post_id: int):
    """お知らせの個別ページ。下書き・予約・削除済みは、閲覧の権限のある管理者だけに見せる。"""
    platform = getattr(request.state, "platform", "web")
    try:
        data = await announcement_service.get_public_post(post_id, platform, lang)
    except (DataBaseError, PoolAcquireTimeoutError) as e:
        logger.error(f"お知らせ #{post_id} の取得中にエラー: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail="データベースエラー")
    admin_preview = None
    user = getattr(request.state, "current_user", None)
    if data["state"] != "ok" and user_has_perm(user, PERM_ANNOUNCEMENTS_VIEW):
        async with get_db_connection_for_bg_task() as db:
            preview = await announcement_service.get_admin_preview_post(db, post_id, platform, lang)
        if preview is not None:
            admin_preview = preview.pop("row")
            data = preview
    if data["state"] == "not_found":
        raise HTTPException(status_code=404, detail="お知らせが見つかりません")
    return templates.TemplateResponse("announcement_detail.html", {
        "request": request,
        "lang": lang,
        "current_page": "home",
        "state": data["state"],
        "item": data.get("item"),
        "newer": data.get("newer"),
        "older": data.get("older"),
        "admin_preview": admin_preview,
        "categories": announcement_service.CATEGORIES,
    }, status_code=200)


@router.get("/automatic_acquisition", name="automatic_acquisition")
async def automatic_acquisition(
    request: Request,
    lang: str,
    db: asyncpg.Connection = Depends(get_shared_db)
):
    num_of_available_brawlers = await calc_num_of_available_brawlers(db)
    
    # テンプレートに渡すコンテキスト
    context = {
        "request": request,
        "lang": lang,
        "n": num_of_available_brawlers, # ヘルプ記事内で最大キャラ数を利用する
    }

    return templates.TemplateResponse("help/automatic_acquisition.html", context)

@router.get("/search_specification", name="search_specification")
async def search_specification(request: Request, lang: str):
    context = {"request": request, "lang": lang}
    return templates.TemplateResponse("help/search_specification.html", context)

@router.get("/about_the_technology", name="about_the_technology")
async def about_the_technology(request: Request, lang: str):
    context = {"request": request, "lang": lang}
    return templates.TemplateResponse("help/about_the_technology.html", context)

@router.get("/version_history", name="version_history")
async def version_history(request: Request, lang: str):
    platform = getattr(request.state, "platform", "unknown")
    context = {"request": request, "lang": lang, "version_groups": get_version_history(lang, platform)}
    return templates.TemplateResponse("help/version_history.html", context)

@router.get("/sponsor_info", name="sponsor_info")
async def sponsor_info(request: Request, lang: str):
    context = {"request": request, "lang": lang}
    return templates.TemplateResponse("help/sponsor_info.html", context)
