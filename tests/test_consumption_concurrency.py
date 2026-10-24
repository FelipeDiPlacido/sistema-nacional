"""Different allocations may close against the same inventory row concurrently."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, local

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.models import NGO, Certification, NationalResource, ResourceAllocation, User
from app.models.national import ResourceAllocationItem
from app.schemas import (
    AllocationClose,
    AllocationCreate,
    AllocationItemConsumption,
    AllocationItemCreate,
)
from app.services.allocation_service import cancel_allocation, close_allocation


@pytest.mark.postgresql
@pytest.mark.parametrize("cancel_second", [False, True])
def test_concurrent_consumption_on_different_allocations_preserves_both_updates(db, cancel_second):
    if db.bind.dialect.name != "postgresql":
        pytest.skip("Row locking requires PostgreSQL")
    ngo = NGO(
        name="Concurrent consumption",
        owner=User(email="consumption@example.org", password_hash="unused"),
    )
    db.add(ngo)
    db.flush()
    resource = NationalResource(
        ngo_id=ngo.id,
        code="SHARED_CONSUMPTION",
        name="Shared water",
        category="SUPPLIES",
        unit="litros",
        total_quantity=10,
        committed_quantity=6,
        required_certification=db.scalar(select(Certification).limit(1)),
    )
    allocations = [
        ResourceAllocation(
            request_key=uuid.uuid4(),
            project_reference=f"CONSUMPTION-{i}",
            ngo_id=ngo.id,
            risk_level="LOW",
            items=[ResourceAllocationItem(resource=resource, quantity=3)],
        )
        for i in range(2)
    ]
    db.add_all(allocations)
    db.commit()
    ids = [allocation.id for allocation in allocations]
    barrier = Barrier(2)
    first_closed = Event()
    worker = local()

    def before_lock(conn, cursor, statement, parameters, context, executemany):
        if "FROM national_resources" in statement and "FOR UPDATE" in statement:
            # Both requests have already read the resource through the item relationship.
            barrier.wait(timeout=10)
            if worker.index == 1:
                assert first_closed.wait(timeout=10)

    event.listen(db.bind, "before_cursor_execute", before_lock)
    try:

        def close(index):
            worker.index = index
            with Session(db.bind, expire_on_commit=False) as session:
                if index == 1 and cancel_second:
                    return cancel_allocation(
                        session,
                        AllocationCreate(
                            project_reference=allocations[index].project_reference,
                            ngo_id=ngo.id,
                            risk_level="LOW",
                            items=[AllocationItemCreate(resource_code=resource.code, quantity=3)],
                        ),
                        allocations[index].request_key,
                    ).status
                result = close_allocation(
                    session,
                    ids[index],
                    AllocationClose(
                        items=[
                            AllocationItemConsumption(
                                resource_code=resource.code, quantity=index + 1
                            )
                        ]
                    ),
                ).status
                first_closed.set()
                return result

        with ThreadPoolExecutor(max_workers=2) as executor:
            assert list(executor.map(close, range(2))) == ["CLOSED", "CLOSED"]
    finally:
        event.remove(db.bind, "before_cursor_execute", before_lock)
    db.refresh(resource)
    assert resource.total_quantity == (9 if cancel_second else 7)
    assert resource.committed_quantity == 0
