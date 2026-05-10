"""
RQ worker entrypoint.
Run with: python -m app.workers.worker
"""
import redis
from loguru import logger
from rq import Worker, Queue

from app.config import settings


def main() -> None:
    logger.info(f"Connecting to Redis: {settings.redis_url}")
    conn = redis.from_url(settings.redis_url)
    queue = Queue("default", connection=conn)
    worker = Worker([queue], connection=conn)
    logger.info("Worker started — waiting for jobs")
    worker.work()


if __name__ == "__main__":
    main()
