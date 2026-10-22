"""Explicit operator commands for managed Connector keys and recovery."""

# ruff: noqa: BLE001 - CLI driver exceptions may include credential-bearing inputs.

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from uuid import UUID

from ..auth.models import User
from ..config import settings
from ..database.session import SessionLocal
from .credentials.dependencies import get_credential_store
from .credentials.recovery import managed_dispatch_is_locked, set_managed_dispatch_lock
from .credentials.rotation import (
    initialize_key_files,
    reencrypt_credentials_batch,
    retire_encryption_key,
    retire_hmac_key,
    rotate_key_file,
    verify_retained_credentials,
)
from .legacy import import_legacy_search_connectors, preview_legacy_search_import
from .service import ConnectorCommandError, owned_project, require_global_operator


def _source_values(env_file: Path | None) -> dict[str, str]:
    values = dict(os.environ)
    if env_file is not None:
        from dotenv import dotenv_values

        loaded = dotenv_values(env_file)
        values = {
            **{key: value for key, value in loaded.items() if value is not None},
            **values,
        }
    return values


def _legacy_credentials(values: dict[str, str]) -> dict[str, str | None]:
    return {
        "brave": values.get("BRAVE_SEARCH_API_KEY"),
        "tavily": values.get("TAVILY_API_KEY"),
    }


def _available_services(credentials: dict[str, str | None]) -> tuple[str, ...]:
    return tuple(service for service, key in credentials.items() if isinstance(key, str) and key)


def _uuid(value: str | None) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(value)
    except ValueError:
        raise argparse.ArgumentTypeError("expected a UUID") from None


def _common_migration_arguments(parser: argparse.ArgumentParser, *, import_mode: bool) -> None:
    parser.add_argument("--visibility", required=True, choices=("global", "project"))
    parser.add_argument("--project-id", type=_uuid)
    parser.add_argument("--service", action="append", required=True, choices=("brave", "tavily"))
    parser.add_argument("--default-service", action="append", default=[], choices=("brave", "tavily"))
    parser.add_argument("--env-file", type=Path)
    if import_mode:
        parser.add_argument("--actor-user-id", required=True, type=_uuid)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openship-connectors")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("key-init", help="create protected encryption and HMAC key files")
    init.add_argument("--encryption-file", type=Path, default=settings.connector_encryption_key_file)
    init.add_argument("--hmac-file", type=Path, default=settings.connector_idempotency_hmac_key_file)
    init.set_defaults(handler=_key_init)

    rotate = commands.add_parser("key-rotate", help="add a new active key without retiring old keys")
    rotate.add_argument("--key-kind", required=True, choices=("encryption", "hmac"))
    rotate.add_argument("--key-id", required=True)
    rotate.set_defaults(handler=_key_rotate)

    reencrypt = commands.add_parser("key-reencrypt", help="re-encrypt old credential rows in resumable batches")
    reencrypt.add_argument("--batch-size", type=int, default=100)
    reencrypt.set_defaults(handler=_key_reencrypt)

    retire = commands.add_parser("key-retire", help="retire a verified, unused key version")
    retire.add_argument("--key-kind", required=True, choices=("encryption", "hmac"))
    retire.add_argument("--key-id", required=True)
    retire.set_defaults(handler=_key_retire)

    dispatch = commands.add_parser("dispatch", help="lock or unlock managed Connector dispatch")
    dispatch.add_argument("state", choices=("lock", "unlock"))
    dispatch.set_defaults(handler=_dispatch)

    preview = commands.add_parser("search-import-preview", help="preview selected legacy key imports")
    _common_migration_arguments(preview, import_mode=False)
    preview.set_defaults(handler=_search_import_preview)

    migrate = commands.add_parser("search-import", help="import explicitly selected legacy search keys")
    _common_migration_arguments(migrate, import_mode=True)
    migrate.set_defaults(handler=_search_import)
    return parser


def _key_init(args: argparse.Namespace) -> int:
    initialize_key_files(args.encryption_file, args.hmac_file)
    print("Protected key files initialized; key material was not displayed.")
    return 0


