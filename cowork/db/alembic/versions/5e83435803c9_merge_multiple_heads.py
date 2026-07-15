"""merge multiple heads

Revision ID: 5e83435803c9
Revises: c1d9e2a4b6f0, d5f3a8c1e6b2
Create Date: 2026-07-08 18:58:10.112586

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5e83435803c9'
down_revision: Union[str, Sequence[str], None] = ('c1d9e2a4b6f0', 'd5f3a8c1e6b2')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
