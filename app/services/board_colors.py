"""掲示板のカード・チャットヘッダーのグラデーション色のダークモード版。

ライトモードの色（パステル）を暗い背景になじむ色に1色ずつ対応させる。
ライトモードの色を足したときは、ここにもダークモードの色を足すこと（無い色は既定のグレーになる）。
"""
from __future__ import annotations

# ライトモードの色 -> ダークモードの色
BOARD_DARK_COLORS: dict[str, str] = {
    # --- チーム募集掲示板（カテゴリ・モード別） ---
    "#ffd4d4": "#684041", "#d4d7ff": "#48496e",  # ranked
    "#fff0d4": "#5a4a2e", "#eeffd4": "#46532b",  # friendly
    "#ffd4e6": "#663f51",  # event
    "#ffe7d4": "#5f4833", "#fff6d4": "#5b4a2a",  # others（トロフィー）
    "#d4f6ff": "#2d535d", "#ffd4f9": "#61405e",  # 5v5
    "#e4ffd4": "#3f5530", "#d4ffd4": "#365637",  # duoShowdown
    "#d4ffe2": "#2d573e", "#d4ffec": "#2a5746",  # trioShowdown
    "#eed4ff": "#594266", "#f9d4ff": "#5e4162",  # gemGrab
    "#d4d4ff": "#49486f", "#dfd4ff": "#51466c",  # brawlBall
    "#fed4ff": "#5f4160", "#ffd4ec": "#663f56",  # heist
    "#d4fffd": "#265655", "#d4f9ff": "#2b555a",  # bounty
    "#ffd4e4": "#673f4f",  # hotZone
    "#fff1d4": "#5e492e",  # knockout
    # --- なんでも掲示板（カテゴリ別） ---
    "#d4e6ff": "#3c4e67", "#d4f0ff": "#335161",  # chat
    "#ffeed4": "#5b4a2f",  # question
    "#d8f7cb": "#3d5531", "#c5f2b1": "#445e2e",  # tournament
    "#e8d4ff": "#564368", "#f0d4ff": "#594266",  # offtopic
    "#ffe2c4": "#624728", "#ffd3a6": "#654624",  # brawl_info
    "#3a3a3a": "#2a2a2a", "#4a4a4a": "#383838",  # x
    "#4752c4": "#363e9c", "#5865f2": "#4450c4",  # discord
    "#ffd4de": "#67404a",  # youtube
    "#ffd4ef": "#653f58",  # tiktok
    # --- テーマ掲示板のキャラ（レアリティ別） ---
    "#d4f5f7": "#2e5557", "#c4f0f3": "#245659",  # 1
    "#d4f5d4": "#365637", "#c4f0c4": "#305b3a",  # 2
    "#d4e0f8": "#414d66", "#c4d6f5": "#3c4e6c",  # 3
    "#f0d4fb": "#5b4265", "#eac4f8": "#62406e",  # 4
    "#fad4d4": "#664142", "#f5c4c4": "#6c3f40",  # 5
    "#f9f5d4": "#5e522a", "#f5f0c4": "#625222",  # 6
    "#faefd4": "#5b4a30", "#f5e8c4": "#5e4927",  # 7
    # --- テーマ掲示板のマップ（モード別） ---
    "#ecd4fb": "#594266", "#e3c4f8": "#584267",  # gemGrab
    "#d8dafa": "#494969", "#cacdf7": "#48486f",  # brawlBall
    "#f8d4e9": "#634156", "#f5c4e0": "#653f56",  # heist
    "#d4eefa": "#35525f", "#c4e6f7": "#2d5264",  # bounty
    "#fad4d5": "#664143", "#f7c4c5": "#6c3e40",  # hotZone
    "#faecd0": "#594b2e", "#f7e3bd": "#5e4a22",  # knockout
    "#e2f3d4": "#435335", "#d6eec4": "#40552f",  # showdown
    "#e5e0f8": "#4f4961", "#dbd4f5": "#4f4767",  # hockey
    "#fae2d0": "#5f4735", "#f7d6bd": "#65462c",  # orange
    "#d4e2f8": "#3f4e65", "#c4d7f5": "#3b4e6a",  # takedown
    "#fadada": "#644344", "#f7cccc": "#684041",  # loneStar
    "#d0f0e8": "#32554e", "#bfe9df": "#28574e",  # emerald
    # --- 既定色 ---
    "#e8e8e8": "#4d4d4d", "#dcdcdc": "#454545",  # default
}
BOARD_DARK_DEFAULT_COLOR = "#4a4a4a"


def board_dark_color(color: str | None) -> str:
    """ライトモードのグラデーション色に対応するダークモードの色。"""
    if not color:
        return BOARD_DARK_DEFAULT_COLOR
    return BOARD_DARK_COLORS.get(color.lower(), BOARD_DARK_DEFAULT_COLOR)
