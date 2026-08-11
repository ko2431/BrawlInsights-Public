"""ミニゲーム用のカード画像定義。"""

MYSTERY_IMAGE = "ui/mystery.png"
BACK_SYMBOL = "mastery/empty.png"


def static_url_path(rel: str) -> str:
    """静的画像の公開パスを返す。"""
    return f"/static/images/{rel}"


def _spray(asset_id: int) -> str:
    return f"spray/{asset_id}.webp"

def _pin(asset_id: int) -> str:
    return f"pins/{asset_id}.webp"


CARD_ASSETS: dict[tuple[str, int], dict] = {
    ("card_flip_single", 2): {"ranks": {1: _spray(68000004), 2: _spray(68000098)}},
    ("card_flip_single", 3): {"ranks": {1: _spray(68000076), 2: _spray(68000007), 3: _spray(68000001)}},
    ("card_flip_single", 4): {"ranks": {1: _spray(68000566), 2: _spray(68000560), 3: _spray(68000561), 4: _spray(68000569)}},
    ("card_flip_single", 5): {"ranks": {1: _spray(68000728), 2: _spray(68000722), 3: _spray(68000723), 4: _spray(68000727), 5: _spray(68000726)}},
    ("card_flip_single", 6): {"ranks": {1: _spray(68000497), 2: _spray(68000080), 3: _spray(68000061), 4: _spray(68000079), 5: _spray(68000081), 6: _spray(68000062)}},
    ("card_flip_single", 7): {"ranks": {1: _spray(68000595), 2: _spray(68000594), 3: _spray(68000597), 4: _spray(68000598), 5: _spray(68000599), 6: _spray(68000307), 7: _spray(68000277)}},
    ("card_flip_single", 8): {"ranks": {1: _spray(68000715), 2: _spray(68000716), 3: _spray(68000717), 4: _spray(68000710), 5: _spray(68000718), 6: _spray(68000712), 7: _spray(68000713), 8: _spray(68000464)}},
    ("card_flip_multi1", 2): {"ranks": {1: _spray(68000595), 2: _spray(68000594)}, "decoys": [_spray(68000597), _spray(68000598), _spray(68000599)]},
    ("card_flip_multi1", 3): {"ranks": {1: _spray(68000307), 2: _spray(68000277), 3: _spray(68000235)}, "decoys": [_spray(68000284), _spray(68000309), _spray(68000296)]},
    ("card_flip_multi1", 4): {"ranks": {1: _spray(68000715), 2: _spray(68000716), 3: _spray(68000717), 4: _spray(68000710)}, "decoys": [_spray(68000718), _spray(68000712), _spray(68000713)]},
    ("card_flip_multi1", 5): {"ranks": {1: _spray(68000464), 2: _spray(68000465), 3: _spray(68000462), 4: _spray(68000467), 5: _spray(68000463)}, "decoys": [_spray(68000466), _spray(68000457), _spray(68000455)]},
    ("card_flip_multi1", 6): {"ranks": {1: _spray(68000060), 2: _spray(68000059), 3: _spray(68000058), 4: _spray(68000057), 5: _spray(68000056), 6: _spray(68000055)}, "decoys": [_spray(68000054)]},
    ("card_flip_multi1", 7): {"ranks": {1: _spray(68000497), 2: _spray(68000080), 3: _spray(68000061), 4: _spray(68000079), 5: _spray(68000081), 6: _spray(68000062), 7: _spray(68000004)}, "decoys": [_spray(68000098), _spray(68000076)]},
    ("card_flip_multi1", 8): {"ranks": {1: _spray(68000566), 2: _spray(68000560), 3: _spray(68000561), 4: _spray(68000569), 5: _spray(68000728), 6: _spray(68000722), 7: _spray(68000723), 8: _spray(68000727)}, "decoys": [_spray(68000726), _spray(68000007)]},
    ("card_flip_multi2", 2): {"pool": [_spray(68000095), _spray(68000096), _spray(68000097)]},
    ("card_flip_multi2", 3): {"pool": [_spray(68000450), _spray(68000452), _spray(68000453), _spray(68000456)]},
    ("card_flip_multi2", 4): {"ranks": {1: _spray(68000486), 2: _spray(68000483)}, "decoys": [_pin(52002016), _pin(52002025), _pin(52002026)]},
    ("card_flip_multi2", 5): {"ranks": {1: _spray(68000664), 2: _spray(68000641), 3: _spray(68000572)}, "decoys": [_spray(68000675), _spray(68000671)]},
    ("card_flip_multi2", 6): {"ranks": {1: _spray(68000357), 2: _spray(68000355), 3: _spray(68000351)}, "decoys": [_spray(68000352)]},
    ("card_flip_multi2", 7): {"ranks": {1: _spray(68000486), 2: _spray(68000483), 3: _spray(68000664)}, "decoys": [_spray(68000641), _spray(68000572), _spray(68000675)]},
    ("card_flip_multi2", 8): {"ranks": {1: _spray(68000357), 2: _spray(68000355), 3: _spray(68000351)}, "decoys": [_spray(68000352), _spray(68000671), _pin(52002016)]},
    ("scratch1", 2): {"pool": [_pin(52002372), _pin(52002374), _pin(52002375)]},
    ("scratch1", 3): {"pool": [_pin(52000064), _pin(52000145), _pin(52000005)]},
    ("scratch1", 4): {"ranks": {1: _pin(52002867), 2: _pin(52002838)}, "decoys": [_pin(52002839), _pin(52002865), _pin(52002829)]},
    ("scratch1", 5): {"ranks": {1: _pin(52002407), 2: _pin(52003172), 3: _pin(52002329)}, "decoys": [_pin(52002325), _pin(52002328)]},
    ("scratch1", 6): {"ranks": {1: _pin(52001307), 2: _pin(52000368), 3: _pin(52002639)}, "decoys": [_pin(52000608)]},
    ("scratch1", 7): {"ranks": {1: _pin(52002867), 2: _pin(52002838), 3: _pin(52002407)}, "decoys": [_pin(52003172), _pin(52002329), _pin(52002325)]},
    ("scratch1", 8): {"ranks": {1: _pin(52001307), 2: _pin(52000368), 3: _pin(52002639)}, "decoys": [_pin(52000608), _pin(52002328), _pin(52002839)]},
    ("bingo1", 2): {"pool": [_spray(68000095), _spray(68000096), _spray(68000097), _spray(68000450), _spray(68000452)]},
    ("bingo1", 3): {"pool": [_spray(68000453), _spray(68000456), _spray(68000486), _spray(68000483), _spray(68000664)]},
    ("bingo1", 4): {"pool": [_spray(68000641), _spray(68000572), _spray(68000675), _spray(68000671), _spray(68000357), _spray(68000355)]},
    ("bingo1", 5): {"pool": [_spray(68000351), _spray(68000352), _spray(68000566), _spray(68000560), _spray(68000561), _spray(68000569)]},
    ("bingo1", 6): {"pool": [_spray(68000728), _spray(68000722), _spray(68000723), _spray(68000727), _spray(68000726), _spray(68000497)]},
    ("bingo1", 7): {"pool": [_spray(68000080), _spray(68000061), _spray(68000079), _spray(68000081), _spray(68000062), _spray(68000004)]},
    ("bingo1", 8): {"pool": [_spray(68000664), _spray(68000671), _spray(68000666), _spray(68000683), _spray(68000694)]},
}
