from __future__ import annotations

from collections.abc import Callable, Hashable
from threading import RLock

from cachetools import LRUCache, TTLCache
from PIL import Image

CacheKey = Hashable


def estimate_image_size_bytes(image: Image.Image) -> int:
    """RGBA 画像の概算メモリサイズを返す。"""
    bands = max(1, len(image.getbands()))
    return max(1, image.width * image.height * bands)


class ImageComponentCache:
    """Pillow画像専用のスレッドセーフなキャッシュ。

    画像サイズを getsizeof として扱うため、max_bytes で概算メモリ使用量を制御できる。
    get/get_or_create は常に copy を返し、呼び出し側での破壊的変更が
    キャッシュ本体へ波及しないようにする。
    """

    def __init__(self, max_bytes: int, ttl_seconds: int | None = None) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes は 1 以上で指定してください。")

        self._lock = RLock()
        self._hits = 0
        self._misses = 0

        if ttl_seconds is None:
            self._cache: LRUCache[CacheKey, Image.Image] | TTLCache[CacheKey, Image.Image]
            self._cache = LRUCache(maxsize=max_bytes, getsizeof=estimate_image_size_bytes)
        else:
            self._cache = TTLCache(
                maxsize=max_bytes,
                ttl=ttl_seconds,
                getsizeof=estimate_image_size_bytes,
            )

    def get(self, key: CacheKey) -> Image.Image | None:
        with self._lock:
            image = self._cache.get(key)
            if image is None:
                self._misses += 1
                return None
            self._hits += 1
            return image.copy()

    def set(self, key: CacheKey, image: Image.Image) -> None:
        with self._lock:
            self._cache[key] = image.copy()

    def get_or_create(self, key: CacheKey, factory: Callable[[], Image.Image]) -> Image.Image:
        cached = self.get(key)
        if cached is not None:
            return cached

        created = factory()
        self.set(key, created)
        return created.copy()

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "hits": self._hits,
                "misses": self._misses,
                "entries": len(self._cache),
                "currsize": int(self._cache.currsize),
                "maxsize": int(self._cache.maxsize),
            }
