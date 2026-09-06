"""
ad_banner_service.py
バナー広告の抽選ロジック。
ファイルシステムから ad_banners/ フォルダを走査し、config.json に基づいて
言語・プラットフォームを考慮した2段階ランダム抽選を行う。
"""
import json
import random
from pathlib import Path

# --- 定数 ---
AD_BANNER_DIR = Path(__file__).resolve().parent.parent / "static" / "images" / "ad_banners"
AD_BANNER_STATIC_PREFIX = "/static/images/featured_media"
AD_BANNER_MAX_SPONSORS = 10   # スポンサー上限人数。これ未満のときself広告も候補に入る
BANNER_EXTENSIONS = {".webp", ".png", ".jpg", ".jpeg"}


def get_random_ad_banner(lang: str, platform: str) -> dict | None:
    """
    バナー広告を1つランダムに抽選して返す。

    抽選フロー:
        1. sponsor_* フォルダを全て列挙
        2. スポンサー数 < AD_BANNER_MAX_SPONSORS ならば、
           プラットフォームに応じた self フォルダ（self_web or self_ios or self_android）も候補に追加
        3. 候補フォルダからランダムに1フォルダを選択（クリエイター間の公平性を担保）
        4. フォルダ内の config.json を読み込み、現在の lang に合致するバナーを抽出
        5. 合致バナーからランダムに1つ選択

    Returns:
        { "image_url": str, "click_url": str | None } または None（バナーなし）
    """
    if not AD_BANNER_DIR.exists():
        return None

    # sponsor_* フォルダを取得（存在するもののみ）
    sponsor_folders: list[Path] = sorted(
        p for p in AD_BANNER_DIR.iterdir()
        if p.is_dir() and p.name.startswith("sponsor_")
    )
    sponsor_count = len(sponsor_folders)

    # self フォルダをプラットフォームに応じて選択
    self_folder_name = "self_web" if platform == "web" else "self_ios" if platform == "ios" else "self_android"
    self_folder = AD_BANNER_DIR / self_folder_name

    # 候補フォルダを決定
    candidate_folders: list[Path] = list(sponsor_folders)
    if sponsor_count < AD_BANNER_MAX_SPONSORS and self_folder.exists():
        candidate_folders.append(self_folder)

    if not candidate_folders:
        return None

    # 候補フォルダをシャッフル（順序をランダムに）
    random.shuffle(candidate_folders)

    for chosen_folder in candidate_folders:
        # config.json を読み込む
        config = _load_config(chosen_folder)
        if config is None:
            continue

        default_url: str | None = config.get("default_url") or None
        banners: list[dict] = config.get("banners", [])

        # 現在の lang に合致するバナーを絞り込む
        filtered = [
            b for b in banners
            if b.get("lang") is None or b.get("lang") == lang
        ]

        if not filtered:
            continue

        # フォルダ内の候補バナーもシャッフルして有効なものを探す
        random.shuffle(filtered)
        for chosen_banner in filtered:
            file_name: str = chosen_banner.get("file", "")
            if not file_name:
                continue

            # 拡張子チェック
            if Path(file_name).suffix.lower() not in BANNER_EXTENSIONS:
                continue

            # 画像ファイルが実際に存在するかチェック
            image_path = chosen_folder / file_name
            if not image_path.exists():
                continue

            # 全てのチェックを通過したら結果を返す
            image_url = f"{AD_BANNER_STATIC_PREFIX}/{chosen_folder.name}/{file_name}"
            click_url: str | None = chosen_banner.get("url") or default_url

            return {
                "image_url": image_url,
                "click_url": click_url,
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
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
