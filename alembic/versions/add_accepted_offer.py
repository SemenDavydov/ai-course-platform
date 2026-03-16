"""add accepted_offer column

Revision ID: add_accepted_offer
Revises: 2431a051cec7
Create Date: 2026-03-16 15:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'add_accepted_offer'
down_revision = '2431a051cec7'
branch_labels = None
depends_on = None

def upgrade():
    # Добавляем колонку accepted_offer в таблицу users
    op.add_column('users', sa.Column('accepted_offer', sa.Boolean(), nullable=False, server_default='false'))

def downgrade():
    # Удаляем колонку при откате
    op.drop_column('users', 'accepted_offer')