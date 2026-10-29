"""add announcement posts

お知らせの刷新（検討メモ notes_private/announcement_plan_2026-10-29.md の 3章・10章）。
本体（announcement_posts）・変更の履歴（announcement_revisions）・テンプレートと部品（announcement_templates）を作り、
旧テーブル announcements の全件を写す。旧テーブルはデプロイ中の古いプロセスが読むので、このリビジョンでは消さない。

写し方
- 数字の ID はその番号のまま、それ以外（web1・ios1・G41 など）は数字の ID の最大の次から日時の順に番号を振る。旧 ID はすべて legacy_key に入れる。
- 端末は旧 ID の先頭（web・ios・android・app）から決める。言語はどちらもオン。日本語がないものは英語を写す（今の表示と同じ）。
- タイトル・本文は announcement_html の convert_legacy_* で新しい形にする（1回だけの移行なので、アプリのモジュールをそのまま使う）。
- 写した時点の旧形式の値は、変更の履歴（action = 'migrate'）の snapshot の legacy に残す。
- テンプレートは app/data/announcement_templates_seed.json（今の管理画面の定型文と notes_public の文面を変換したもの）から入れる。

Revision ID: df1682b7cc67
Revises: d4004799fa24
Create Date: 2026-10-29 17:00:00.000000

"""
import json
import os
import re
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'df1682b7cc67'
down_revision: Union[str, Sequence[str], None] = 'd4004799fa24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SEED_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'app', 'data', 'announcement_templates_seed.json')
_PREFIX_PLATFORMS = (
    ('android', (False, False, True)),
    ('web', (True, False, False)),
    ('ios', (False, True, False)),
    ('app', (False, True, True)),
)


def _platforms_from_legacy_id(legacy_id: str) -> tuple[bool, bool, bool]:
    lowered = legacy_id.lower()
    for prefix, flags in _PREFIX_PLATFORMS:
        if lowered.startswith(prefix):
            return flags
    return (True, True, True)