def _key_rotate(args: argparse.Namespace) -> int:
    if not managed_dispatch_is_locked():
        raise RuntimeError("lock_managed_dispatch_before_key_rotation")
    path = (
        settings.connector_encryption_key_file
        if args.key_kind == "encryption"
        else settings.connector_idempotency_hmac_key_file
    )
    rotate_key_file(path, key_id=args.key_id, key_kind=args.key_kind)
    print(f"New active {args.key_kind} key added; prior versions were retained.")
    return 0


def _key_reencrypt(args: argparse.Namespace) -> int:
    if not managed_dispatch_is_locked():
        raise RuntimeError("lock_managed_dispatch_before_reencryption")
    if settings.connector_installation_id is None:
        raise RuntimeError("connector_installation_id_unavailable")
    store = get_credential_store()
    count = 0
    db = SessionLocal()
    try:
        while True:
            batch_count = reencrypt_credentials_batch(
                db,
                store,
                installation_id=settings.connector_installation_id,
                batch_size=args.batch_size,
            )
            count += batch_count
            if batch_count == 0:
                break
        verified = verify_retained_credentials(
            db, store, installation_id=settings.connector_installation_id
        )
    finally:
        db.close()
    print(f"Re-encrypted {count} credential records; verified {verified} retained records.")
    return 0


def _key_retire(args: argparse.Namespace) -> int:
    if not managed_dispatch_is_locked():
        raise RuntimeError("lock_managed_dispatch_before_key_retirement")
    db = SessionLocal()
    try:
        if args.key_kind == "encryption":
            if settings.connector_installation_id is None:
                raise RuntimeError("connector_installation_id_unavailable")
            retire_encryption_key(
                db,
                settings.connector_encryption_key_file,
                key_id=args.key_id,
                installation_id=settings.connector_installation_id,
            )
        else:
            retire_hmac_key(
                db,
                settings.connector_idempotency_hmac_key_file,
                key_id=args.key_id,
            )
    finally:
        db.close()
    print(f"Retired {args.key_kind} key {args.key_id} after reference checks.")
    return 0


def _dispatch(args: argparse.Namespace) -> int:
    locked = args.state == "lock"
    set_managed_dispatch_lock(locked=locked)
    print(f"Managed Connector dispatch is {'locked' if locked else 'unlocked'}.")
    return 0


def _search_import_preview(args: argparse.Namespace) -> int:
    preview_legacy_search_import(
        services=args.service,
        visibility=args.visibility,
        project_id=args.project_id,
        default_services=args.default_service,
    )
    credentials = _legacy_credentials(_source_values(args.env_file))
    preview = preview_legacy_search_import(
        services=args.service,
        visibility=args.visibility,
        project_id=args.project_id,
        default_services=args.default_service,
        available_services=_available_services(credentials),
    )
    print(json.dumps(preview, sort_keys=True))
    return 0


def _search_import(args: argparse.Namespace) -> int:
    preview_legacy_search_import(
        services=args.service,
        visibility=args.visibility,
        project_id=args.project_id,
        default_services=args.default_service,
    )
    db = SessionLocal()
    try:
        actor = db.query(User).filter(User.id == args.actor_user_id).first()
        if actor is None:
            raise RuntimeError("operator_user_not_found")
        if args.visibility == "global":
            require_global_operator(actor)
        else:
            assert args.project_id is not None
            owned_project(db, project_id=args.project_id, user_id=actor.id)
        credentials = _legacy_credentials(_source_values(args.env_file))
        imported = import_legacy_search_connectors(
            db,
            actor=actor,
            visibility=args.visibility,
            project_id=args.project_id,
            services=args.service,
            credentials=credentials,
            default_services=args.default_service,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    print(
        json.dumps(
            [
                {
                    "id": item["id"],
                    "kind": item["kind"],
                    "visibility": item["visibility"],
                    "revision": item["revision"],
                    "status": item["status"],
                }
                for item in imported
            ],
            sort_keys=True,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except ConnectorCommandError as refused:
        print(refused.code, file=sys.stderr)
        return 2
    except Exception:
        # Driver and parser exceptions may contain query parameters or inputs.
        print("Connector operation failed.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
