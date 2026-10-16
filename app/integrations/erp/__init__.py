"""app.integrations.erp package — ERP/SIS integration boundary.

Part of Phase 7B Real ERP/SIS Adapter.
"""

from app.integrations.erp.adapter import ErpSisAdapter, QueryExecutor
from app.integrations.erp.config import MySqlErpSettings
from app.integrations.erp.dtos import (
    ErpAcademicSessionDTO,
    ErpClassSectionDTO,
    ErpFreshnessMetadataDTO,
    ErpOrganizationContextDTO,
    ErpStaffDTO,
    ErpStudentHeadcountAggregateDTO,
    ErpSubjectDTO,
    ErpTimetableCommitmentDTO,
    ExternalIdentityMappingDTO,
)
from app.integrations.erp.exceptions import (
    EntityNotFoundError,
    ErpAdapterError,
    ErpConnectionError,
    ErpPermissionDeniedError,
    ErpTimeoutError,
    ReadOnlyViolationError,
    ScopeUnresolvedError,
)
from app.integrations.erp.mysql_adapter import MySqlIreduAdapter

__all__ = [
    "EntityNotFoundError",
    "ErpAcademicSessionDTO",
    "ErpAdapterError",
    "ErpClassSectionDTO",
    "ErpConnectionError",
    "ErpFreshnessMetadataDTO",
    "ErpOrganizationContextDTO",
    "ErpPermissionDeniedError",
    "ErpSisAdapter",
    "ErpStaffDTO",
    "ErpStudentHeadcountAggregateDTO",
    "ErpSubjectDTO",
    "ErpTimeoutError",
    "ErpTimetableCommitmentDTO",
    "ExternalIdentityMappingDTO",
    "MySqlErpSettings",
    "MySqlIreduAdapter",
    "QueryExecutor",
    "ReadOnlyViolationError",
    "ScopeUnresolvedError",
]
