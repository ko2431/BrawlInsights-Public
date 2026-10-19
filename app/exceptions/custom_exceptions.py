class BrawlStarsAPIError(Exception):
    """基本のBrawl Stars APIエラー。
    """
    def __init__(self, message: str = "Brawl Stars APIに接続できませんでした。"):
        self.message: str = message
        super().__init__(self.message)

class PlayerNotFoundError(BrawlStarsAPIError):
    """存在しないプレイヤータグが指定された (APIが404を返し、DBにも存在しない)。ユーザーの入力ミスによるもの。
    """
    def __init__(self, message: str = "プレイヤーが見つかりませんでした。"):
        super().__init__(message)

class DataBaseError(Exception):
    """基本のデータベースエラー。
    """
    def __init__(self, message: str = "データベースの更新に失敗しました。"):
        self.message: str = message
        super().__init__(self.message)