"""統計データタブ・ツールタブ（ハブ）のコンテンツの定義と、画面に渡す形への組み立て。

バナー画像はやめ、カードは HTML/CSS で描く（templates/components/hub.html・css/components/hub.css・js/hub.js）。
検討メモは docs/plans/hub_renewal_plan_2026-11-25.md。

- コンテンツ（ITEMS）は id ごとに1つ。色・アイコン・タイトル（行ごと）・札などを持つ。
- タブ（HUBS）はサブタブの並びと、サブタブごとのデフォルトの並び（「すべて」と各サブタブで別々に持てる）。
- 並び替えはサブタブごとに保存する。未ログインは端末（localStorage）、ログイン中はアカウント（users.hub_orders）。
  保存しているのは id の並びだけで、表示のたびに今の定義と照らし合わせる（merge_order）。
- NEW・リニューアルの札とお知らせは、掲載の終わり（UTC の日付・その日の終わりまで）を持てる。None は無期限。
"""
from __future__ import annotations

import datetime
import math
from html import escape
from typing import Any

from app.core.templating import cached_url_for, version_at_least

UTC = datetime.timezone.utc

# カードの色（今のバナーの色相）。c1 が明るい側、c2 が濃い側
COLORS: dict[str, tuple[str, str]] = {
    "purple": ("#b85bd8", "#9b3ac8"),
    "blue": ("#4a86f2", "#2f63db"),
    "peach": ("#ffd6c7", "#ffbdb3"),
    "coral": ("#ffc7a3", "#ffab94"),
    "yellow": ("#fff28f", "#ffe36b"),
    "gold": ("#ffec80", "#ffd75a"),
    "lime": ("#d6f59f", "#bcec80"),
    "cyan": ("#a6f0ff", "#7fdcfb"),
    "mint": ("#94f0c4", "#6ce2bb"),
    "lavender": ("#e6ccff", "#cdabfa"),
    "magenta": ("#ffc7eb", "#f9a8de"),
    "orange": ("#ffe19e", "#ffc985"),
    "dusk": ("#76637f", "#5a4866"),
    "gray": ("#6c6c70", "#4e4e52"),
}

# 複数のアイコンを並べるカードでは、% の幅ではなく見える部分（不透明な部分）の面積をそろえる。
# 画像ごとの (高さ/幅, 不透明な部分の割合)。画像から測った値
ICON_META: dict[str, tuple[float, float]] = {
    "ranked_icons/pro.png": (1435 / 1710, 0.68),
    "ranked_icons/legendary.png": (1326 / 1557, 0.81),
    "ranked_icons/diamond.png": (1210 / 1245, 0.80),
    "ui/trophy.png": (286 / 320, 0.71),
    "mode_icons/3v3.png": (87 / 95, 0.86),
    "ui/total_prestige.png": (472 / 560, 0.73),
    "mode_icons/gemGrab.png": (171 / 159, 0.67),
    "mode_icons/soloShowdown.png": (181 / 159, 0.71),
    "ui/starrdrop.png": (1.0, 0.44),
    "ui/chaosdrop.png": (1.0, 0.44),
}

# 複数アイコン: (画像, 中心x%, 中心y%, 見える部分を正方形にしたときの一辺%, 重なり順)
BOXES = (("ui/starrdrop.png", 40, 42, 56, 1), ("ui/chaosdrop.png", 74, 73, 34, 2))

TAGS = {
    "ranked": ("5000万件以上の勝敗データから毎日自動更新", "Automatically Updated Daily"),
    "multi": ("8000万件以上の勝敗データから毎日自動更新", "Automatically Updated Daily"),
    "active": ("アクティブプレイヤーデータから毎日自動更新", "Automatically Updated Daily"),
    "profile": ("SNSでの共有や記録用に使える8種類の画像を生成", "Generate 8 types of images"),
    "guide": ("おすすめガジェ/スタパ/ギア情報が毎日自動更新", "Automatically Updated Daily"),
}

BADGE_LABELS = {
    "new": ("NEW", "NEW"),
    "renewal": ("リニューアル", "Renewed"),
    "early": ("先行公開", "Early Access"),
}


