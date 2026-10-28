"""Wait for authenticated JetStream readiness without exposing connection data."""

from __future__ import annotations

import argparse
import asyncio
import json
import time

from .connection import connect_nats


async def wait_for_jetstream(*, timeout: float, interval: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        client = None
        try:
            client = await connect_nats(
                name="openship-messaging-startup-check",
                allow_reconnect=False,
            )
            await client.jetstream(timeout=3).account_info()
            return True
        except Exception:  # noqa: BLE001 - startup probe reports a safe result only
            await asyncio.sleep(min(interval, max(0.0, deadline - time.monotonic())))
        finally:
            if client is not None:
                await client.close()
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--interval", type=float, default=2)
    args = parser.parse_args()
    if args.timeout <= 0 or args.interval <= 0:
        parser.error("timeout and interval must be positive")
    ready = asyncio.run(
        wait_for_jetstream(timeout=args.timeout, interval=args.interval)
    )
    print(json.dumps({"status": "ready" if ready else "unavailable"}, sort_keys=True))
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
