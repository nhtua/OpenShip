"""The authenticated public catalog of the model profiles an installation offers.

A browser can name a profile and nothing else: it cannot choose an endpoint,
an adapter, an SDK argument, or a credential. This is the only place it can
learn what exists, so the catalog states the installation's live view — the
same view a submission is checked against — and never a secret.

What stays out on purpose: credential values, the names of the bindings they
use, and full endpoint URLs. A destination label (host and path) is enough
for a person to recognise where their words go, and it is the same string
the run's own records use.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..auth.models import User
from ..auth.routes import require_jwt
from ..config import settings
from ..providers.configuration import destination_label
from ..providers.contracts import ProfileAvailability
from ..providers.registry import _adapter_supported, load_profiles

router = APIRouter(prefix="/api", tags=["providers"])


class ProfileSummary(BaseModel):
    """One profile, as a browser may act on it."""

    profile_id: str
    label: str
    adapter: str
    model: str
    destination_label: str | None = None
    capabilities: dict
    available: bool
    reason: str | None = None
    explanation: str = ""
    allowed_fallback_profile_ids: tuple[str, ...] = ()


class ModelCatalogResponse(BaseModel):
    """What may be selected now, and which one is the default."""

    default_profile_id: str
    profiles: list[ProfileSummary]


def _catalog_entry(registry, profile_id: str) -> ProfileSummary:
    """A profile and whether it can be used now — no secret is read here."""
    profile = registry.get(profile_id)
    availability: ProfileAvailability = registry.availability(profile_id)
    if not _adapter_supported(profile.adapter):
        # The build cannot serve this provider at all, which is a stronger
        # statement than anything the profile's own configuration says.
        return ProfileSummary(
            profile_id=profile_id,
            label=profile.label,
            adapter=profile.adapter,
            model=profile.model,
            destination_label=destination_label(profile.endpoint),
            capabilities=profile.capabilities.model_dump(),
            available=False,
            reason="adapter_unavailable",
            explanation=(
                f"this build does not include the adapter for profile {profile_id!r}; "
                "choose a profile whose provider is supported"
            ),
            allowed_fallback_profile_ids=profile.allowed_fallback_profile_ids,
        )
    return ProfileSummary(
        profile_id=profile_id,
        label=profile.label,
        adapter=profile.adapter,
        model=profile.model,
        destination_label=destination_label(profile.endpoint),
        capabilities=profile.capabilities.model_dump(),
        available=availability.available,
        reason=availability.reason,
        explanation=availability.explanation,
        allowed_fallback_profile_ids=profile.allowed_fallback_profile_ids,
    )


@router.get("/model-profiles")
async def list_model_profiles(
    user: User = Depends(require_jwt),
):
    """The installation's own profiles, in the order it declares them."""
    registry = load_profiles(settings)
    return ModelCatalogResponse(
        default_profile_id=registry.default_profile_id,
        profiles=[_catalog_entry(registry, profile_id) for profile_id in registry.profile_ids],
    )
