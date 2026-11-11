import logging
import time

from control_plane.entrypoints.worker import app, dispatch
from control_plane.infrastructure import db


def publish_one(conn, send=None):
    row = db.one(
        conn,
        """SELECT o.* FROM outbox o JOIN commands c ON c.id=o.command_id
        WHERE NOT c.retired AND NOT c.dispatch_ready AND
        (o.published_at IS NULL OR o.published_at < now()-interval '30 seconds')
        ORDER BY o.created_at
        FOR UPDATE OF o SKIP LOCKED LIMIT 1""",
    )
    if not row:
        return False
    if send:
        send(str(row["command_id"]))
    else:
        with app.producer_or_acquire() as producer:
            # Explicitly declare the durable route before confirmed publication.
            app.amqp.queues["platform.dispatch"].bind(producer.channel).declare()
            dispatch.apply_async(
                args=[str(row["command_id"])],
                task_id=str(row["id"]),
                producer=producer,
                mandatory=True,
                retry=False,
            )
    db.execute(conn, "UPDATE outbox SET published_at=now() WHERE id=:id", id=row["id"])
    return True


def main():
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            with db.engine.begin() as conn:
                published = publish_one(conn)
            if not published:
                time.sleep(0.5)
        except Exception:
            logging.exception("Publish not committed; original command will be retried")
            time.sleep(2)


if __name__ == "__main__":
    main()
