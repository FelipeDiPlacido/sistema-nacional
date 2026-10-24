import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from urllib.parse import urlsplit

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.core.database import Base
from app.schemas import AllocationClose, AllocationItemConsumption
from app.services.allocation_service import close_allocation


@pytest.mark.postgresql
def test_upgrade_preserves_existing_accounts_stock_and_active_allocations():
    database_url = os.getenv("NATIONAL_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set NATIONAL_TEST_DATABASE_URL to run PostgreSQL migration tests")
    if not urlsplit(database_url).path.lstrip("/").endswith("_test"):
        pytest.fail("Migration tests require a dedicated database ending in _test")
    engine = create_engine(database_url)
    env = {**os.environ, "DATABASE_URL": database_url}

    def migrate(direction, revision):
        subprocess.run(
            [sys.executable, "-m", "alembic", direction, revision],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )

    def clean():
        Base.metadata.drop_all(engine)
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS alembic_version"))

    clean()
    try:
        migrate("upgrade", "0003")
        ids = {
            key: uuid.uuid4()
            for key in ("user", "ngo", "cert", "resource", "allocation", "item", "key")
        }
        params = {**ids, "now": datetime.now(UTC)}
        statements = [
            """INSERT INTO users (id,email,password_hash,is_active,created_at,updated_at)
               VALUES (:user,'legacy@example.org','test-only-hash',true,:now,:now)""",
            """INSERT INTO ngos (id,owner_user_id,name,status,created_at,updated_at)
               VALUES (:ngo,:user,'ONG anterior','ENABLED',:now,:now)""",
            """INSERT INTO certifications (id,code,name,description,is_active)
               VALUES (:cert,'LEGACY_CERT','Certificación anterior','Prueba de migración',true)""",
            """INSERT INTO national_resources
               (id,code,name,category,unit,total_quantity,committed_quantity,
                required_certification_id,is_active,created_at,updated_at)
               VALUES (:resource,'LEGACY_RESOURCE','Stock anterior','SUPPLIES','litros',10,4,
                       :cert,true,:now,:now)""",
            """INSERT INTO resource_allocations
               (id,request_key,project_reference,ngo_id,risk_level,status,created_at)
               VALUES (:allocation,:key,'LEGACY-PROJECT',:ngo,'LOW','ACTIVE',:now)""",
            """INSERT INTO resource_allocation_items (id,allocation_id,resource_id,quantity)
               VALUES (:item,:allocation,:resource,4)""",
        ]
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement), params)
        migrate("upgrade", "head")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT role FROM users WHERE id=:user"), params) == "NGO"
            row = connection.execute(
                text("SELECT ngo_id, total_quantity, committed_quantity FROM national_resources")
            ).one()
            assert tuple(row) == (None, 10, 4)
            assert connection.scalar(text("SELECT count(*) FROM resource_allocations")) == 1
        constraints = inspect(engine).get_unique_constraints("resource_allocations")
        assert any(item["column_names"] == ["project_reference", "ngo_id"] for item in constraints)
        with Session(engine) as db:
            closed = close_allocation(
                db,
                ids["allocation"],
                AllocationClose(
                    items=[AllocationItemConsumption(resource_code="LEGACY_RESOURCE", quantity=0)]
                ),
            )
            assert closed.status == "CLOSED"
            assert closed.items[0].consumed_quantity == 0
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT committed_quantity FROM national_resources")) == 0
        migrate("downgrade", "0003")
        migrate("upgrade", "head")
        subprocess.run(
            [sys.executable, "-m", "alembic", "check"],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
    finally:
        clean()
        engine.dispose()
