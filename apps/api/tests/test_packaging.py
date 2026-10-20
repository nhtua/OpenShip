"""What an installation is packaged to run, checked from the packaging itself.

These cases read the files an operator reads - ``docker-compose.yml``,
``.env.example``, and the live smoke test's opt-in - rather than the code
they start. The breaks they catch are the ones a person meets and the suite
elsewhere cannot: a worker configured with a different profile than the API
queued, so "which model answered" has two answers; a credential carried into
the packaging as a value instead of named; a binding the packaging interpolates
that the documented environment never mentions, so following the docs leaves a
service half-configured; and an operator opting into qualification with a name
nothing reads, which skips quietly and looks like a pass.

Nothing here prints what a binding holds. The packaging is read as text and
only its shape is asserted, so validating it cannot put a secret in a log.
"""

import re
import tomllib
from pathlib import Path

import pytest
import yaml

from src.openship.config import settings
from src.openship.providers.errors import ProviderError
from src.openship.providers.registry import load_profiles

from .conftest import configure_profiles, profile_spec

HERE = Path(__file__).resolve()
#: ``apps/api`` and the repository root, anchored from this file rather than
#: assumed: the packaging is checked where it actually is.
API_DIR = HERE.parents[1]
ROOT = API_DIR.parents[1]
COMPOSE = ROOT / "docker-compose.yml"
ENVIRONMENT = API_DIR / ".env.example"
PYPROJECT = API_DIR / "pyproject.toml"
SMOKE = HERE.parent / "live" / "test_provider_smoke.py"

#: The services that share a profile: one queues a run and the other executes
#: it, so a profile that means different things to the two of them is a
#: question with two answers rather than one.
SHARED_SERVICES = ("api", "worker")

#: Search runs execute in the worker, so these bindings intentionally do not
#: need to be present in the API container.
WORKER_ONLY_CREDENTIALS = {"BRAVE_SEARCH_API_KEY", "TAVILY_API_KEY"}

#: Provider credentials are optional bindings, never values embedded in the
#: packaging. Installation secrets such as JWT_SECRET have a separate policy.
PROVIDER_CREDENTIALS = {
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    *WORKER_ONLY_CREDENTIALS,
}

#: ``${NAME}`` or ``${NAME:-default}`` - and nothing else. The ``:?`` form is
#: deliberately not accepted: a required credential would make the packaging
#: fail to render, and an installation with no key is a working installation
#: whose catalog says which profiles it cannot serve.
INTERPOLATION = re.compile(r"^\$\{(?P<name>[A-Z][A-Z0-9_]*)(?::-(?P<default>[^}]*))?\}$")

#: What an ``.env.example`` line declares.
DOCUMENTED_BINDING = re.compile(r"^(?P<name>[A-Z][A-Z0-9_]*)=")


def _services() -> dict:
    """The Compose file's services, as the packaging says they are."""
    return yaml.safe_load(COMPOSE.read_text())["services"]


def _documented_bindings() -> set:
    """Every binding name the documented environment declares."""
    return {
        match.group("name")
        for line in ENVIRONMENT.read_text().splitlines()
        if (match := DOCUMENTED_BINDING.match(line.strip()))
    }


def test_the_packaging_names_the_services_that_share_a_profile():
    """The two services the claim is about exist, and are configured."""
    services = _services()
    for name in SHARED_SERVICES:
        assert name in services, f"the packaging does not name {name}"
        assert "environment" in services[name], f"{name} is configured by nothing"


def test_the_two_services_are_configured_with_the_same_thing():
    """A profile means the same thing to both services; search keys stay worker-only.

    Search is executed by the worker, so its provider keys are the only
    intentional environment difference. Other mismatches can make the API
    queue a run under settings the worker does not use.
    """
    services = _services()
    api, worker = (services[name]["environment"] for name in SHARED_SERVICES)
    api_shared = {key: value for key, value in api.items() if key not in WORKER_ONLY_CREDENTIALS}
    worker_shared = {key: value for key, value in worker.items() if key not in WORKER_ONLY_CREDENTIALS}
    assert api_shared == worker_shared, (
        "the API and the worker are configured differently: a run one queues "
        "is not executed under the profile the other offered"
    )
    assert WORKER_ONLY_CREDENTIALS <= worker.keys()
    assert WORKER_ONLY_CREDENTIALS.isdisjoint(api)


