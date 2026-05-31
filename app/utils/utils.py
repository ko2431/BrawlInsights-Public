import ipaddress
import re
import os
import datetime
from typing import Union, Optional
from fastapi import Request

# [この部分は公開用リポジトリでは非公開にされています]

def calc_mastery_rank(score: int | float) -> int:
    """マスタリーのスコアからランクを計算する。

    Args:
        score (int | float): マスタリースコア

    Returns:
        int: ランク
    """
    if score < 300:
        return 0
    elif 300 <= score < 800:
        return 1
    elif 800 <= score < 1500:
        return 2
    elif 1500 <= score < 2600:
        return 3
    elif 2600 <= score < 4000:
        return 4
    elif 4000 <= score < 5800:
        return 5
    elif 5800 <= score < 10300:
        return 6
    elif 10300 <= score < 16800:
        return 7
    elif 16800 <= score < 24800:
        return 8
    elif score >= 24800:
        return 9

# 改名履歴・クラブ履歴の辞書のアップデート用
def update_logdict(logdict: dict[str, str | None], current_value: str | None) -> dict[str, str | None]:
    """
    改名履歴・クラブ履歴をアップデートします。
    
    Args:
        name_history: 日付をキー、文字列またはNoneを値とする辞書
        current_name: 現在の値、またはNone
    
    Returns:
        更新された履歴辞書
    """
    # 履歴が空の場合は、今日の日付で現在の名前を追加
    if not logdict:
        today_str = format_utc_date(datetime.datetime.now(datetime.timezone.utc).date())
        logdict[today_str] = current_value
        return logdict
    
    # キーを日付でソートして最新の履歴を取得
    sorted_dates = sorted(logdict.keys())
    latest_date = sorted_dates[-1]
    latest_name = logdict[latest_date]
    
    if latest_date.startswith("1") and ((latest_name == current_value) or (latest_name is None and current_value is None)):
        # もし最新の履歴が1で始まる(v8からの名前しかない)で、かつ最新の履歴と今の値が同じ時は、最新の履歴を消して今の値を追加する
        logdict.pop(latest_date)
        logdict[format_utc_date()] = current_value
    
    # 最新の履歴と現在の名前を比較
    if latest_name != current_value:
        # 名前が変わっていれば、今日の日付で新しい履歴を追加
        logdict[format_utc_date()] = current_value
    
    return logdict