def _item(id: str, color: str, icon: Any, ja: tuple[str, ...], en: tuple[str, ...], *, endpoint: str | None = None,
          href: str | None = None, tag: str | None = None, unofficial: bool = False, slim: bool = False,
          icon_scale: float = 1.0, live: str | None = None, live_ja: tuple[str, ...] | None = None,
          live_en: tuple[str, ...] | None = None, badge: str | None = None, badge_until: datetime.date | None = None,
          external: bool = False) -> dict[str, Any]:
    """コンテンツ1つ。endpoint は url_for の名前、href は /{lang} からの相対（?type= 付きのランキング）か外部 URL。
    slim: スマホ幅で細いカードにする（今のスリムバナーと同じ）。live: 下に帯を出すライブ表示の種類。
    badge: 'new' / 'renewal'。badge_until はその日（UTC）の終わりまで出す。None なら無期限"""
    return {
        "id": id, "color": color, "icon": icon, "ja": ja, "en": en, "endpoint": endpoint, "href": href, "tag": tag,
        "unofficial": unofficial, "slim": slim, "icon_scale": icon_scale, "live": live, "live_ja": live_ja,
        "live_en": live_en, "badge": badge, "badge_until": badge_until, "external": external,
    }


_CUSTOM = "/stats/ranking/custom?type="

