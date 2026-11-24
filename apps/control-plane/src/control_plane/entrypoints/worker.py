import os

from celery import Celery
from control_plane.infrastructure import db
from kombu import Exchange, Queue
from sqlalchemy.exc import OperationalError

app = Celery(
    "artemis_platform",
    broker=os.getenv("BROKER_URL", "amqp://127.0.0.1:56732/platform"),
)
exchange = Exchange("platform", type="direct", durable=True)
app.conf.update(
    task_queues=(
        Queue("platform.dispatch", exchange=exchange, routing_key="dispatch", durable=True),
    ),
    task_default_queue="platform.dispatch",
    task_default_exchange="platform",
    task_default_routing_key="dispatch",
    task_create_missing_queues=False,
    task_default_delivery_mode="persistent",
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_transport_options={"confirm_publish": True},
    broker_connection_retry_on_startup=True,
    task_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,
    task_soft_time_limit=20,
    task_time_limit=30,
)


@app.task(
    name="platform.dispatch",
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 5},
)
def dispatch(command_id):
    # Durable handoff only. No device execution and no interpretation as test PASS.
    with db.engine.begin() as conn:
        db.execute(
            conn,
            "UPDATE commands SET dispatch_ready=true WHERE id=:id AND NOT retired",
            id=command_id,
        )
