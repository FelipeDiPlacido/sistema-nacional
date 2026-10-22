import logging
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models import (
    NGO,
    AllocationStatus,
    NationalResource,
    NGOCertification,
    ResourceAllocation,
    ResourceAllocationItem,
)
from app.models.national import NGOCertificationStatus, NGOStatus, RiskLevel
from app.schemas import (
    AllocationConstraintDetail,
    AllocationCreate,
    AllocationItemResponse,
    AllocationResponse,
    AvailabilityConstraint,
)

logger = logging.getLogger(__name__)


def _load_allocation(db: Session, allocation_id: uuid.UUID) -> ResourceAllocation:
    allocation = db.scalar(
        select(ResourceAllocation)
        .options(selectinload(ResourceAllocation.items).joinedload(ResourceAllocationItem.resource))
        .where(ResourceAllocation.id == allocation_id)
    )
    if allocation is None:
        raise HTTPException(404, "No se encontró la adjudicación")
    return allocation


def _allocation_response(allocation: ResourceAllocation) -> AllocationResponse:
    return AllocationResponse(
        id=allocation.id,
        request_key=allocation.request_key,
        project_reference=allocation.project_reference,
        ngo_id=allocation.ngo_id,
        risk_level=RiskLevel(allocation.risk_level),
        status=AllocationStatus(allocation.status),
        created_at=allocation.created_at,
        closed_at=allocation.closed_at,
        items=[
            AllocationItemResponse(
                resource_code=item.resource.code,
                resource_name=item.resource.name,
                unit=item.resource.unit,
                quantity=item.quantity,
            )
            for item in allocation.items
        ],
    )


def _find_existing_allocation(
    db: Session, request_key: uuid.UUID, project_reference: str, ngo_id: uuid.UUID
) -> ResourceAllocation | None:
    return db.scalar(
        select(ResourceAllocation)
        .options(selectinload(ResourceAllocation.items).joinedload(ResourceAllocationItem.resource))
        .where(
            or_(
                ResourceAllocation.request_key == request_key,
                and_(
                    ResourceAllocation.project_reference == project_reference,
                    ResourceAllocation.ngo_id == ngo_id,
                ),
            )
        )
    )


def _matches_request(
    allocation: ResourceAllocation, body: AllocationCreate, request_key: uuid.UUID
) -> bool:
    existing_items = sorted((item.resource.code, item.quantity) for item in allocation.items)
    requested_items = sorted((item.resource_code, item.quantity) for item in body.items)
    return (
        allocation.request_key == request_key
        and allocation.project_reference == body.project_reference
        and allocation.ngo_id == body.ngo_id
        and allocation.risk_level == body.risk_level.value
        and existing_items == requested_items
    )


def create_allocation(
    db: Session, body: AllocationCreate, request_key: uuid.UUID
) -> tuple[AllocationResponse, bool]:
    try:
        with db.begin():
            existing = _find_existing_allocation(
                db, request_key, body.project_reference, body.ngo_id
            )
            if existing is not None:
                if not _matches_request(existing, body, request_key):
                    raise HTTPException(
                        409,
                        "La clave o referencia del proyecto ya se usó con otra adjudicación",
                    )
                response = _allocation_response(existing)
                created = False
            else:
                requested_codes = [item.resource_code for item in body.items]
                if len(requested_codes) != len(set(requested_codes)):
                    raise HTTPException(422, "No se puede repetir un recurso en la adjudicación")

                ngo = db.scalar(select(NGO).where(NGO.id == body.ngo_id).with_for_update())
                if ngo is None:
                    raise HTTPException(404, "No se encontró la ONG")

                resources = db.scalars(
                    select(NationalResource)
                    .where(
                        NationalResource.code.in_(requested_codes),
                        NationalResource.is_active.is_(True),
                    )
                    .order_by(NationalResource.code)
                    .with_for_update()
                ).all()
                resources_by_code = {resource.code: resource for resource in resources}
                missing_codes = sorted(set(requested_codes) - resources_by_code.keys())
                if missing_codes:
                    raise HTTPException(
                        404,
                        {"message": "No se encontraron recursos activos", "codes": missing_codes},
                    )

                # A concurrent request may have committed while this transaction waited for
                # the ordered resource locks.
                existing = _find_existing_allocation(
                    db, request_key, body.project_reference, body.ngo_id
                )
                if existing is not None:
                    if not _matches_request(existing, body, request_key):
                        raise HTTPException(
                            409,
                            "La clave o referencia del proyecto ya se usó con otra adjudicación",
                        )
                    response = _allocation_response(existing)
                    created = False
                else:
                    certifications = db.scalars(
                        select(NGOCertification).where(
                            NGOCertification.ngo_id == ngo.id,
                            NGOCertification.certification_id.in_(
                                {resource.required_certification_id for resource in resources}
                            ),
                        )
                    ).all()
                    certifications_by_id = {item.certification_id: item for item in certifications}
                    constraints: list[AllocationConstraintDetail] = []
                    for requested in body.items:
                        resource = resources_by_code[requested.resource_code]
                        certification = certifications_by_id.get(resource.required_certification_id)
                        issues: list[AvailabilityConstraint] = []
                        if resource.ngo_id != ngo.id:
                            issues.append(AvailabilityConstraint.RESOURCE_NOT_OWNED)
                        if ngo.status != NGOStatus.ENABLED or not ngo.owner.is_active:
                            issues.append(AvailabilityConstraint.ONG_NOT_ENABLED)
                        if (
                            certification is None
                            or certification.status != NGOCertificationStatus.VERIFIED
                        ):
                            issues.append(AvailabilityConstraint.CERTIFICATION_NOT_VERIFIED)
                        elif not resource.required_certification.is_active:
                            issues.append(AvailabilityConstraint.CERTIFICATION_INACTIVE)
                        elif body.risk_level.value not in certification.allowed_risk_levels:
                            issues.append(AvailabilityConstraint.RISK_LEVEL_NOT_ALLOWED)

                        available = resource.total_quantity - resource.committed_quantity
                        if available < requested.quantity:
                            issues.append(AvailabilityConstraint.INSUFFICIENT_STOCK)
                        if issues:
                            constraints.append(
                                AllocationConstraintDetail(
                                    resource_code=resource.code,
                                    requested_quantity=requested.quantity,
                                    available_quantity=available,
                                    constraints=issues,
                                )
                            )

                    if constraints:
                        raise HTTPException(
                            409,
                            {
                                "code": "ALLOCATION_CONSTRAINTS",
                                "message": (
                                    "La adjudicación no se comprometió; revisá las "
                                    "capacidades y disponibilidad."
                                ),
                                "items": [item.model_dump(mode="json") for item in constraints],
                            },
                        )

                    allocation = ResourceAllocation(
                        request_key=request_key,
                        project_reference=body.project_reference,
                        ngo_id=ngo.id,
                        risk_level=body.risk_level.value,
                        status=AllocationStatus.ACTIVE,
                        items=[],
                    )
                    for requested in body.items:
                        resource = resources_by_code[requested.resource_code]
                        resource.committed_quantity += requested.quantity
                        allocation.items.append(
                            ResourceAllocationItem(
                                resource=resource,
                                quantity=requested.quantity,
                            )
                        )
                    db.add(allocation)
                    db.flush()
                    allocation_id = allocation.id
                    created = True

        if created:
            return _allocation_response(_load_allocation(db, allocation_id)), True
        return response, False
    except IntegrityError as exc:
        db.rollback()
        existing = _find_existing_allocation(db, request_key, body.project_reference, body.ngo_id)
        if existing is not None and _matches_request(existing, body, request_key):
            return _allocation_response(existing), False
        logger.info("allocation_registration_conflict")
        raise HTTPException(
            409, "La clave o referencia del proyecto ya se usó con otra adjudicación"
        ) from exc


