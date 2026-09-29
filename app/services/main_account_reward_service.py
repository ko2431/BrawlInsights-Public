"""メインアカウント(ブロスタタグ)単位の報酬受け取り上限を管理する。

同じ人が複数のユーザーアカウントを作成しても、トークン等の獲得量が増えないようにするため、
デイリー報酬・1回限りの報酬の上限を、同じメインアカウントを持つユーザー間で共有する。

呼び出し側は、ユーザー単位の付与処理と同じトランザクション内で reserve_main_account_reward を呼ぶこと。
付与に失敗した場合はトランザクションごとロールバックされ、枠だけが消費されることはない。
"""
import datetime
from collections.abc import Awaitable, Callable

import asyncpg

from app.core.cache import delete_cache
from app.core.logger import logger
from app.exceptions.custom_exceptions import DataBaseError

# [この部分は公開用リポジトリでは非公開にされています]