def test_provider_credentials_are_named_never_carried():
    """Provider secrets stay optional named bindings rather than packaged values.

    This deliberately excludes required installation secrets such as the JWT
    signing key, which have their own startup requirements.
    """
    for name in SHARED_SERVICES:
        environment = _services()[name]["environment"]
        for key in PROVIDER_CREDENTIALS & environment.keys():
            value = environment[key]
            match = INTERPOLATION.match(str(value))
            assert match, (
                f"{name}'s {key} carries a value rather than naming a binding"
            )
            assert match.group("name") == key, (
                f"{name}'s {key} is filled from {match.group('name')}, so the "
                "documented environment names a different binding than the service gets"
            )
            assert match.group("default") == "", (
                f"{name}'s {key} defaults to something, which hides that this "
                "installation has no credential for it"
            )


def test_the_documented_environment_names_every_binding_the_packaging_asks_for():
    """An operator copying the documented environment gets what the packaging wants."""
    wanted = set()
    for name in SHARED_SERVICES:
        for value in _services()[name]["environment"].values():
            match = INTERPOLATION.match(str(value))
            if match:
                wanted.add(match.group("name"))

    missing = sorted(wanted - _documented_bindings())
    assert not missing, (
        f"the packaging asks for {missing}, which the documented environment never mentions"
    )


def test_the_documented_profiles_form_a_usable_installation_configuration():
    """Copying the example supplies valid profiles and an explicit default."""
    from dotenv import dotenv_values

    from src.openship.config import Settings

    declared = dotenv_values(ENVIRONMENT)
    registry = load_profiles(Settings(
        _env_file=None,
        model_profiles_json=declared["MODEL_PROFILES_JSON"],
        model_default_profile_id=declared["MODEL_DEFAULT_PROFILE_ID"],
    ))

    assert registry.profile_ids
    assert registry.default_profile_id in registry.profile_ids
    assert registry.get(registry.default_profile_id).credential_ref == "OPENAI_API_KEY"


def test_qualification_is_opted_into_by_the_name_the_packaging_documents():
    """The opt-in an operator sets is the one the live test reads.

    A name only one of the two has is the quiet break: the operator sets a
    variable nothing reads, the live test skips, and the run looks like a
    qualification that never happened. The marker is checked in the same place
    because without it the opt-in is not a lane - pytest refuses the selection
    rather than running it.
    """
    reads = re.search(r'^PROFILE_ID_ENV = "(?P<name>[A-Z_]+)"', SMOKE.read_text(), re.MULTILINE)
    assert reads, "the live test no longer names the variable it opts in on"
    opted_into = reads.group("name")

    assert opted_into in _documented_bindings(), (
        f"the documented environment never mentions {opted_into}, so an operator "
        "following the docs cannot opt into qualification at all"
    )

    markers = tomllib.load(PYPROJECT.open("rb"))["tool"]["pytest"]["ini_options"]["markers"]
    assert any(marker.split(":", 1)[0] == "provider_live" for marker in markers), (
        "the provider_live lane is not registered, so pytest cannot select it"
    )


def test_an_unqualified_selection_never_reaches_a_destination(monkeypatch):
    """Qualification asked for an undeclared profile is refused before any request.

    This is the live smoke test's guard, run where no provider is contacted:
    an operator who asks for qualification of a profile this installation never
    declared has to be answered with a reason, not sent to whichever
    destination happens to answer. Nothing is built here, so nothing was
    contacted and nothing was charged.
    """
    configure_profiles(monkeypatch, [profile_spec("primary-fixture")], "primary-fixture")

    built = []
    monkeypatch.setattr(
        "src.openship.providers.registry.build_adapter",
        lambda profile: built.append(profile.id),
    )
    registry = load_profiles(settings)

    with pytest.raises(ProviderError) as refused:
        registry.get("not-declared")
    assert refused.value.code == "configuration_error"
    assert not built, "refusing an undeclared profile still reached for an adapter"
