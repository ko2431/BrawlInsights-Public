"""
ad_banner_service.py
バナー広告の抽選ロジック。
ファイルシステムから featured_media/ フォルダを走査し、config.json に基づいて
言語・プラットフォーム・掲載期間を考慮した2段階ランダム抽選を行う。
（DB での管理に移す刷新の検討メモ: docs/plans/sponsor_ads_plan_2026-11-26.md。これは段階0）
"""
import datetime
import json
import random
import time
from dataclasses import dataclass
from pathlib import Path

from app.core.logger import logger

# --- 定数 ---
_IMAGES_ROOT = Path(__file__).resolve().parent.parent / "static" / "images"
_BANNER_DIR_CANDIDATES: tuple[Path, ...] = (
    _IMAGES_ROOT / "featured_media",
    _IMAGES_ROOT / "ad_banners",
)
# nginx は親ディスクを直読みする。抽選側が旧フォルダに落ちても公開 URL は新パスに揃える。
AD_BANNER_STATIC_PREFIX = "/static/images/featured_media"
AD_BANNER_MAX_SPONSORS = 10   # スポンサー上限人数。これ未満のときself広告も候補に入る
BANNER_EXTENSIONS = {".webp", ".png", ".jpg", ".jpeg"}
# フォルダ・config.json・画像の有無を読み直す間隔（秒）。掲載期間の判定は抽選のたびに行うので、開始・終了の時刻はずれない
_SNAPSHOT_TTL_SECONDS = 30.0
# config.json の start_at・end_at でタイムゾーンを省略したときは JST とみなす
_JST = datetime.timezone(datetime.timedelta(hours=9))

_logged_missing_banner_dir = False
_logged_legacy_banner_dir = False
_logged_config_problems: set[str] = set()


@dataclass(frozen=True, slots=True)
class _BannerFolder:
    """1フォルダ分の設定（画像が実在するバナーだけを持つ）。"""
    name: str
    default_url: str | None
    banners: tuple[dict, ...]
    start_at: datetime.datetime | None
    end_at: datetime.datetime | None

    def is_live(self, now: datetime.datetime) -> bool:
        if self.start_at is not None and now < self.start_at:
            return False
        if self.end_at is not None and now >= self.end_at:
            return False
        return True


@dataclass(frozen=True, slots=True)
class _Snapshot:
    expires_at: float  # time.monotonic() の値
    sponsors: tuple[_BannerFolder, ...]
    selves: dict[str, _BannerFolder]


_snapshot: _Snapshot | None = None


def _banner_dir_usable(directory: Path) -> bool:
    """空フォルダや README だけのディレクトリは使わない。"""
    if not directory.is_dir():
        return False
    try:
        return any(
            p.is_dir() and (p.name.startswith("sponsor_") or p.name.startswith("self_"))
            for p in directory.iterdir()
        )
    except OSError:
        return False


def _resolve_banner_storage() -> Path:
    """
    抽選は各 uvicorn のローカルディスクを見る。HTML の大半は子サーバが生成する。
    git mv 後に featured_media が無い・空だと、以前はログも出さずバナー全体が消えていた。
    """
    global _logged_missing_banner_dir, _logged_legacy_banner_dir
    for directory in _BANNER_DIR_CANDIDATES:
        if not _banner_dir_usable(directory):
            continue
        if directory.name == "ad_banners" and not _logged_legacy_banner_dir:
            logger.warning(
                "独自バナーを旧フォルダ ad_banners から読みます。"
                "親・子の両方で git pull し、featured_media があることを確認してください: %s",
                directory,
            )
            _logged_legacy_banner_dir = True
        return directory
    if not _logged_missing_banner_dir:
        logger.error(
            "独自バナー用ディレクトリが見つかりません。探したパス: %s",
            ", ".join(str(path) for path in _BANNER_DIR_CANDIDATES),
        )
        _logged_missing_banner_dir = True
    return _BANNER_DIR_CANDIDATES[0]


def _warn_config_once(key: str, message: str, *args) -> None:
    """同じ設定の誤りで、読み直しのたびに WARNING（管理者通知）が出続けないようにする。"""
    if key in _logged_config_problems:
        return
    _logged_config_problems.add(key)
    logger.warning(message, *args)


def _parse_schedule_value(value, folder_name: str, field: str) -> tuple[bool, datetime.datetime | None]:
    """start_at・end_at を読む。戻り値は (読めたか, 日時)。未指定は (True, None)。"""
    if value is None or value == "":
        return True, None
    if not isinstance(value, str):
        _warn_config_once(
            f"{folder_name}:{field}:{value!r}",
            "独自バナー %s の config.json の %s が文字列ではありません（%r）。このフォルダは表示しません。",
            folder_name, field, value,
        )
        return False, None
    try:
        parsed = datetime.datetime.fromisoformat(value.strip())
    except ValueError:
        _warn_config_once(
            f"{folder_name}:{field}:{value}",
            "独自バナー %s の config.json の %s（%s）を日時として読めません。このフォルダは表示しません。",
            folder_name, field, value,
        )
        return False, None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_JST)
    return True, parsed