def close_allocation(
    db: Session, allocation_id: uuid.UUID, ngo_id: uuid.UUID | None = None
) -> AllocationResponse:
    with db.begin():
        allocation = db.scalar(
            select(ResourceAllocation)
            .where(ResourceAllocation.id == allocation_id)
            .with_for_update()
        )
        if allocation is None:
            raise HTTPException(404, "No se encontró la adjudicación")
        if ngo_id is not None and allocation.ngo_id != ngo_id:
            raise HTTPException(404, "No se encontró la adjudicación")
        if allocation.status != AllocationStatus.CLOSED:
            items = db.scalars(
                select(ResourceAllocationItem)
                .where(ResourceAllocationItem.allocation_id == allocation.id)
                .order_by(ResourceAllocationItem.resource_id)
            ).all()
            resource_ids = [item.resource_id for item in items]
            resources = db.scalars(
                select(NationalResource)
                .where(NationalResource.id.in_(resource_ids))
                .order_by(NationalResource.code)
                .with_for_update()
            ).all()
            resources_by_id = {resource.id: resource for resource in resources}
            for item in items:
                resource = resources_by_id.get(item.resource_id)
                if resource is None or resource.committed_quantity < item.quantity:
                    logger.error(
                        "allocation_release_invariant_violation",
                        extra={"allocation_id": str(allocation.id)},
                    )
                    raise HTTPException(
                        500,
                        (
                            "No se pudo cerrar el proyecto por una inconsistencia del "
                            "inventario; requiere revisión."
                        ),
                    )
            for item in items:
                resources_by_id[item.resource_id].committed_quantity -= item.quantity
            allocation.status = AllocationStatus.CLOSED
            allocation.closed_at = datetime.now(UTC)
            allocation_id = allocation.id
    return _allocation_response(_load_allocation(db, allocation_id))


def get_allocation(
    db: Session, allocation_id: uuid.UUID, ngo_id: uuid.UUID | None
) -> AllocationResponse:
    allocation = _load_allocation(db, allocation_id)
    if ngo_id is not None and allocation.ngo_id != ngo_id:
        raise HTTPException(404, "No se encontró la adjudicación")
    return _allocation_response(allocation)


def list_allocations(
    db: Session, ngo_id: uuid.UUID | None, project_reference: str | None, offset: int, limit: int
) -> list[AllocationResponse]:
    query = (
        select(ResourceAllocation)
        .options(selectinload(ResourceAllocation.items).joinedload(ResourceAllocationItem.resource))
        .order_by(ResourceAllocation.created_at, ResourceAllocation.id)
        .offset(offset)
        .limit(limit)
    )
    if ngo_id is not None:
        query = query.where(ResourceAllocation.ngo_id == ngo_id)
    if project_reference is not None:
        query = query.where(ResourceAllocation.project_reference == project_reference)
    return [_allocation_response(item) for item in db.scalars(query)]
