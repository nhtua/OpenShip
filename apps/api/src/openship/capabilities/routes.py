from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.openship.auth.models import User
from src.openship.auth.routes import require_jwt
from src.openship.capabilities.catalog import list_capabilities
from src.openship.capabilities.contracts import CapabilitySummary
from src.openship.database.session import get_db
from src.openship.execution.availability import read_tool_availability

router = APIRouter(tags=["capabilities"])


@router.get("/api/capabilities", response_model=list[CapabilitySummary])
def get_capabilities(
    _user: User = Depends(require_jwt), db: Session = Depends(get_db)
) -> list[CapabilitySummary]:
    return list_capabilities(
        availability=read_tool_availability(db),
        credential_check=lambda _binding: True,
    )
