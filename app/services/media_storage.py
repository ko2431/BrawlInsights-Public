"""アップロードした画像のファイルの保存（検討メモ notes_private/push_image_plan_2026-10-27.md の 6.1 の B）。

今は親サーバーのディスク（settings.MEDIA_DIR）に置き、Nginx が /media/ で親のディスクから直接配る
（アップロードの URL は Nginx が親のアプリだけに送る）。開発環境では main.py が /media を配る。
サーバーを増やす・容量が足りなくなるときは、このモジュールの中だけを R2 などに差し替える。

置き場所は media/{kind}/{key の先頭2文字}/{key}{サイズ違いの接尾辞}。ファイルは一度書いたら変えない
（差し替えは別の key にする）ので、Nginx・Cloudflare で長くキャッシュしてよい。

公開しない種類（PRIVATE_KINDS。フィードバックの添付）は media_private/ に同じ形で置く。Nginx は直接配らない。
URL は /{lang}/media/private/{key}（小さいサイズは …/s）で、アプリ（routers/media.py）が権限を確かめてから返す。
拡張子を付けないのは、Cloudflare が拡張子で画像をキャッシュしないようにするため（Cache-Control も private, no-store）。
"""
from __future__ import annotations

import contextlib
import os
import re
import tempfile
from pathlib import Path

from app.core.config import settings

PUBLIC_PREFIX = "/media/"
PRIVATE_KINDS = frozenset({"feedback"})
ACCEL_PREFIX = "/_media_private/"  # Nginx の internal の location（本番。config.MEDIA_ACCEL_REDIRECT）
# サイズ違い: "" は大きいサイズ（本文に出す）、"s" は一覧用の小さいサイズ、"og" は SNS の共有の画像（JPEG）
VARIANT_SUFFIXES = {"": ".webp", "s": "_s.webp", "og": "_og.jpg"}
_PUBLIC_URL_RE = re.compile(r"^/media/([a-z]+)/([a-z0-9]{2})/([a-z0-9]{16,64})\.webp$")


def is_enabled() -> bool:
    """このサーバーでファイルを保存できるか（親サーバーだけ）。"""
    return settings.MEDIA_STORAGE_ENABLED


def is_private(kind: str) -> bool:
    return kind in PRIVATE_KINDS


def relative_path(kind: str, key: str, variant: str = "") -> str:
    return f"{kind}/{key[:2]}/{key}{VARIANT_SUFFIXES[variant]}"


def file_path(kind: str, key: str, variant: str = "") -> Path:
    base = settings.MEDIA_PRIVATE_DIR if is_private(kind) else settings.MEDIA_DIR
    return base / relative_path(kind, key, variant)


def public_url(kind: str, key: str, variant: str = "") -> str:
    return PUBLIC_PREFIX + relative_path(kind, key, variant)


def private_url(lang: str, key: str, variant: str = "") -> str:
    return f"/{'en' if lang == 'en' else 'ja'}/media/private/{key}" + (f"/{variant}" if variant else "")


def accel_path(kind: str, key: str, variant: str = "") -> str:
    return ACCEL_PREFIX + relative_path(kind, key, variant)


def variant_url(src: str | None, variant: str, *, kind: str | None = None) -> str | None:
    """本文に入っている大きいサイズの URL から、サイズ違いの URL を作る（その形でなければ None）。"""
    match = _PUBLIC_URL_RE.match(src or "")
    if not match or (kind and match.group(1) != kind) or not match.group(3).startswith(match.group(2)):
        return None
    return public_url(match.group(1), match.group(3), variant)


def save_files(kind: str, key: str, files: dict[str, bytes]) -> None:
    """サイズ違いをまとめて書く（イベントループを止めないようスレッドで呼ぶ）。途中で失敗したら書いた分を消す。"""
    written = []
    try:
        for variant, data in files.items():
            path = file_path(kind, key, variant)
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                os.chmod(tmp, 0o644)  # Nginx（アプリとは別のユーザー）が読めるように
                os.replace(tmp, path)
            except BaseException:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(tmp)
                raise
            written.append(path)
    except BaseException:
        for path in written:
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
        raise


def delete_files(kind: str, key: str) -> None:
    for variant in VARIANT_SUFFIXES:
        with contextlib.suppress(FileNotFoundError):
            file_path(kind, key, variant).unlink()
