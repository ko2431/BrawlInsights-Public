class Missing:
    """引数が指定されていないことを表す特別な型"""
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Missing, cls).__new__(cls)
        return cls._instance
    
    def __repr__(self):
        return "<Missing>"
    
    def __bool__(self):
        """Falseと評価される"""
        return False

# シングルトンインスタンスをモジュールレベルで作成
MISSING = Missing()

# from app.models.missing import MISSING で使用する
# MISSING はbool評価ではFalseを返す
# if name is MISSING: のようにしてMISSINGかどうかを確認する(isを使用)