ITEMS: dict[str, dict[str, Any]] = {i["id"]: i for i in (
    # ---- 統計データ ----
    _item("ranked_tier_list", "purple", "ui/ranked.png", ("ガチバトル", "最強キャラTier表"), ("Ranked", "Brawler Tier List"),
          endpoint="ranked_tier_list", tag="ranked"),
    _item("ranked_rank_distribution", "peach",
          (("ranked_icons/pro.png", 50, 27, 46, 2), ("ranked_icons/legendary.png", 26, 74, 41, 1), ("ranked_icons/diamond.png", 74, 74, 41, 1)),
          ("ガチバトル", "ランク分布"), ("Ranked Rank", "Distribution"), endpoint="ranked_rank_distribution", slim=True),
    _item("ranked_ranking", "blue", "ui/ranked.png", ("ガチバトル", "ランキング"), ("Ranked", "Leaderboards"),
          endpoint="ranked_ranking", slim=True),
    _item("multiplayer_tier_list", "yellow", "ui/trophy.png", ("マルチプレイ", "最強キャラTier表"), ("Multiplayer", "Brawler Tier List"),
          endpoint="multiplayer_tier_list", tag="multi"),
    _item("skin_ranking", "cyan", "ui/skin_super_rare.png", ("スキン", "使用率ランキング"), ("Skin", "Usage Ranking"),
          endpoint="skin_ranking", tag="active"),
    _item("player_stats_distribution", "lavender",
          (("ui/trophy.png", 50, 27, 46, 2), ("mode_icons/3v3.png", 25, 74, 36, 1), ("ui/total_prestige.png", 74, 74, 41, 1)),
          ("プレイヤー", "各種データ分布"), ("Player Stats", "Distribution"), endpoint="player_stats_distribution", slim=True),
    _item("player_ranking", "gold", "ui/trophy.png", ("総合トロフィー", "ランキング"), ("Player", "Leaderboards"),
          endpoint="player_ranking", slim=True),
    _item("brawler_ranking", "orange", "ui/brawlers.png", ("キャラクター", "ランキング"), ("Brawler", "Leaderboards"),
          endpoint="brawler_ranking", slim=True, icon_scale=1.05),
    _item("club_ranking", "coral", "ui/club.webp", ("クラブ", "ランキング"), ("Club", "Leaderboards"), endpoint="club_ranking", slim=True),
    _item("prestige_borders", "mint", "ui/prestige.png", ("旧トップランカー", "ボーダー一覧"), ("Prestige", "Borders"),
          endpoint="prestige_borders", slim=True),
    _item("title_ranking", "orange", "ui/title.png", ("キャッチフレーズ", "使用率ランキング"), ("Title", "Usage Ranking"),
          endpoint="title_ranking", tag="active", slim=True),
    _item("player_icon_ranking", "blue", "player_icon/28000000.webp", ("プレイヤーアイコン", "使用率ランキング"), ("Player Icon", "Usage Ranking"),
          endpoint="player_icon_ranking", tag="active", slim=True, icon_scale=0.8),
    _item("pins_ranking", "purple", "ui/pin_normal.png", ("バトルカードピンズ", "使用率ランキング"), ("Battle Card Pins", "Usage Ranking"),
          endpoint="pins_ranking", tag="active", slim=True),
    _item("battle_card_ranking", "cyan", "battle_card/85000007.webp", ("バトルカード背景", "使用率ランキング"), ("Battle Card Frame", "Usage Ranking"),
          endpoint="battle_card_ranking", tag="active", slim=True, icon_scale=1.14),
    _item("play_time", "gold", "ui/refresh.png", ("推定プレイ時間", "ランキング"), ("Play Time", "Leaderboards"),
          href=_CUSTOM + "play_time", unofficial=True, slim=True),
    _item("team_victories", "lime", "mode_icons/3v3.png", ("勝利数", "ランキング"), ("Victories", "Leaderboards"),
          href=_CUSTOM + "team_victories", unofficial=True, slim=True, icon_scale=0.93),
    _item("max_winstreak", "coral", "ui/win_streak.png", ("連勝数", "ランキング"), ("Win Streak", "Leaderboards"),
          href=_CUSTOM + "max_winstreak&limit=100", unofficial=True, slim=True, icon_scale=1.12),
    _item("fame_points", "cyan", "ui/credit.png", ("名声ポイント", "ランキング"), ("Fame Points", "Leaderboards"),
          href=_CUSTOM + "fame_points&limit=100", unofficial=True, slim=True, icon_scale=0.88),
    _item("record_points", "dusk", "record_levels/12.png", ("記録ポイント", "ランキング"), ("Record Pts.", "Leaderboards"),
          href=_CUSTOM + "record_points&limit=100", unofficial=True, slim=True),
    _item("prestige", "mint", "ui/prestige.png", ("旧トップランカー", "所持数ランキング"), ("Prestige", "Leaderboards"),
          href=_CUSTOM + "prestige&limit=100", unofficial=True, slim=True),
    _item("total_mastery", "gold", "ui/mastery_gold.png", ("合計旧マスタリー", "ランキング"), ("Old Mastery", "Leaderboards"),
          href=_CUSTOM + "total_mastery&limit=100", unofficial=True, slim=True),

    # ---- ツール ----
    _item("tournament", "lime", "mode_icons/challenge.png", ("大会開催", "ツール"), ("Community", "Tournament Hub"),
          endpoint="tournament_hub", live="tournament", live_ja=("大会開催ツール",), live_en=("Tournament Hub",)),
    _item("pick_tool", "coral", "ui/ranked.png", ("ガチバトル", "ピック提案ツール"), ("Ranked Pick", "Suggestion Tool"),
          endpoint="pick_tool", tag="ranked"),
    _item("ranked_maps", "purple", "ui/ranked.png", ("ガチバトル", "マップ一覧"), ("Ranked", "Map List"), endpoint="ranked_maps", slim=True),
    _item("profile_image", "blue", "ui/total_prestige.png", ("プロフィール画像", "生成ツール"), ("Profile Image", "Generator"),
          endpoint="profile_image_tool", tag="profile"),
    _item("brawler_guide", "dusk", "ui/brawlers.png", ("キャラクター", "図鑑"), ("Brawler", "Stats Guide"),
          endpoint="brawler_guide_menu", tag="guide", icon_scale=1.05),
    _item("cost_calc", "magenta", "ui/power_point.png", ("育成", "計算機"), ("Level-Up Cost", "Calculator"), endpoint="cost_calc", slim=True),
    _item("random_brawler", "cyan", "ui/brawlers.png", ("キャラクター", "ルーレット"), ("Random Brawler", "Selector"),
          endpoint="random_brawler", slim=True, icon_scale=1.05),
    _item("map_rotation", "lime", "ui/refresh.png", ("マップ", "周期"), ("Map", "Rotation"), endpoint="map_rotation",
          live="rotation", live_ja=("マップ周期",), live_en=("Map Rotation",)),
    _item("all_maps", "yellow", (("mode_icons/gemGrab.png", 38, 40, 56, 1), ("mode_icons/soloShowdown.png", 73, 72, 36, 2)),
          ("マップ", "一覧"), ("All", "Maps"), endpoint="all_maps", slim=True),
    _item("trophy_table", "cyan", "ui/trophy.png", ("トロフィー", "増減表"), ("Trophy Change", "Table"), endpoint="trophy_table", slim=True),
    _item("starrdrop_chances", "yellow", BOXES, ("ボックス", "確率表"), ("Box", "Chances"), endpoint="starrdrop_chances"),
    _item("box_simulator", "mint", BOXES, ("ボックス", "シミュレーター"), ("Box", "Simulator"), endpoint="box_simulator", slim=True),
    _item("starrdrop_calc", "orange", BOXES, ("ボックス", "期待値計算機"), ("Box EV", "Calculator"), endpoint="starrdrop_calc", slim=True),
    _item("reward_calc", "blue", "ui/brawl_pass.png", ("ブロスタパス / プロパス", "報酬量計算機"), ("Brawl Pass Reward", "Calculator"),
          endpoint="reward_calc", slim=True),
    _item("trophy_reward_calc", "lavender", "ui/trophy.png", ("トロフィー", "目標報酬計算機"), ("Trophy Reward", "Calculator"),
          endpoint="trophy_reward_calc", slim=True),
    _item("brawl_videos", "peach", "ui/brawltv.png", ("ブロスタ", "おすすめ動画"), ("Featured", "Brawl Videos"), endpoint="brawl_videos", slim=True),
    _item("app_faq", "gray", "ui/mystery.png", ("アプリの", "よくある質問"), ("App", "FAQ"), endpoint="app_faq", slim=True),

    # ---- お役立ち外部リンク（{store}・{event} は言語で入れ替える） ----
    _item("ext_store", "purple", "ui/shop.png", ("Supercell Store",), ("Supercell Store",),
          href="https://store.supercell.com/{store}/brawlstars", external=True, slim=True, icon_scale=0.94),
    _item("ext_rewards", "dusk", "ui/scid.png", ("Supercell ID リワード",), ("Supercell ID Rewards",),
          href="https://id.supercell.com/{store}/brawlstars/", external=True, slim=True, icon_scale=0.85),
    _item("ext_champ", "lime", "ui/challenge_small.png", ("ブロスタ チャンピオンシップ",), ("Brawl Stars Championship",),
          href="https://event.supercell.com/brawlstars/{event}/", external=True, slim=True, icon_scale=1.07),
    _item("ext_fankit", "cyan", "ui/starrdrop.png", ("ファンキット",), ("Fan Kit",),
          href="https://fankit.supercell.com/d/YvtsWV4pUQVm/game-assets", external=True, slim=True, icon_scale=1.15),
    _item("ext_pinmaker", "coral", "ui/pin_pack.png", ("ピンズメーカー",), ("Pin Maker",),
          href="https://pinmaker.supercell.com/{event}", external=True, slim=True),
)}

