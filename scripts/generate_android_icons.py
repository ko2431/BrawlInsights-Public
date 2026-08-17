#!/usr/bin/env python3
"""Android ランチャーアイコン（余白付き角丸）を生成する。"""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ICON_SRC = ROOT / "assets" / "icon.png"
RES = ROOT / "android" / "app" / "src" / "main" / "res"

# アダプティブアイコン全体（丸や角丸四角の枠）に対する、中の角丸アイコンの大きさ。
# 1.0 で枠いっぱいになり、小さくするほど白/黒の余白が広がる。
# 目安: 0.58 だと狭い / 0.46 だと余白がはっきり見える / 0.40 だとさらに広い
ICON_RATIO = 0.46

# 中のアイコンの角丸。iOS アイコンに近い比率。余白とは別物。
CORNER_RATIO = 0.2237

LEGACY = {
    "ldpi": 36,
    "mdpi": 48,
    "hdpi": 72,
    "xhdpi": 96,
    "xxhdpi": 144,
    "xxxhdpi": 192,
}
FOREGROUND = {
    "ldpi": 81,
    "mdpi": 108,
    "hdpi": 162,
    "xhdpi": 216,
    "xxhdpi": 324,
    "xxxhdpi": 432,
}


def round_icon(im: Image.Image, radius_ratio: float = CORNER_RATIO) -> Image.Image:
    im = im.convert("RGBA")
    width, height = im.size
    radius = max(1, int(min(width, height) * radius_ratio))
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=255)
    out = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    out.paste(im, (0, 0))
    out.putalpha(mask)
    return out


def make_padded(rounded: Image.Image, size: int, ratio: float, bg: tuple[int, int, int, int] | None = None) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), bg or (0, 0, 0, 0))
    icon_size = max(1, int(size * ratio))
    icon_resized = rounded.resize((icon_size, icon_size), Image.Resampling.LANCZOS)
    offset = (size - icon_size) // 2
    canvas.paste(icon_resized, (offset, offset), icon_resized)
    return canvas


def main() -> None:
    rounded = round_icon(Image.open(ICON_SRC))
    make_padded(rounded, 1024, ICON_RATIO).save(ROOT / "assets" / "icon-foreground.png")

    for density, size in LEGACY.items():
        folder = RES / f"mipmap-{density}"
        folder.mkdir(parents=True, exist_ok=True)
        make_padded(rounded, size, ICON_RATIO, bg=(255, 255, 255, 255)).save(folder / "ic_launcher_round.png")

    for density, size in FOREGROUND.items():
        folder = RES / f"mipmap-{density}"
        folder.mkdir(parents=True, exist_ok=True)
        make_padded(rounded, size, ICON_RATIO).save(folder / "ic_launcher_foreground.png")

    print(f"Generated launcher icons with ICON_RATIO={ICON_RATIO}")


if __name__ == "__main__":
    main()
