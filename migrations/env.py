from alembic import context

from app.core.database import Base, get_engine
from app.models import (  # noqa: F401
    NGO,
    AllocationStatus,
    Certification,
    NationalResource,
    NGOCertification,
    ResourceAllocation,
    ResourceAllocationItem,
    User,
)

target_metadata = Base.metadata
engine = get_engine()

if context.is_offline_mode():
    context.configure(
        url=str(engine.url),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
