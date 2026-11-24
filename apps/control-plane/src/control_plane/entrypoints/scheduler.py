import logging
import math
import os
import time

from control_plane.infrastructure import db
from control_plane.modules.service import schedule_one
from control_plane.modules.tasks import advance


def main():
    logging.basicConfig(level=logging.INFO)
    interval = float(os.getenv("SCHEDULER_POLL_SECONDS", "1"))
    if not math.isfinite(interval) or not 0.05 <= interval <= 60:
        raise ValueError("SCHEDULER_POLL_SECONDS must be between 0.05 and 60")
    while True:
        try:
            with db.engine.begin() as conn:
                advance(conn)
                schedule_one(conn)
        except Exception:
            logging.exception("Scheduling failed; no new allocation committed")
        time.sleep(interval)


if __name__ == "__main__":
    main()
