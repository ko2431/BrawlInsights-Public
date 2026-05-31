import json
import math
from typing import Any

import asyncpg

from app.core.cache import get_redis
from app.core.logger import logger
from app.utils.utils import estimate_play_time

# [この部分は公開用リポジトリでは非公開にされています]