EXTERNAL = ("ext_store", "ext_rewards", "ext_champ", "ext_fankit", "ext_pinmaker")


def _sub(id: str, icon: str, icon_class: str, ja: str, en: str, desc_ja: str, desc_en: str, order: tuple[str, ...], *,
         suggest: tuple[str, ...] = (), external: tuple[str, ...] = (), external_as_suggest: bool = False,
         tn_badge: bool = False) -> dict[str, Any]:
    """サブタブ1つ。order はこのサブタブのデフォルトの並び。suggest は「こちらをお探しですか？」（点線の枠）、
    external は「お役立ち外部リンク」（実線の枠）。external_as_suggest は外部リンクを点線の「こちらをお探しですか？」に入れる"""
    return {
        "id": id, "icon": icon, "icon_class": icon_class, "ja": ja, "en": en, "desc_ja": desc_ja, "desc_en": desc_en,
        "order": order, "suggest": suggest, "external": external, "external_as_suggest": external_as_suggest, "tn_badge": tn_badge,
    }


_STATS_RANKED = ("ranked_tier_list", "ranked_rank_distribution", "ranked_ranking")
_STATS_BRAWLERS = ("multiplayer_tier_list", "skin_ranking", "player_stats_distribution", "player_ranking", "brawler_ranking",
                   "club_ranking", "prestige_borders")
_STATS_RANKINGS = ("title_ranking", "player_icon_ranking", "pins_ranking", "battle_card_ranking", "play_time", "team_victories",
                   "max_winstreak", "fame_points", "record_points", "prestige", "total_mastery")
_TOOLS_RANKED = ("pick_tool", "ranked_maps")
_TOOLS_BRAWLERS = ("profile_image", "brawler_guide", "cost_calc", "random_brawler")
_TOOLS_MAPS = ("map_rotation", "all_maps", "trophy_table")
_TOOLS_REWARDS = ("starrdrop_chances", "box_simulator", "starrdrop_calc", "reward_calc", "trophy_reward_calc")
_TOOLS_OTHERS = ("tournament", "brawl_videos", "app_faq")

# お知らせ（タブの上の1行。× で閉じると、その id は出さない）。until は UTC の日付（その日の終わりまで）、None は無期限
UI_NOTICE = {
    "id": "hub-ui-2026-11",
    "ja": "<b>タブのUI</b>が刷新。ダークモードに対応し、コンテンツの<b>並び替え</b>もできるように！",
    "en": "The <b>tab UI</b> has been renewed, with dark mode support and <b>reordering</b> of contents!",
    "href": None,
    "until": datetime.date(2026, 12, 5),
}

