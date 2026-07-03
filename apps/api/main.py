from __future__ import annotations

import os
import sys

SERVICE_ROLE = os.environ.get("MARATHONRUNNER_SERVICE_ROLE", "api")


def main() -> None:
    if SERVICE_ROLE == "worker":
        from .worker import run_worker
        run_worker()
    else:
        from .server import run_api
        run_api()


if __name__ == "__main__":
    main()