def _build_folder(folder: Path) -> _BannerFolder | None:
    """config.json を読み、画像が実在するバナーだけを持つ _BannerFolder を作る。表示できない設定なら None。"""
    config = _load_config(folder)
    if config is None:
        return None

    ok_start, start_at = _parse_schedule_value(config.get("start_at"), folder.name, "start_at")
    ok_end, end_at = _parse_schedule_value(config.get("end_at"), folder.name, "end_at")
    # 期間の指定を読めないときは、契約の期間の外に出してしまわないよう、フォルダごと出さない
    if not (ok_start and ok_end):
        return None
    if start_at is not None and end_at is not None and start_at >= end_at:
        _warn_config_once(
            f"{folder.name}:range:{start_at.isoformat()}:{end_at.isoformat()}",
            "独自バナー %s の config.json の start_at（%s）が end_at（%s）より後です。このフォルダは表示しません。",
            folder.name, start_at.isoformat(), end_at.isoformat(),
        )
        return None

    banners: list[dict] = []
    for banner in config.get("banners", []) or []:
        if not isinstance(banner, dict):
            continue
        file_name = banner.get("file") or ""
        if not file_name:
            continue
        # 拡張子チェック
        if Path(file_name).suffix.lower() not in BANNER_EXTENSIONS:
            continue
        # 画像ファイルが実際に存在するかチェック
        if not (folder / file_name).exists():
            continue
        banners.append(banner)

    return _BannerFolder(
        name=folder.name,
        default_url=config.get("default_url") or None,
        banners=tuple(banners),
        start_at=start_at,
        end_at=end_at,
    )


def _get_snapshot() -> _Snapshot | None:
    """フォルダ・設定・画像の有無を読み直す（_SNAPSHOT_TTL_SECONDS ごと）。ページの表示のたびにディスクを読まない。"""
    global _snapshot
    now_mono = time.monotonic()
    if _snapshot is not None and now_mono < _snapshot.expires_at:
        return _snapshot

    banner_dir = _resolve_banner_storage()
    if not _banner_dir_usable(banner_dir):
        _snapshot = _Snapshot(expires_at=now_mono + _SNAPSHOT_TTL_SECONDS, sponsors=(), selves={})
        return _snapshot

    sponsors: list[_BannerFolder] = []
    selves: dict[str, _BannerFolder] = {}
    try:
        folders = sorted(p for p in banner_dir.iterdir() if p.is_dir())
    except OSError:
        logger.warning("独自バナー用ディレクトリの一覧を読めませんでした: %s", banner_dir, exc_info=True)
        # 前回の内容があればそれを使い続ける
        if _snapshot is not None:
            _snapshot = _Snapshot(expires_at=now_mono + _SNAPSHOT_TTL_SECONDS, sponsors=_snapshot.sponsors, selves=_snapshot.selves)
            return _snapshot
        return None

    for folder in folders:
        if folder.name.startswith("sponsor_"):
            entry = _build_folder(folder)
            if entry is not None:
                sponsors.append(entry)
        elif folder.name.startswith("self_"):
            entry = _build_folder(folder)
            if entry is not None:
                selves[folder.name] = entry

    _snapshot = _Snapshot(expires_at=now_mono + _SNAPSHOT_TTL_SECONDS, sponsors=tuple(sponsors), selves=selves)
    return _snapshot


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def get_random_ad_banner(lang: str, platform: str) -> dict | None:
    """
    バナー広告を1つランダムに抽選して返す。

    抽選フロー:
        1. 掲載期間内（config.json の start_at <= 今 < end_at）の sponsor_* フォルダを全て列挙
        2. スポンサー数 < AD_BANNER_MAX_SPONSORS ならば、
           プラットフォームに応じた self フォルダ（self_web or self_ios or self_android）も候補に追加
        3. 候補フォルダからランダムに1フォルダを選択（クリエイター間の公平性を担保）
        4. フォルダ内の config.json のうち、現在の lang に合致するバナーを抽出
        5. 合致バナーからランダムに1つ選択

    Returns:
        { "image_url": str, "click_url": str | None } または None（バナーなし）
    """
    snapshot = _get_snapshot()
    if snapshot is None:
        return None

    now = _now()
    # 掲載期間内の sponsor_* フォルダ（期間外のものはスポンサー数にも数えない）
    sponsor_folders = [entry for entry in snapshot.sponsors if entry.is_live(now)]
    sponsor_count = len(sponsor_folders)

    # self フォルダをプラットフォームに応じて選択
    self_folder_name = "self_web" if platform == "web" else "self_ios" if platform == "ios" else "self_android"
    self_folder = snapshot.selves.get(self_folder_name)

    # 候補フォルダを決定
    candidate_folders: list[_BannerFolder] = list(sponsor_folders)
    if sponsor_count < AD_BANNER_MAX_SPONSORS and self_folder is not None and self_folder.is_live(now):
        candidate_folders.append(self_folder)

    if not candidate_folders:
        return None

    # 候補フォルダをシャッフル（順序をランダムに）
    random.shuffle(candidate_folders)

    for chosen_folder in candidate_folders:
        # 現在の lang に合致するバナーを絞り込む（画像の有無は読み直しのときに確認済み）
        filtered = [
            b for b in chosen_folder.banners
            if b.get("lang") is None or b.get("lang") == lang
        ]
        if not filtered:
            continue

        chosen_banner = random.choice(filtered)
        file_name: str = chosen_banner["file"]
        return {
            "image_url": f"{AD_BANNER_STATIC_PREFIX}/{chosen_folder.name}/{file_name}",
            "click_url": chosen_banner.get("url") or chosen_folder.default_url,
        }

    # 全てのフォルダを確認しても表示可能なバナーがなかった場合
    return None


def _load_config(folder: Path) -> dict | None:
    """フォルダ内の config.json を読み込む。存在しない・不正な場合は None を返す。"""
    config_path = folder / "config.json"
    if not config_path.exists():
        return None
    try:
        with config_path.open(encoding="utf-8") as f:
            config = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    return config if isinstance(config, dict) else None
