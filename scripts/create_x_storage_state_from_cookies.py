from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from pathlib import Path


DEFAULT_OUTPUT_PATH = Path(".playwright") / "x_storage_state.json"
COOKIE_ENV_VARS = {
    "auth_token": "X_AUTH_TOKEN",
    "ct0": "X_CT0",
    "twid": "X_TWID",
    "guest_id": "X_GUEST_ID",
    "personalization_id": "X_PERSONALIZATION_ID",
}
REQUIRED_ENV_VARS = ("X_AUTH_TOKEN", "X_CT0")


def build_storage_state(environment: Mapping[str, str]) -> dict[str, object]:
    missing = [name for name in REQUIRED_ENV_VARS if not environment.get(name, "").strip()]
    if missing:
        raise ValueError(f"Missing required environment variable(s): {', '.join(missing)}")

    cookies: list[dict[str, object]] = []
    for cookie_name, environment_name in COOKIE_ENV_VARS.items():
        value = environment.get(environment_name, "").strip()
        if not value:
            continue
        cookies.append(
            {
                "name": cookie_name,
                "value": value,
                "domain": ".x.com",
                "path": "/",
                "expires": -1,
                "httpOnly": cookie_name == "auth_token",
                "secure": True,
                "sameSite": "Lax",
            }
        )

    return {"cookies": cookies, "origins": []}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create Playwright storage state from existing X cookie environment variables."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Output path for the Playwright storage-state JSON file.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        storage_state = build_storage_state(os.environ)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    output_path: Path = args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(storage_state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"X storage state written to {output_path}")
    print("WARNING: This file contains sensitive login credentials. Never commit or share it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
