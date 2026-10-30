from celery import Celery
from kombu import Exchange, Queue

from app.config import settings
from celery.signals import worker_init


celery_app = Celery(
    "docpipe",
    broker=settings.broker_url,
    backend=settings.result_backend,
    include=["app.worker.tasks"],
)

celery_app.conf.update(
    task_acks_late=True,                 # ack AFTER the task finishes
    worker_prefetch_multiplier=1,        # take one message at a time
    broker_connection_retry_on_startup=True,
    task_default_queue="jobs",
    task_default_exchange="jobs",
    task_default_routing_key="jobs",
    task_queues=(
        Queue(
            "jobs", Exchange("jobs"), routing_key="jobs",
            queue_arguments={
                "x-dead-letter-exchange": "jobs.dlx",
                "x-dead-letter-routing-key": "jobs.dead",
            },
        ),
        Queue("jobs.dead", Exchange("jobs.dlx"), routing_key="jobs.dead"),
    ),
)



@worker_init.connect
def declare_queues(**_):
    """Declare every queue and exchange up front, including the dead-letter ones
    the worker doesn't consume from (-Q jobs would otherwise skip them)."""
    with celery_app.connection_for_write() as conn:
        channel = conn.default_channel
        for queue in celery_app.conf.task_queues:
            queue(channel).declare()      # creates exchange, queue and binding