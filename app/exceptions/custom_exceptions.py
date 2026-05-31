class BrawlStarsAPIError(Exception):
    """基本のBrawl Stars APIエラー。
    """
    def __init__(self, message: str = "Brawl Stars APIに接続できませんでした。"):
        self.message: str = message
        super().__init__(self.message)
        
class DataBaseError(Exception):
    """基本のデータベースエラー。
    """
    def __init__(self, message: str = "データベースの更新に失敗しました。"):
        self.message: str = message
        super().__init__(self.message)