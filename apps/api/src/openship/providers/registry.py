"""The server-owned provider registry: exact lookup and per-run pinning.

Only the server resolves a profile ID to a configuration. A browser can name
an ID and nothing else, so it can never choose an endpoint, a model, an
adapter implementation, or an SDK argument.
"""

from __future__ import annotations

import hashlib

from src.openship.providers.configuration import parse_profiles
from src.openship.providers.contracts import (
    ProfileAvailability,
    RunLimits,
    RunModelConfig,
    pin_profile,
    validate_limits,
)
from src.openship.providers.errors import ProviderError, credential_binding_present

#: Adapter factories, registered by the adapter that needs them. The registry
#: holds no SDK import of its own.
_adapter_factories: dict[str, object] = {}


def register_adapter(name: str, factory) -> None:
    """Adapters register themselves; the registry never invents one."""
    _adapter_factories[name] = factory


def build_adapter(profile):
    """Instantiate the adapter a profile names, importing its module on demand.

    The registry holds no SDK import of its own: an adapter module registers
    itself the first time a profile asks for it, so a profile that names an
    adapter nobody shipped is a configuration error, never a guess.
    """
    factory = _adapter_factories.get(profile.adapter)
    if factory is None:
        import importlib

        importlib.import_module(f"src.openship.providers.{profile.adapter}")
        factory = _adapter_factories.get(profile.adapter)
    if factory is None:
        raise ProviderError(
            "configuration_error",
            safe_message=f"no adapter is registered for profile {profile.id!r}",
        )
    return factory(profile)


def _adapter_supported(name: str) -> bool:
    """Whether this build ships the adapter a profile names.

    Adapters register themselves when their module is first imported, which is
    what `build_adapter` does; availability asks the same question without
    building a client, so a catalog never imports an SDK of its own.
    """
    if name in _adapter_factories:
        return True
    import importlib

    try:
        importlib.import_module(f"src.openship.providers.{name}")
    except ImportError:
        return False
    return name in _adapter_factories


class ProviderRegistry:
    """Immutable, non-secret view of the installation's model profiles."""

    def __init__(self, profiles: dict[str, object], default_profile_id: str):
        self._profiles = dict(profiles)
        self.default_profile_id = default_profile_id

    @property
    def profile_ids(self) -> tuple[str, ...]:
        return tuple(self._profiles)

    def get(self, profile_id: str):
        """Exact lookup. An unknown ID is a configuration error, never a guess."""
        try:
            return self._profiles[profile_id]
        except KeyError:
            raise ProviderError(
                "configuration_error",
                safe_message=(
                    f"profile {profile_id!r} is not configured for this installation; "
                    "choose one of the available profiles"
                ),
            ) from None

    def availability(self, profile_id: str) -> ProfileAvailability:
        """Whether a profile can be used now, without naming any secret value."""
        profile = self.get(profile_id)
        if profile.adapter_options.authentication == "bearer" and not profile.credential_ref:
            return ProfileAvailability(
                profile_id=profile_id,
                available=False,
                reason="configuration_error",
                explanation=(
                    f"profile {profile_id!r} requires a bearer credential but declares "
                    "no credential_ref"
                ),
            )
        if profile.credential_ref and not credential_binding_present(profile.credential_ref):
            return ProfileAvailability(
                profile_id=profile_id,
                available=False,
                reason="missing_credentials",
                explanation=(
                    f"profile {profile_id!r} needs the credential binding "
                    f"{profile.credential_ref}, which this installation does not configure"
                ),
            )
        return ProfileAvailability(profile_id=profile_id, available=True)

    def adapter_factory(self, profile):
        """The factory for a profile's adapter, or a configuration error."""
        try:
            return _adapter_factories[profile.adapter]
        except KeyError:
            raise ProviderError(
                "configuration_error",
                safe_message=f"no adapter is registered for profile {profile.id!r}",
            ) from None

    def pin_selection(self, profile_id: str | None, fallback_ids=()) -> RunModelConfig:
        """Freeze the configuration one run will execute.

        The snapshot carries the full settings, not just IDs, so a later
        change to the installation cannot change what a queued run runs.
        """
        primary = self.get(profile_id or self.default_profile_id)

        requested = list(fallback_ids or ())
        if len(set(requested)) != len(requested):
            raise ProviderError(
                "configuration_error",
                safe_message="fallback profile IDs must be distinct",
            )
        allowed = list(primary.allowed_fallback_profile_ids)
        unauthorized = [name for name in requested if name not in allowed]
        if unauthorized or primary.id in requested:
            raise ProviderError(
                "configuration_error",
                safe_message=(
                    f"profile {primary.id!r} does not allow fallback to "
                    + ", ".join(unauthorized or [primary.id])
                ),
            )

        limits = primary.limits
        validate_limits(limits, primary)

        return RunModelConfig(
            schema_version=1,
            primary=primary,
            fallbacks=tuple(self.get(name) for name in requested),
            limits=limits,
            binding_origin="submission",
        )


def _configuration_digest(settings) -> str:
    """A cache key over non-secret configuration only."""
    material = {
        "profiles": settings.model_profiles_json,
        "default": settings.model_default_profile_id,
        "max_output_tokens": settings.model_max_tokens,
    }
    return hashlib.sha256(repr(sorted(material.items())).encode("utf-8")).hexdigest()


def _build_registry(settings) -> ProviderRegistry:
    """Parse, validate, and freeze every profile this installation declares."""
    declared = settings.model_profiles_json.strip()
    if not declared:
        raise ProviderError(
            "configuration_error",
            safe_message="MODEL_PROFILES_JSON must declare at least one model profile",
        )
    profiles = parse_profiles(declared)
    if not profiles:
        raise ProviderError(
            "configuration_error",
            safe_message="MODEL_PROFILES_JSON must declare at least one model profile",
        )
    default_id = settings.model_default_profile_id
    known = {profile.id for profile in profiles}
    if not default_id or default_id not in known:
        raise ProviderError(
            "configuration_error",
            safe_message="MODEL_DEFAULT_PROFILE_ID must name one of the declared model profiles",
        )
    for profile in profiles:
        unknown = [
            name for name in profile.allowed_fallback_profile_ids if name not in known
        ]
        if unknown:
            raise ProviderError(
                "configuration_error",
                safe_message=(
                    f"profile {profile.id!r} allows fallback to "
                    f"{unknown[0]!r}, which is not declared"
                ),
            )

    installation = RunLimits(max_output_tokens=settings.model_max_tokens)
    pinned = {
        profile.id: pin_profile(profile, defaults=installation) for profile in profiles
    }
    return ProviderRegistry(pinned, default_id)


#: Profiles are parsed once per process: configuration is read at startup, and
#: a run's snapshot is what later execution uses.
_REGISTRIES: dict[str, ProviderRegistry] = {}


def load_profiles(settings) -> ProviderRegistry:
    """The installation's provider registry, parsed at most once per process."""
    key = _configuration_digest(settings)
    if key not in _REGISTRIES:
        _REGISTRIES[key] = _build_registry(settings)
    return _REGISTRIES[key]
