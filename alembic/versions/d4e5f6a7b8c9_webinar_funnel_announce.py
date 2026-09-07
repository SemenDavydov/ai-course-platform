"""add webinar funnel announce flags

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-07

"""
from alembic import op
import sqlalchemy as sa

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "webinar_subscribers",
        sa.Column(
            "funnel_announce_sent",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "webinar_subscribers",
        sa.Column(
            "video_clicked",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "webinar_subscribers",
        sa.Column("video_click_slug", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("webinar_subscribers", "video_click_slug")
    op.drop_column("webinar_subscribers", "video_clicked")
    op.drop_column("webinar_subscribers", "funnel_announce_sent")