HUBS: dict[str, dict[str, Any]] = {
    "stats": {
        "title_ja": "統計データ", "title_en": "Statistics", "ad_unit": "stats_hub", "notice": UI_NOTICE,
        "subtabs": (
            _sub("all", "mastery_gold.png", "mastery", "すべて", "All",
                 "このタブでは、Tier表やランキングなどのブロスタのさまざまな統計データを閲覧できます。",
                 "On this tab, you can view a variety of Brawl Stars statistics, including Tier lists and leaderboards.",
                 _STATS_RANKED + _STATS_BRAWLERS + _STATS_RANKINGS),
            _sub("ranked", "ranked.png", "ranked", "ガチバトル", "Ranked",
                 "このタブでは、ガチバトルのTier表やランク分布、ランキングを閲覧できます。",
                 "On this tab, you can view the Ranked Tier List, rank distribution, and leaderboards.",
                 _STATS_RANKED, suggest=("pick_tool", "ranked_maps")),
            _sub("brawlers", "trophy.png", "trophy", "キャラクター・<wbr>トロフィー", "Brawler &<wbr> Trophy",
                 "このタブでは、マルチプレイTier表やスキン・トロフィーのランキング、各種データ分布などを閲覧できます。",
                 "On this tab, you can view the Multiplayer Tier List, skin ranking, trophy leaderboards, various stat distributions, etc.",
                 _STATS_BRAWLERS, suggest=("brawler_guide",)),
            _sub("rankings", "leaderboards.png", "leaderboards", "非公式<wbr>ランキング", "Unofficial<wbr> Rankings",
                 "このタブでは、キャッチフレーズやアイコン、名声などのさまざまな非公式ランキングを閲覧できます。",
                 "On this tab, you can view various unofficial rankings such as titles, player icons, and fame.",
                 _STATS_RANKINGS, suggest=("skin_ranking",)),
        ),
    },
    "tools": {
        "title_ja": "ツール", "title_en": "Tools", "ad_unit": "tools_hub", "notice": UI_NOTICE,
        "subtabs": (
            # 大会開催ツールは「すべて」でもいちばん先頭（今と同じ）
            _sub("all", "mastery_gold.png", "mastery", "すべて", "All",
                 "このタブでは、ブロスタに関連するさまざまなツールを利用できます。",
                 "On this tab, a variety of tools related to Brawl Stars are available.",
                 ("tournament",) + _TOOLS_RANKED + _TOOLS_BRAWLERS + _TOOLS_MAPS + _TOOLS_REWARDS + ("brawl_videos", "app_faq"),
                 external=EXTERNAL),
            _sub("ranked", "ranked.png", "ranked", "ガチ<wbr>バトル", "Ranked",
                 "このタブでは、ガチバトルのさまざまなツールを利用できます。",
                 "On this tab, you can use various tools related to Ranked.",
                 _TOOLS_RANKED, suggest=("ranked_tier_list", "ranked_rank_distribution", "ranked_ranking")),
            _sub("brawlers", "brawlers.png", "brawlers", "キャラ<wbr>クター", "Brawler",
                 "このタブでは、キャラクター関連のさまざまなツールを利用できます。",
                 "On this tab, you can use various tools related to brawlers.",
                 _TOOLS_BRAWLERS, suggest=("skin_ranking", "title_ranking")),
            _sub("maps", "trophy.png", "trophy", "マップ・<wbr>トロフィー", "Maps &<wbr> Trophy",
                 "このタブでは、マップ周期やマップ一覧、トロフィー増減表を利用できます。",
                 "On this tab, you can use the map rotation, the map list, and the trophy change table.",
                 _TOOLS_MAPS, suggest=("ranked_maps", "profile_image", "multiplayer_tier_list")),
            _sub("rewards", "starrdrop.png", "starrdrop", "報酬関連", "Rewards",
                 "このタブでは、ブロスタパスや各種ドロップ／ボックスなどの、さまざまな報酬関連ツールを利用できます。",
                 "On this tab, you can use various tools related to rewards such as Brawl Pass, drop chances, and more.",
                 _TOOLS_REWARDS, external=EXTERNAL[:3], external_as_suggest=True),
            _sub("others", "skin_other.png", "other", "その他", "Others",
                 "このタブでは、大会開催ツールやお役立ち外部リンクなど、その他のツールを利用できます。",
                 "On this tab, you can use other tools such as the Community Tournament Hub and useful external links.",
                 _TOOLS_OTHERS, external=EXTERNAL, tn_badge=True),
        ),
    },
}

# 並び替えの保存の上限（1サブタブの id の数・id の長さ）
ORDER_MAX_IDS = 60
ORDER_MAX_ID_LEN = 40


def default_order(hub: str, sub: str) -> list[str]:
    for s in HUBS[hub]["subtabs"]:
        if s["id"] == sub:
            return list(s["order"])
    return []


