"""DBやキャッシュから復元したキャラのマスタリーランクが、MeowAPIと同じくポイントから求まることを検証する。"""

from app.services.brawl_service import PlayerBrawler
from app.utils.utils import calc_mastery_rank


def test_rank_is_calculated_from_points_when_missing():
    # DBの player_brawlers にはポイント(mastery)しかない
    brawler = PlayerBrawler.from_dict({"brawler_id": 16000000, "mastery": 26456})
    assert brawler.mastery_rank == calc_mastery_rank(26456) == 9
    assert PlayerBrawler.from_dict({"brawler_id": 16000000, "mastery": 0}).mastery_rank == 0


def test_rank_from_cache_is_kept():
    brawler = PlayerBrawler.from_dict({"id": 16000000, "mastery": 26456, "mastery_rank": 9})
    assert brawler.mastery_rank == 9


def test_rank_stays_none_without_points():
    brawler = PlayerBrawler.from_dict({"id": 16000000, "mastery": None})
    assert brawler.mastery is None and brawler.mastery_rank is None