def upgrade() -> None:
    """Upgrade schema."""
    from app.services.announcement_html import convert_legacy_body, convert_legacy_title, first_image_src

    op.execute("SET LOCAL lock_timeout = '5s'")
    op.create_table('announcement_posts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('legacy_key', sa.Text(), nullable=True),
    sa.Column('category', sa.SmallInteger(), nullable=True),
    sa.Column('status', sa.Text(), server_default='draft', nullable=False),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('show_web', sa.Boolean(), server_default='True', nullable=False),
    sa.Column('show_ios', sa.Boolean(), server_default='True', nullable=False),
    sa.Column('show_android', sa.Boolean(), server_default='True', nullable=False),
    sa.Column('show_ja', sa.Boolean(), server_default='True', nullable=False),
    sa.Column('show_en', sa.Boolean(), server_default='True', nullable=False),
    sa.Column('on_home', sa.Boolean(), server_default='True', nullable=False),
    sa.Column('listed', sa.Boolean(), server_default='True', nullable=False),
    sa.Column('title_ja', sa.Text(), server_default='', nullable=False),
    sa.Column('title_en', sa.Text(), server_default='', nullable=False),
    sa.Column('body_ja', sa.Text(), server_default='', nullable=False),
    sa.Column('body_en', sa.Text(), server_default='', nullable=False),
    sa.Column('cover_url', sa.Text(), nullable=True),
    sa.Column('source_type', sa.Text(), nullable=True),
    sa.Column('source_id', sa.Text(), nullable=True),
    sa.Column('source_role', sa.Text(), nullable=True),
    sa.Column('version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('updated_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('deleted_by', sa.Integer(), nullable=True),
    sa.CheckConstraint("status IN ('draft', 'published')", name='ck_announcement_posts_status'),
    sa.CheckConstraint("status = 'draft' OR published_at IS NOT NULL", name='ck_announcement_posts_published_at'),
    sa.CheckConstraint('category IS NULL OR category BETWEEN 1 AND 5', name='ck_announcement_posts_category'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['deleted_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('legacy_key')
    )
    op.create_index('ix_announcement_posts_public', 'announcement_posts', [sa.text('published_at DESC'), sa.text('id DESC')], unique=False, postgresql_where=sa.text("status = 'published' AND deleted_at IS NULL"))
    op.create_index('ix_announcement_posts_source', 'announcement_posts', ['source_type', 'source_id'], unique=False)
    op.create_table('announcement_revisions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('post_id', sa.Integer(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('action', sa.Text(), nullable=False),
    sa.Column('snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('edited_by', sa.Integer(), nullable=True),
    sa.Column('edited_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("action IN ('create', 'update', 'delete', 'restore', 'migrate')", name='ck_announcement_revisions_action'),
    sa.ForeignKeyConstraint(['edited_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['post_id'], ['announcement_posts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_announcement_revisions_post_id', 'announcement_revisions', ['post_id', sa.text('id DESC')], unique=False)
    op.create_table('announcement_templates',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.Text(), server_default='template', nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('category', sa.SmallInteger(), nullable=True),
    sa.Column('title_ja', sa.Text(), server_default='', nullable=False),
    sa.Column('title_en', sa.Text(), server_default='', nullable=False),
    sa.Column('body_ja', sa.Text(), server_default='', nullable=False),
    sa.Column('body_en', sa.Text(), server_default='', nullable=False),
    sa.Column('settings', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False),
    sa.Column('updated_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('template', 'snippet')", name='ck_announcement_templates_kind'),
    sa.CheckConstraint('category IS NULL OR category BETWEEN 1 AND 5', name='ck_announcement_templates_category'),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )

    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT id, datetime, en_title, ja_title, en_text, ja_text, category FROM announcements ORDER BY datetime, id"
    )).mappings().all()
    numeric_ids = [int(r['id']) for r in rows if re.fullmatch(r'[0-9]+', r['id'])]
    next_id = max(numeric_ids, default=0) + 1
    insert_post = sa.text("""
        INSERT INTO announcement_posts (
            id, legacy_key, category, status, published_at, show_web, show_ios, show_android,
            title_ja, title_en, body_ja, body_en, cover_url, created_at, updated_at
        ) VALUES (
            :id, :legacy_key, :category, 'published', :published_at, :show_web, :show_ios, :show_android,
            :title_ja, :title_en, :body_ja, :body_en, :cover_url, :published_at, now()
        )
    """)
    insert_revision = sa.text("""
        INSERT INTO announcement_revisions (post_id, version, action, snapshot, edited_at)
        VALUES (:post_id, 1, 'migrate', CAST(:snapshot AS JSONB), now())
    """)
    for row in rows:
        legacy_id = row['id']
        if re.fullmatch(r'[0-9]+', legacy_id):
            post_id = int(legacy_id)
        else:
            post_id = next_id
            next_id += 1
        show_web, show_ios, show_android = _platforms_from_legacy_id(legacy_id)
        en_title = convert_legacy_title(row['en_title'])
        en_body = convert_legacy_body(row['en_text'])
        ja_title = convert_legacy_title(row['ja_title']) if row['ja_title'] else en_title
        ja_body = convert_legacy_body(row['ja_text']) if row['ja_text'] else en_body
        category = row['category'] if row['category'] in (1, 2, 3, 4, 5) else None
        values = {
            'id': post_id,
            'legacy_key': legacy_id,
            'category': category,
            'published_at': row['datetime'],
            'show_web': show_web,
            'show_ios': show_ios,
            'show_android': show_android,
            'title_ja': ja_title,
            'title_en': en_title,
            'body_ja': ja_body,
            'body_en': en_body,
            'cover_url': first_image_src(ja_body) or first_image_src(en_body),
        }
        bind.execute(insert_post, values)
        snapshot = {
            key: values[key] for key in (
                'category', 'show_web', 'show_ios', 'show_android', 'title_ja', 'title_en', 'body_ja', 'body_en',
            )
        }
        snapshot.update({
            'status': 'published',
            'published_at': row['datetime'].isoformat(),
            'show_ja': True,
            'show_en': True,
            'on_home': True,
            'listed': True,
            'legacy': {
                'id': legacy_id,
                'en_title': row['en_title'],
                'ja_title': row['ja_title'],
                'en_text': row['en_text'],
                'ja_text': row['ja_text'],
                'category': row['category'],
            },
        })
        bind.execute(insert_revision, {'post_id': post_id, 'snapshot': json.dumps(snapshot, ensure_ascii=False)})
    bind.execute(sa.text(
        "SELECT setval(pg_get_serial_sequence('announcement_posts', 'id'), GREATEST(COALESCE(MAX(id), 0), 1), MAX(id) IS NOT NULL) FROM announcement_posts"
    ))

    # 公開版のリポジトリには app/data がないので、そのときはテンプレートを入れない
    if not os.path.exists(SEED_PATH):
        return
    with open(SEED_PATH, encoding='utf-8') as f:
        templates = json.load(f)
    insert_template = sa.text("""
        INSERT INTO announcement_templates (kind, name, category, title_ja, title_en, body_ja, body_en, sort_order)
        VALUES (:kind, :name, :category, :title_ja, :title_en, :body_ja, :body_en, :sort_order)
    """)
    for item in templates:
        bind.execute(insert_template, {key: item[key] for key in (
            'kind', 'name', 'category', 'title_ja', 'title_en', 'body_ja', 'body_en', 'sort_order',
        )})


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('announcement_templates')
    op.drop_index('ix_announcement_revisions_post_id', table_name='announcement_revisions')
    op.drop_table('announcement_revisions')
    op.drop_index('ix_announcement_posts_source', table_name='announcement_posts')
    op.drop_index('ix_announcement_posts_public', table_name='announcement_posts', postgresql_where=sa.text("status = 'published' AND deleted_at IS NULL"))
    op.drop_table('announcement_posts')