def merge_order(saved: list[str] | None, defaults: list[str]) -> list[str]:
    """保存した並びを今の定義に合わせる（js/hub.js の mergeOrder と同じ）。
    もう無い（別のサブタブへ移った）id は読み飛ばし、増えた（移ってきた）id はデフォルトの並びで直前にある id のすぐ後ろへ
    （直前のものが無ければ先頭）。"""
    if not saved:
        return list(defaults)
    allowed = set(defaults)
    result: list[str] = []
    for i in saved:
        if i in allowed and i not in result:
            result.append(i)
    for index, i in enumerate(defaults):
        if i in result:
            continue
        before = next((p for p in reversed(defaults[:index]) if p in result), None)
        result.insert(result.index(before) + 1 if before else 0, i)
    return result


def clean_orders(raw: Any) -> dict[str, dict[str, list[str]]]:
    """アカウントに保存する並び（{"stats": {"all": [...], ...}, "tools": {...}}）を、知っている hub・サブタブ・id だけにする。
    デフォルトと同じ並びは保存しない（デフォルトの変更に追従させるため）。
    一度でも保存したタブは、並びが空（すべてデフォルト）でもキーを残す。キーが無いタブだけを「アカウントに並びがない」
    （初めてログインした・まだ並べ替えていない）とみなし、端末の並びを引き継ぐ。空を残さないと、「デフォルトに戻す」の
    あとに別の端末にあるアカウントの写しが引き継がれ、元の並びに戻ってしまう"""
    result: dict[str, dict[str, list[str]]] = {}
    if not isinstance(raw, dict):
        return result
    for hub, subs in raw.items():
        if hub not in HUBS or not isinstance(subs, dict):
            continue
        result.setdefault(hub, {})
        for sub, ids in subs.items():
            defaults = default_order(hub, sub)
            if not defaults or not isinstance(ids, list):
                continue
            ids = [i for i in ids[:ORDER_MAX_IDS] if isinstance(i, str) and len(i) <= ORDER_MAX_ID_LEN]
            merged = merge_order(ids, defaults)
            if merged != defaults:
                result.setdefault(hub, {})[sub] = merged
    return result


def _active(until: datetime.date | None, now: datetime.datetime) -> bool:
    return until is None or now.date() <= until


def _icon_html(icon: Any, img: str, scale: float) -> str:
    """アイコンの HTML。複数のときは見える部分の面積が指定どおりになるよう幅を決め、中心の位置に置く"""
    if isinstance(icon, str):
        style = f' style="transform:scale({scale:g})"' if scale != 1 else ""
        return f'<img class="hub-ic" src="{img}{escape(icon)}" alt="" loading="lazy" decoding="async"{style}>'
    parts = []
    for src, cx, cy, size, z in icon:
        ratio, fill = ICON_META.get(src, (1.0, 0.75))
        w = size / math.sqrt(ratio * fill)
        h = w * ratio
        parts.append(
            f'<img src="{img}{escape(src)}" alt="" loading="lazy" decoding="async" '
            f'style="left:{cx - w / 2:.1f}%;top:{cy - h / 2:.1f}%;width:{w:.1f}%;z-index:{z}">'
        )
    return '<span class="hub-cluster">' + "".join(parts) + "</span>"


