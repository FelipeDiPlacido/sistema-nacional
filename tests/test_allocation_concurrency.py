import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from urllib.parse import urlsplit

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models import (
    NGO,
    Certification,
    NationalResource,
    NGOCertification,
    ResourceAllocation,
    User,
)
from app.models.national import NGOCertificationStatus, NGOStatus, RiskLevel
from app.schemas import AllocationCreate, AllocationItemCreate
from app.services.allocation_service import close_allocation, create_allocation


@pytest.mark.postgresql
@pytest.mark.parametrize("same_request", [False, True])
def test_concurrent_awards_and_closures_preserve_stock(same_request):
    database_url = os.getenv("NATIONAL_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set NATIONAL_TEST_DATABASE_URL to run PostgreSQL locking tests")
    if not urlsplit(database_url).path.lstrip("/").endswith("_test"):
        pytest.fail("PostgreSQL concurrency tests require a dedicated database ending in _test")

    engine = create_engine(database_url, pool_size=5, max_overflow=5)
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as db:
            certification = Certification(
                code="CONCURRENCY_CERT",
                name="Concurrency certification",
                description="Test-only certification",
                is_active=True,
            )
            user = User(
                email="concurrency-test@example.org",
                password_hash="unused-test-hash",
                is_active=True,
            )
            ngo = NGO(name="Concurrency test NGO", status=NGOStatus.ENABLED, owner=user)
            ngo.certifications.append(
                NGOCertification(
                    certification=certification,
                    status=NGOCertificationStatus.VERIFIED,
                    allowed_risk_levels=[RiskLevel.LOW.value],
                )
            )
            db.add(ngo)
            db.flush()
            resource = NationalResource(
                ngo_id=ngo.id,
                code="CONCURRENCY_STOCK",
                name="Concurrency stock",
                category="TEST",
                unit="units",
                total_quantity=10,
                committed_quantity=0,
                required_certification=certification,
                is_active=True,
            )
            db.add(resource)
            db.commit()
            ngo_id = ngo.id

        barrier = Barrier(2)
        shared_key = uuid.uuid4()

        def attempt_award(index: int):
            body = AllocationCreate(
                project_reference=f"CONCURRENT-PROJECT-{1 if same_request else index}",
                ngo_id=ngo_id,
                risk_level=RiskLevel.LOW,
                items=[AllocationItemCreate(resource_code="CONCURRENCY_STOCK", quantity=7)],
            )
            barrier.wait(timeout=10)
            with Session(engine, expire_on_commit=False) as db:
                try:
                    _, created = create_allocation(
                        db, body, shared_key if same_request else uuid.uuid4()
                    )
                    return "created" if created else "replayed"
                except HTTPException as exc:
                    if exc.status_code != 409:
                        raise
                    return "conflict"

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt_award, (1, 2)))

        assert sorted(results) == (
            ["created", "replayed"] if same_request else ["conflict", "created"]
        )
        with Session(engine) as db:
            resource = db.scalar(
                select(NationalResource).where(NationalResource.code == "CONCURRENCY_STOCK")
            )
            allocations = db.scalars(select(ResourceAllocation)).all()
            assert resource.committed_quantity == 7
            assert resource.committed_quantity <= resource.total_quantity
            assert len(allocations) == 1
            allocation_id = allocations[0].id

        def attempt_close(_):
            barrier.wait(timeout=10)
            with Session(engine, expire_on_commit=False) as db:
                return close_allocation(db, allocation_id).status

        with ThreadPoolExecutor(max_workers=2) as executor:
            assert list(executor.map(attempt_close, (1, 2))) == ["CLOSED", "CLOSED"]
        with Session(engine) as db:
            resource = db.scalar(
                select(NationalResource).where(NationalResource.code == "CONCURRENCY_STOCK")
            )
            assert resource.committed_quantity == 0
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
