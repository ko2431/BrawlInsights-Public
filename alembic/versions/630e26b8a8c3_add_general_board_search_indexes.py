"""add general board search indexes

Revision ID: 630e26b8a8c3
Revises: c7d8e9f0a1b2
Create Date: 2026-09-28 10:25:53.721832

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '630e26b8a8c3'
down_revision: Union[str, Sequence[str], None] = 'c7d8e9f0a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 本番の posts / users をロックしないよう CONCURRENTLY で作成する
    with op.get_context().autocommit_block():
        # 式は app/core/text_search.py の search_normalize_sql と一致させること
        op.create_index('idx_posts_general_comment_search_bigm', 'posts', [sa.literal_column("(translate(normalize(comment, NFKC), 'ABCDEFGHIJKLMNOPQRSTUVWXYZァアィイゥウェエォオカガキギクグケゲコゴサザシジスズセゼソゾタダチヂッツヅテデトドナニヌネノハバパヒビピフブプヘベペホボポマミムメモャヤュユョヨラリルレロヮワヰヱヲンヴヵヶ', 'abcdefghijklmnopqrstuvwxyzぁあぃいぅうぇえぉおかがきぎくぐけげこごさざしじすずせぜそぞただちぢっつづてでとどなにぬねのはばぱひびぴふぶぷへべぺほぼぽまみむめもゃやゅゆょよらりるれろゎわゐゑをんゔゕゖ')) gin_bigm_ops")], unique=False, postgresql_using='gin', postgresql_where=sa.text("type = 'general'"), postgresql_concurrently=True)
        op.create_index('idx_users_name_search_norm', 'users', [sa.literal_column("translate(normalize(name, NFKC), 'ABCDEFGHIJKLMNOPQRSTUVWXYZァアィイゥウェエォオカガキギクグケゲコゴサザシジスズセゼソゾタダチヂッツヅテデトドナニヌネノハバパヒビピフブプヘベペホボポマミムメモャヤュユョヨラリルレロヮワヰヱヲンヴヵヶ', 'abcdefghijklmnopqrstuvwxyzぁあぃいぅうぇえぉおかがきぎくぐけげこごさざしじすずせぜそぞただちぢっつづてでとどなにぬねのはばぱひびぴふぶぷへべぺほぼぽまみむめもゃやゅゆょよらりるれろゎわゐゑをんゔゕゖ')")], unique=False, postgresql_concurrently=True)


def downgrade() -> None:
    """Downgrade schema."""
    with op.get_context().autocommit_block():
        op.drop_index('idx_users_name_search_norm', table_name='users', postgresql_concurrently=True)
        op.drop_index('idx_posts_general_comment_search_bigm', table_name='posts', postgresql_concurrently=True)