# 日時関連
def parse_api_utc_datetime(date_str: str) -> datetime.datetime:
    """
    Brawl Stars APIのUTC日時の文字列 ('2025-01-01 23:59:00Z' 形式) を datetime オブジェクトに変換します。形式が不正な場合はValueErrorが発生します。
    
    Args:
        date_str: UTCタイムゾーンの日時を表す文字列(Brawl Stars APIの形式)
        
    Returns:
        UTCタイムゾーン情報付きのdatetimeオブジェクト
    """
    try:
        # フォーマットに従ってパース
        # %Y: 4桁の年, %m: 2桁の月, %d: 2桁の日
        # %H: 24時間表記の時, %M: 分, %S: 秒, %f: マイクロ秒
        dt = datetime.datetime.strptime(date_str, "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=datetime.timezone.utc)
        # すでにUTCとして解釈されている
        return dt
    except ValueError as e:
        raise ValueError(f"日時文字列のフォーマットが不正です: {e}")

def parse_utc_datetime(date_str: str) -> datetime.datetime:
    """
    UTC日時の文字列 ('2025-01-01 23:59:00Z' 形式) を datetime オブジェクトに変換します
    
    Args:
        date_str: UTCタイムゾーンの日時を表す文字列
        
    Returns:
        UTCタイムゾーン情報付きのdatetimeオブジェクト
    """
    # 'Z'がついている場合はUTCを表すため、それを除去
    if date_str.endswith('Z'):
        date_str = date_str[:-1]
        
    # 文字列をdatetimeに変換し、UTCタイムゾーンを設定
    dt = datetime.datetime.fromisoformat(date_str).replace(tzinfo=datetime.timezone.utc)
    return dt

def format_utc_datetime(dt: datetime.datetime = None) -> str:
    """
    datetime オブジェクトを UTC日時の文字列 ('2025-01-01 23:59:00Z' 形式) に変換します。引数が渡されなかった場合、現在時刻を使用。
    
    Args:
        dt: datetime オブジェクト（タイムゾーン情報がなければUTCとして扱う。タイムゾーン情報があればUTCに自動変換）
        
    Returns:
        UTC日時を表す文字列
    """
    # 引数が渡されなかった場合、現在時刻を使用
    if not dt:
        dt = datetime.datetime.now(datetime.timezone.utc)
    # タイムゾーン情報がない場合は、UTCとして扱う
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    # 違うタイムゾーンの場合は、UTCに変換
    elif dt.tzinfo != datetime.timezone.utc:
        dt = dt.astimezone(datetime.timezone.utc)
        
    # フォーマット
    return dt.strftime('%Y-%m-%d %H:%M:%SZ')

def parse_utc_date(date_str: str) -> datetime.date:
    """
    UTC日付の文字列 ('2025-01-01' 形式) を date オブジェクトに変換します
    
    Args:
        date_str: 日付を表す文字列
        
    Returns:
        dateオブジェクト
    """
    return datetime.date.fromisoformat(date_str)

def format_utc_date(date_obj: datetime.date = None) -> str:
    """
    date オブジェクトを UTC日付の文字列 ('2025-01-01' 形式) に変換します。引数が渡されなかった場合、現在UTC日付を使用。
    
    Args:
        date_obj: date オブジェクト
        
    Returns:
        日付を表す文字列
    """
    # 引数が渡されなかった場合、現在時刻を使用
    if not date_obj:
        date_obj = datetime.datetime.now(datetime.timezone.utc).date()
    return date_obj.isoformat()

def is_expired(expiration_datetime: Optional[datetime.datetime]) -> bool:
    """
    与えられた期限が現在時刻（UTC）を過ぎているかどうかを判定する。
    
    Args:
        expiration_datetime: 確認する期限（datetime型、タイムゾーン情報付きUTC）。タイムゾーン情報がない場合UTCとみなす。
        
    Returns:
        bool: 期限が過ぎていればTrue、まだであればFalse。期限がNoneの場合はFlase。
    """
    # 引数のチェック
    if expiration_datetime is None:
        return False  # 期限がNoneの場合は期限切れではない
    
    # タイムゾーン情報がない場合はUTCと見なす
    if expiration_datetime.tzinfo is None:
        expiration_datetime = expiration_datetime.replace(tzinfo=datetime.timezone.utc)
    
    # 現在のUTC時刻を取得
    current_utc = datetime.datetime.now(datetime.timezone.utc)
    
    # 期限と現在時刻を比較
    return current_utc > expiration_datetime

def get_first_thursdays(limit: int = 1) -> list[datetime.date]:
    """トロフィーシーズン切り替え日である、月の第1木曜日を取得していく関数。

    Args:
        limit (int, optional): 取得数。デフォルトは1。

    Returns:
        list[datetime.date]: 第1木曜日のリスト。最も最近(過去)のものが最初になっている。
    """
    today = datetime.datetime.now(datetime.timezone.utc).date()
    results = []
    # ヘルパー関数: 指定された年・月の第1木曜日を取得
    def first_thursday(year: int, month: int) -> datetime.date:
        d = datetime.date(year, month, 1)
        while d.weekday() != 3: # 0=Mon, ..., 3=Thu
            d += datetime.timedelta(days=1)
        return d
    
    # 現在の月から1か月ずつ遡っていく
    year, month = today.year, today.month
    
    while len(results) < limit:
        ft = first_thursday(year, month)
        if ft <= today:
            results.append(ft)
        # 月を1つ前へ
        if month == 1:
            month = 12
            year -= 1
        else:
            month -= 1
            
    return results

def _get_season_start_dt(year: int, month: int) -> datetime.datetime:
    """
    指定された年月のガチバトルシーズン開始日時（第3木曜日UTC9時）を計算します。

    Args:
        year (int): 年
        month (int): 月

    Returns:
        datetime.datetime: シーズン開始日時 (UTC)
    """
    # 月の初日を取得します
    first_day_of_month = datetime.date(year, month, 1)
    # 月の初日の曜日を取得します (月曜日=0, ..., 木曜日=3, ..., 日曜日=6)
    weekday_of_first_day = first_day_of_month.weekday()
    
    # 最初の木曜日が月の何日かを計算します
    # (3 - weekday_of_first_day + 7) % 7 で、初日から見て次の木曜日まで何日あるかがわかります
    days_to_first_thursday = (3 - weekday_of_first_day + 7) % 7
    first_thursday = 1 + days_to_first_thursday
    
    # 第3木曜日は、最初の木曜日の14日後です
    third_thursday = first_thursday + 14
    
    # 第3木曜日のUTC午前9時のdatetimeオブジェクトを作成して返します
    return datetime.datetime(year, month, third_thursday, 9, 0, 0, tzinfo=datetime.timezone.utc)

def calc_ranked_season(dt: datetime.datetime | None = None) -> int:
    """ガチバトルのシーズン番号を、日時をもとに計算する。

    Args:
        dt (datetime.datetime | None): 日時。未指定の場合は現在UTC日時を使用。

    Returns:
        int: シーズン番号
    """
    if not dt:
        dt = datetime.datetime.now(datetime.timezone.utc)
        
    # dtにタイムゾーンがない場合はutcを付与、utc以外のタイムゾーンが付与されている場合はutcに変換
    if dt.tzinfo is None:
        # タイムゾーン情報がないnaiveなdatetimeの場合は、UTCとして扱います
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    else:
        # タイムゾーン情報があるawareなdatetimeの場合は、UTCに変換します
        dt = dt.astimezone(datetime.timezone.utc)
    
    # シーズン番号を求める。
    # 基準となるシーズン情報 (2025年6月 -> シーズン34)
    base_year = 2025
    base_month = 6
    base_season_number = 34

    # 判定対象の日時が含まれる月のシーズン開始日時を取得します
    this_month_season_start = _get_season_start_dt(dt.year, dt.month)
    
    # シーズンが属する年月を特定します
    season_year = dt.year
    season_month = dt.month
    
    # もし判定対象の日時が、その月のシーズン開始日時よりも前であれば、
    # その日時は前月のシーズンに属することになります。
    if dt < this_month_season_start:
        season_month -= 1
        # 月が0になった場合は、前年の12月を意味します
        if season_month == 0:
            season_month = 12
            season_year -= 1

    # 基準となる年月からの月差を計算します
    # (年の差 × 12ヶ月) + 月の差
    month_diff = (season_year - base_year) * 12 + (season_month - base_month)
    
    # 基準となるシーズン番号に月差を加算して、現在のシーズン番号を算出します
    current_season_number = base_season_number + month_diff
        
    return current_season_number

def get_ranked_season_period(season_number: int) -> tuple[datetime.date, datetime.date]:
    """
    ガチバトルシーズン番号に対応する開始日と終了日を取得します。

    Args:
        season_number (int): シーズン番号。

    Returns:
        tuple[datetime.date, datetime.date]: (開始日, 終了日) のタプル。

    Raises:
        ValueError: サポートされていないシーズン番号の場合。
    """
    if season_number == 31:
        # シーズン31は期間が特殊なため、ハードコードで対応します
        return (datetime.date(2025, 3, 3), datetime.date(2025, 4, 16))

    if season_number < 31:
        raise ValueError("シーズン31より前のシーズンはサポートされていません。")

    # 基準となるシーズン情報 (2025年6月 -> シーズン34) を使って計算します
    base_year = 2025
    base_month = 6
    base_season_number = 34

    # 基準からの月差を計算します
    month_diff = season_number - base_season_number

    # 基準月を0-indexed (0～11) に変換して月差を加え、12で割った商を年のオフセット、
    # 剰余を月のオフセットとして扱います。
    # 例: 基準が6月(5)で差が+7ヶ月 -> 5 + 7 = 12 -> 12//12=1(年), 12%12=0(月) -> 1年後の1月
    total_months_offset = (base_month - 1) + month_diff
    year_offset = total_months_offset // 12
    
    target_year = base_year + year_offset
    target_month = total_months_offset % 12 + 1

    # シーズン開始日時と、次のシーズンの開始日時を取得します
    season_start_dt = _get_season_start_dt(target_year, target_month)

    # 次の月の年月を計算します
    next_season_year = target_year
    next_season_month = target_month + 1
    if next_season_month > 12:
        next_season_month = 1
        next_season_year += 1
    
    next_season_start_dt = _get_season_start_dt(next_season_year, next_season_month)

    start_date = season_start_dt.date()
    # 終了日は、次のシーズンの開始日の1日前です
    end_date = next_season_start_dt.date() - datetime.timedelta(days=1)

    return (start_date, end_date)

def get_ranked_seasons_for_filter() -> list[dict]:
    """
    HTMLのプルダウンメニュー用に、シーズン31から現在のシーズンまでのリストを生成します。
    """
    seasons = []
    # 現在のシーズン番号を取得します（この関数はUTC9時を考慮済み）
    current_season_num = calc_ranked_season()

    # 現在のシーズンからシーズン31まで、降順でループ処理します
    for season_num in range(current_season_num, 30, -1):
        start_date, end_date = get_ranked_season_period(season_num)

        # 表示用文字列（日本語）を生成します
        if season_num == current_season_num:
            display_ja = f"シーズン{season_num} (現在のシーズン)"
        elif start_date.year == end_date.year:
            display_ja = f"シーズン{season_num} ({start_date.year}年{start_date.month}-{end_date.month}月)"
        else:
            # 年をまたぐ場合
            display_ja = f"シーズン{season_num} ({start_date.year}年{start_date.month}月 - {end_date.year}年{end_date.month}月)"

        # 表示用文字列（英語）を生成します
        if season_num == current_season_num:
            display_en = f"Season {season_num} (Current Season)"
        elif start_date.year == end_date.year:
            display_en = f"Season {season_num} ({start_date.strftime('%b')} - {end_date.strftime('%b')} {end_date.year})"
        else:
            # 年をまたぐ場合
            display_en = f"Season {season_num} ({start_date.strftime('%b %Y')} - {end_date.strftime('%b %Y')})"


        seasons.append({
            "number": season_num,
            "display_ja": display_ja,
            "display_en": display_en,
            # JavaScriptで扱いやすいように、日付を 'YYYY-MM-DD' 形式の文字列で渡します
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        })

    return seasons

def format_display_datetime(dt: datetime.datetime, lang: str = "ja") -> str:
    """
    管理者ページでの表示用に日時をフォーマットします。
    - 今日、昨日の場合は特別な文字列を返す
    - 今年の場合は年を省略する
    - 昨年以前の場合は年月日を含む
    """
    if not isinstance(dt, datetime.datetime):
        return "" # datetimeオブジェクトでない場合は空文字を返す

    # 日本のタイムゾーン（JST, UTC+9）
    jst = datetime.timezone(datetime.timedelta(hours=9))
    now_jst = datetime.datetime.now(jst)
    dt_jst = dt.astimezone(jst)

    # 日付部分の比較
    today = now_jst.date()
    yesterday = today - datetime.timedelta(days=1)
    
    date_part = dt_jst.date()
    time_part = dt_jst.strftime('%H:%M:%S')

    if date_part == today:
        if lang == "ja":
            return f"今日 {time_part}"
        else:
            return f"Today {time_part}"
            
    if date_part == yesterday:
        if lang == "ja":
            return f"昨日 {time_part}"
        else:
            return f"Yesterday {time_part}"

    if date_part.year == today.year:
        # 今年の場合: MM-DD HH:MM:SS
        return dt_jst.strftime('%m-%d %H:%M:%S')
    else:
        # 昨年以前の場合: YYYY-MM-DD HH:MM:SS
        return dt_jst.strftime('%Y-%m-%d %H:%M:%S')

# プレイヤーアイコン関連
def get_icon_path(icon_id: int | None) -> str:
    if icon_id:
        return f"static/images/player_icon/{icon_id}.webp"
    return "static/images/player_icon/0.png"

# 自動追跡関連
def calc_auto_activate_hours(b: int, highest_trophies: int, ranked_highest_score: int, ranked_current_score: int, solo_pl_rank: int) -> int:
    """自動追跡機能を自動で有効化する時間を計算します。

    Args:
        b (int): 現在利用可能なキャラクター数。
        highest_trophies (int): 最多トロフィー数。
        ranked_highest_score (int): ガチバトル最高スコア。
        ranked_current_score (int): ガチバトル現在スコア。
        solo_pl_rank (int): 旧ソロパワーリーグ最高ランク。

    Returns:
        int: 有効化時間。単位は時間。
    """
    if (highest_trophies >= b * 2000) or (ranked_highest_score >= 15500) or (ranked_current_score >= 12000): return 102
    elif (highest_trophies >= b * 1960) or (ranked_highest_score >= 14600) or (ranked_current_score >= 11800): return 96
    elif (highest_trophies >= b * 1920) or (ranked_highest_score >= 13800) or (ranked_current_score >= 11600): return 90
    elif (highest_trophies >= b * 1880) or (ranked_highest_score >= 13100) or (ranked_current_score >= 11500) or (solo_pl_rank >= 19): return 84
    elif (highest_trophies >= b * 1840) or (ranked_highest_score >= 12500) or (ranked_current_score >= 11400): return 78
    elif (highest_trophies >= b * 1800) or (ranked_highest_score >= 12100) or (ranked_current_score >= 11300): return 72
    elif (highest_trophies >= b * 1760) or (ranked_highest_score >= 11850) or (ranked_current_score >= 11200): return 66
    elif (highest_trophies >= b * 1720) or (ranked_highest_score >= 11650) or (ranked_current_score >= 11100) or (solo_pl_rank >= 18): return 60
    elif (highest_trophies >= b * 1680) or (ranked_highest_score >= 11450) or (ranked_current_score >= 11000): return 54
    elif (highest_trophies >= b * 1640) or (ranked_highest_score >= 11250) or (ranked_current_score >= 10900): return 48
    elif (highest_trophies >= b * 1600) or (ranked_highest_score >= 11050) or (ranked_current_score >= 10700): return 44
    elif (highest_trophies >= b * 1570) or (ranked_highest_score >= 10850) or (ranked_current_score >= 10500) or (solo_pl_rank >= 17): return 40
    elif (highest_trophies >= b * 1540) or (ranked_highest_score >= 10650) or (ranked_current_score >= 10300): return 36
    elif (highest_trophies >= b * 1510) or (ranked_highest_score >= 10450) or (ranked_current_score >= 10100): return 32
    elif (highest_trophies >= b * 1480) or (ranked_highest_score >= 10250) or (ranked_current_score >= 9900): return 28
    elif (highest_trophies >= b * 1450) or (ranked_highest_score >= 10050) or (ranked_current_score >= 9700) or (solo_pl_rank >= 16): return 24
    elif (highest_trophies >= b * 1420) or (ranked_highest_score >= 9850) or (ranked_current_score >= 9500): return 21
    elif (highest_trophies >= b * 1390) or (ranked_highest_score >= 9650) or (ranked_current_score >= 9300): return 18
    elif (highest_trophies >= b * 1360) or (ranked_highest_score >= 9450) or (ranked_current_score >= 9100) or (solo_pl_rank >= 15): return 15
    elif (highest_trophies >= b * 1330) or (ranked_highest_score >= 9250) or (ranked_current_score >= 8900): return 12
    elif (highest_trophies >= b * 1300) or (ranked_highest_score >= 9050) or (ranked_current_score >= 8700): return 9
    elif (highest_trophies >= b * 1270) or (ranked_highest_score >= 8850) or (ranked_current_score >= 8500) or (solo_pl_rank >= 14): return 7
    elif (highest_trophies >= b * 1240) or (ranked_highest_score >= 8650) or (ranked_current_score >= 8300): return 5
    elif (highest_trophies >= b * 1220) or (ranked_highest_score >= 8450) or (ranked_current_score >= 8100): return 3
    elif (highest_trophies >= b * 1200) or (ranked_highest_score >= 8250) or (ranked_current_score >= 7900) or (solo_pl_rank >= 13): return 1
    else: return 0

# IPアドレス関連
def get_normalized_ip(ip_str: str | None) -> str | None:
    """
    IPアドレス文字列を受け取り、正規化された文字列を返す。
    無効なIPアドレスの場合はNoneを返す。
    """
    if not ip_str:
        return None
    try:
        # ipaddressオブジェクトに変換し、str()で正規化された文字列表現を得る
        ip_obj = ipaddress.ip_address(ip_str)
        # .compressed を使っても同様の結果が得られます
        return str(ip_obj)
    except ValueError:
        # 無効なIPアドレス形式の場合はNoneを返す
        return None

def get_remote_ip(request: Request) -> str:
    """
    リクエストから信頼できるクライアントIPアドレスを取得します。
    Cloudflareのヘッダー(CF-Connecting-IP)を最優先し、次に X-Forwarded-For の先頭、
    最後に直接の接続元IPを確認します。
    取得したIPは正規化され、不正な形式（攻撃コード等）の場合はフォールバックします。
    """
    # 1. Cloudflareヘッダー
    cf_ip = request.headers.get("CF-Connecting-IP")
    normalized_cf = get_normalized_ip(cf_ip)
    if normalized_cf:
        return normalized_cf

    # 2. X-Forwarded-For (プロキシ経由)
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        # 最初の要素を取得
        first_xff = xff.split(",")[0].strip()
        normalized_xff = get_normalized_ip(first_xff)
        if normalized_xff:
            return normalized_xff

    # 3. フォールバック: 直接の接続元
    return get_normalized_ip(request.client.host) or "0.0.0.0"

def format_last_played_time(dt: datetime.datetime, lang: str = "ja") -> str:
    """最終プレイ時間を指定された形式にフォーマットする。
    1日以上の場合は、d日h時間前 (0時間でも略さない)
    1時間以上の場合は、h時間m分前 (0分でも略さない)
    1分前以上の場合は、m分前
    それ未満の場合は、1分未満前

    Args:
        dt (datetime.datetime): 最終プレイ日時(UTC)
        lang (str, optional): 言語("ja" or "en")。デフォルトは"ja"。

    Returns:
        str: フォーマットされた文字列。
    """
    if not dt:
        return ""
    
    # タイムゾーンの調整
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    
    now = datetime.datetime.now(datetime.timezone.utc)
    diff = now - dt
    
    # 負の差分（未来の日時）の場合は「1分未満前」とする
    if diff.total_seconds() < 0:
        if lang == "ja":
            return "1分未満前"
        else:
            return "less than 1 minute ago"
            
    days = diff.days
    hours = diff.seconds // 3600
    minutes = (diff.seconds % 3600) // 60
    
    if days >= 1:
        if lang == "ja":
            return f"{days}日{hours}時間前"
        else:
            day_str = "day" if days == 1 else "days"
            hour_str = "hour" if hours == 1 else "hours"
            return f"{days} {day_str} {hours} {hour_str} ago"
    elif hours >= 1:
        if lang == "ja":
            return f"{hours}時間{minutes}分前"
        else:
            hour_str = "hour" if hours == 1 else "hours"
            min_str = "minute" if minutes == 1 else "minutes"
            return f"{hours} {hour_str} {minutes} {min_str} ago"
    elif minutes >= 1:
        if lang == "ja":
            return f"{minutes}分前"
        else:
            min_str = "minute" if minutes == 1 else "minutes"
            return f"{minutes} {min_str} ago"
    else:
        if lang == "ja":
            return "1分未満前"
        else:
            return "less than 1 minute ago"
