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
from app.services.allocation_service import create_allocation


@pytest.mark.postgresql
def test_concurrent_awards_cannot_overallocate_one_resource():
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
            resource = NationalResource(
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

        def attempt_award(index: int):
            body = AllocationCreate(
                project_reference=f"CONCURRENT-PROJECT-{index}",
                ngo_id=ngo_id,
                risk_level=RiskLevel.LOW,
                items=[AllocationItemCreate(resource_code="CONCURRENCY_STOCK", quantity=7)],
            )
            barrier.wait()
            with Session(engine, expire_on_commit=False) as db:
                try:
                    create_allocation(db, body, uuid.uuid4())
                    return "created"
                except HTTPException as exc:
                    if exc.status_code != 409:
                        raise
                    return "conflict"

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt_award, (1, 2)))

        assert sorted(results) == ["conflict", "created"]
        with Session(engine) as db:
            resource = db.scalar(
                select(NationalResource).where(NationalResource.code == "CONCURRENCY_STOCK")
            )
            allocations = db.scalars(select(ResourceAllocation)).all()
            assert resource.committed_quantity == 7
            assert resource.committed_quantity <= resource.total_quantity
            assert len(allocations) == 1
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
