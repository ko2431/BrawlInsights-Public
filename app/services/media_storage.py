"""アップロードした画像のファイルの保存（検討メモ notes_private/push_image_plan_2026-10-27.md の 6.1 の B）。

今は親サーバーのディスク（settings.MEDIA_DIR）に置き、Nginx が /media/ で親のディスクから直接配る
（アップロードの URL は Nginx が親のアプリだけに送る）。開発環境では main.py が /media を配る。
サーバーを増やす・容量が足りなくなるときは、このモジュールの中だけを R2 などに差し替える。

置き場所は media/{kind}/{key の先頭2文字}/{key}{サイズ違いの接尾辞}。ファイルは一度書いたら変えない
（差し替えは別の key にする）ので、Nginx・Cloudflare で長くキャッシュしてよい。
"""
from __future__ import annotations

import contextlib
import os
import re
import tempfile

from app.core.config import settings

PUBLIC_PREFIX = "/media/"
# サイズ違い: "" は大きいサイズ（本文に出す）、"s" は一覧用の小さいサイズ、"og" は SNS の共有の画像（JPEG）
VARIANT_SUFFIXES = {"": ".webp", "s": "_s.webp", "og": "_og.jpg"}
_PUBLIC_URL_RE = re.compile(r"^/media/([a-z]+)/([a-z0-9]{2})/([a-z0-9]{16,64})\.webp$")


def is_enabled() -> bool:
    """このサーバーでファイルを保存できるか（親サーバーだけ）。"""
    return settings.MEDIA_STORAGE_ENABLED


def relative_path(kind: str, key: str, variant: str = "") -> str:
    return f"{kind}/{key[:2]}/{key}{VARIANT_SUFFIXES[variant]}"


def public_url(kind: str, key: str, variant: str = "") -> str:
    return PUBLIC_PREFIX + relative_path(kind, key, variant)


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
            path = settings.MEDIA_DIR / relative_path(kind, key, variant)
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
            (settings.MEDIA_DIR / relative_path(kind, key, variant)).unlink()
