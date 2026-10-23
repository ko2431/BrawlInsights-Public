from app.services.mention_rules import has_mention_mark, pick_mentions


def test_pick_mentions():
    """本文の「@名前」からメンションを読み取る（候補から入れても手で打っても同じ）。"""
    c = [{"id": 1, "name": "テスト"}, {"id": 2, "name": "テスト2"}, {"id": 3, "name": "Koji"}, {"id": 4, "name": "自分"}]
    ids = lambda body, me=4: [m["id"] for m in pick_mentions(body, c, me)]
    assert ids("@テスト よろしく") == [1]
    assert ids("＠テストさん、チェックインお願いします") == [1]  # 全角の「＠」・名前のすぐ後に文字が続いても
    assert ids("@テスト2 と @テスト") == [2, 1]  # 同じ位置では長い名前。本文の順
    assert ids("@koji @KOJI") == [3]  # 英字の大文字・小文字は区別しない。同じ人は1回
    assert ids("mail@koji.example") == []  # 直前が英数字の「@」は数えない
    assert ids("@自分 @テスト") == [1]  # 自分は除く
    assert ids("テスト さん") == []
    many = [{"id": i, "name": f"u{i}"} for i in range(10)]
    assert len(pick_mentions(" ".join(f"@u{i}" for i in range(10)), many, None)) == 5


def test_has_mention_mark():
    assert has_mention_mark("@テスト")
    assert has_mention_mark("よろしく＠テスト")
    assert not has_mention_mark("よろしく")
    assert not has_mention_mark("")
    assert not has_mention_mark(None)
