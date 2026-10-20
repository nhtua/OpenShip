from fastapi import APIRouter, Depends

from src.openship.auth.models import User
from src.openship.auth.routes import require_jwt
from src.openship.capabilities.catalog import list_capabilities
from src.openship.capabilities.contracts import CapabilitySummary

router = APIRouter(tags=["capabilities"])


@router.get("/api/capabilities", response_model=list[CapabilitySummary])
def get_capabilities(_user: User = Depends(require_jwt)) -> list[CapabilitySummary]:
    return list_capabilities()
