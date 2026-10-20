"""Run from the repository root after installing the backend dependencies."""

import asyncio
import sys

from dotenv import load_dotenv

from app.demo.data import SeedError
from app.demo.loader import SeedConfig, run_seed


def main() -> int:
    load_dotenv()
    try:
        result = asyncio.run(run_seed(SeedConfig.from_env()))
    except SeedError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        # Driver/SQL errors may contain credentials, parameters or stored hashes.
        print(
            "Seed failed: database or schema operation failed; no changes committed",
            file=sys.stderr,
        )
        return 1
    for table, counts in result.items():
        print(f"{table}: inserted={counts['inserted']} existing={counts['existing']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