def _card(item: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """1枚のカードを画面に出す形にする"""
    ja = ctx["lang"] == "ja"
    if item["endpoint"]:
        href = str(cached_url_for({"request": ctx["request"]}, item["endpoint"], lang=ctx["lang"]))
    elif item["external"]:
        href = item["href"].format(store="ja" if ja else "en", event="jp" if ja else "en")
    else:
        href = f"/{ctx['lang']}{item['href']}"
    title = item["ja"] if ja else item["en"]
    tag = TAGS[item["tag"]][0 if ja else 1] if item["tag"] else None
    badge = item["badge"] if item["badge"] and _active(item["badge_until"], ctx["now"]) else None
    if item["id"] == "tournament" and ctx.get("tournament_early"):
        badge = "early"
    c1, c2 = COLORS[item["color"]]
    return {
        "id": item["id"],
        "href": href,
        "external": item["external"],
        "style": f"--c1:{c1};--c2:{c2}",
        "icon_html": _icon_html(item["icon"], ctx["img"], item["icon_scale"]),
        "lines": list(title),
        "full": ("" if ja else " ").join(title),
        "live_lines": list((item["live_ja"] if ja else item["live_en"]) or title),
        "tag": tag,
        "unofficial": item["unofficial"],
        "slim": item["slim"],
        "badge": badge,
        "badge_label": BADGE_LABELS[badge][0 if ja else 1] if badge else None,
        "live": item["live"],
    }


def visible_ids(request, *, tournament_visible: bool) -> set[str]:
    """この人に出すコンテンツ。プロフィール画像生成ツールはアプリの版が 14.0 以上か Web だけ、大会開催ツールは段階公開を通る人だけ"""
    platform = getattr(request.state, "platform", "web")
    version = getattr(request.state, "app_client_version", None)
    hidden: set[str] = set()
    if not (platform == "web" or (platform in ("ios", "android") and version_at_least(version, "14.0"))):
        hidden.add("profile_image")
    if not tournament_visible:
        hidden.add("tournament")
    return set(ITEMS) - hidden


def build(request, lang: str, hub: str, *, account_orders: dict | None = None, tournament_visible: bool = False,
          tournament_early: bool = False, rotation: dict | None = None, tournament: dict | None = None) -> dict[str, Any]:
    """テンプレートに渡すハブの中身。account_orders はログイン中の人のアカウントの並び（未ログインは None。端末の並びは JS が当てる）。
    rotation はマップ周期の帯の材料（rotation_data）、tournament は大会開催ツールの帯（{"status": ..., "api": 取り直す URL}）"""
    now = datetime.datetime.now(UTC)
    ja = lang == "ja"
    img = static_images_base(request)
    ctx = {"request": request, "lang": lang, "img": img, "now": now, "tournament_early": tournament_early}
    shown = visible_ids(request, tournament_visible=tournament_visible)
    cards: dict[str, dict[str, Any]] = {}

    def card(item_id: str) -> dict[str, Any]:
        if item_id not in cards:
            cards[item_id] = _card(ITEMS[item_id], ctx)
        return cards[item_id]

    labels = {s["id"]: (s["ja"] if ja else s["en"]).replace("<wbr>", "") for s in HUBS[hub]["subtabs"]}
    category_of: dict[str, str] = {}
    for s in HUBS[hub]["subtabs"]:
        if s["id"] != "all":
            for i in s["order"]:
                category_of.setdefault(i, labels[s["id"]])

    # アカウントにこのタブのキーがあるか（一度でも保存したか）。空でもあれば、端末の並びは引き継がない
    account_hub = account_orders.get(hub) if account_orders is not None else None
    orders = account_hub or {}
    subtabs = []
    for s in HUBS[hub]["subtabs"]:
        defaults = [i for i in s["order"] if i in shown]
        order = merge_order(orders.get(s["id"]), defaults) if orders.get(s["id"]) else defaults
        external = [card(i) for i in s["external"] if i in shown]
        subtabs.append({
            "id": s["id"],
            "icon": s["icon"],
            "icon_class": s["icon_class"],
            "label": s["ja"] if ja else s["en"],
            "desc": s["desc_ja"] if ja else s["desc_en"],
            "defaults": defaults,
            "custom": order != defaults,
            "cards": [card(i) for i in order],
            "suggest": [card(i) for i in s["suggest"] if i in shown] + (external if s["external_as_suggest"] else []),
            "external": [] if s["external_as_suggest"] else external,
            "tn_badge": s["tn_badge"] and "tournament" in shown,
            # 並び替えのリストの1行（id・名前・分類・アイコン）
            "edit": [{"id": i, "name": card(i)["full"], "cat": category_of.get(i, ""), "icon": card(i)["icon_html"],
                      "style": card(i)["style"]} for i in order],
        })

    notice = HUBS[hub]["notice"]
    logged_in = account_orders is not None
    js = {
        "hub": hub,
        "loggedIn": logged_in,
        "hasAccountOrders": account_hub is not None,
        "saveUrl": str(cached_url_for({"request": request}, "account_hub_order", lang=lang)) if logged_in else None,
        "subtabs": [s["id"] for s in subtabs],  # tojson はキーを並べ替えるので、サブタブの順はこちらで渡す
        "defaults": {s["id"]: s["defaults"] for s in subtabs},
        "edit": {s["id"]: s["edit"] for s in subtabs},
        "rotation": rotation,
        "tournament": tournament,
    }
    return {
        "id": hub,
        "title": HUBS[hub]["title_ja"] if ja else HUBS[hub]["title_en"],
        "ad_unit": HUBS[hub]["ad_unit"],
        "subtabs": subtabs,
        "notice": {"id": notice["id"], "html": notice["ja"] if ja else notice["en"], "href": notice["href"]}
        if notice and _active(notice["until"], now) else None,
        "js": js,
    }


def rotation_data(payload: dict, img: str) -> dict[str, Any]:
    """マップ周期の帯に渡す材料。マップ周期のページ（tools/map_rotation.html の getRotationTable）と同じく、
    直近の開始時刻（無ければ起点）・1枠の長さ・何番目か・周期のマップのモードから、次の切り替えを手元で計算する。
    切り替わるたびにサーバーへ問い合わせなくてよいように、周期ぶんのモードを持たせる。
    フリープレイのような独自のアイコンのスロットは入れない。
    戻り値: {"slots": [{"o": 起点のミリ秒, "d": 1枠の分, "i": 起点の枠のマップの番号, "m": [モードのキー...],
    "f": スロットのモードのキー（マップが未確定のとき）}], "icons": {モードのキー: [アイコン, 代わりのアイコン]}}
    呼ぶ前に map_mode_catalog.ensure_catalog を済ませておく（マップ・モードはメモリーのカタログから引く）"""
    from app.services.map_mode_catalog import get_map_by_id, get_mode_by_id, get_mode_slug_to_id, mode_icon_candidates

    slug_to_id = get_mode_slug_to_id()
    icons: dict[str, list[str]] = {}
    slots: list[dict[str, Any]] = []

    def mode_key(mode_id: int | None, slug: str | None) -> str | None:
        if not mode_id and not slug:
            return None
        key = str(mode_id or slug)
        if key not in icons:
            paths = mode_icon_candidates(mode_id, slug)
            icons[key] = [img + p.removeprefix("/images/") for p in paths[:2]]
        return key

    for slot in payload.get("slots") or []:
        if slot.get("iconPath") or not slot.get("maps"):
            continue
        origin = slot.get("latestStart") or slot.get("anchorStart")
        if not origin:
            continue
        slot_modes = [k for k in (mode_key(slug_to_id.get(s), s) for s in slot.get("icons") or []) if k]
        modes = []
        for m in slot["maps"]:
            info = get_map_by_id(m.get("map_id"))
            mode = get_mode_by_id(info.mode_id) if info and info.mode_id else None
            modes.append(mode_key(info.mode_id, mode.slug if mode else None) if info and info.mode_id else None)
        start = datetime.datetime.fromisoformat(origin)
        slots.append({
            "o": int(start.timestamp() * 1000),
            "d": int(slot.get("durationMinutes") or 1440),
            "i": int(slot.get("latestIndex") or 0),
            "m": modes,
            "f": slot_modes,
        })
    return {"slots": slots, "icons": icons}



# --- アカウントに保存した並び（users.hub_orders） ------------------------------------------------
ORDERS_CACHE_TTL = 24 * 60 * 60


def _orders_cache_key(user_id: int) -> str:
    return f"hub_orders:{user_id}"


async def load_account_orders(db, user_id: int) -> dict[str, dict[str, list[str]]]:
    """ハブを開いたときに、ログイン中の人のアカウントの並びを読む（Redis に1日キャッシュ。保存したら消す）"""
    import asyncpg

    from app.core.cache import get_cache, set_cache
    from app.core.logger import logger

    key = _orders_cache_key(user_id)
    cached = await get_cache(key)
    if isinstance(cached, dict):
        return cached
    try:
        raw = await db.fetchval("SELECT hub_orders FROM users WHERE id = $1", user_id)
    except asyncpg.UndefinedColumnError:
        # マイグレーション（3dab75401b11）の前。並びはデフォルトのまま出す
        return {}
    except Exception as e:
        logger.warning(f"ハブの並びの読み込みに失敗しました (user_id={user_id}): {e}")
        return {}
    orders = clean_orders(raw or {})
    await set_cache(key, orders, ttl=ORDERS_CACHE_TTL)
    return orders


async def save_account_orders(db, user_id: int, hub: str, updates: dict[str, Any]) -> dict[str, dict[str, list[str]]]:
    """サブタブごとの並びを保存する。updates は {サブタブ: [id...] / None}。None・デフォルトと同じ並びは消す"""
    from app.core.cache import delete_cache

    async with db.transaction():
        current = await db.fetchval("SELECT hub_orders FROM users WHERE id = $1 FOR UPDATE", user_id) or {}
        merged = {h: dict(subs) for h, subs in clean_orders(current).items()}
        subs = merged.setdefault(hub, {})
        for sub, ids in updates.items():
            if ids is None:
                subs.pop(sub, None)
            else:
                subs[sub] = ids
        orders = clean_orders(merged)
        await db.execute("UPDATE users SET hub_orders = $2 WHERE id = $1", user_id, orders)
    await delete_cache(_orders_cache_key(user_id))
    return orders


def static_images_base(request) -> str:
    """/static/images/ の URL（末尾の / 付き）。画像ごとに url_for を引かないよう、これに相対パスをつなぐ"""
    return str(cached_url_for({"request": request}, "static", path="/images/"))

