"""プレイヤーアイコン認証(アカウント所有者確認)の状態管理。

認証の指示内容・成功状態は、認証を行ったブラウザのセッションに紐づけてRedisに保存する。
タグ単位で共有すると、他人が同じタグで認証した直後の成功状態を流用できてしまうため。
"""
import secrets

from fastapi import Request

from app.core.cache import delete_cache, get_cache, set_cache

# [この部分は公開用リポジトリでは非公開にされています]
