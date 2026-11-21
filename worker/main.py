import os
import socket
import sys
from multiprocessing import get_context

from redis import Redis
from rq import Queue, Worker

from worker.indexing import dispatch


def main():
    connection = Redis.from_url(os.environ["REDIS_URL"])
    name = socket.gethostname()
    if sys.argv[1:] == ["--check"]:
        sys.exit(
            0 if connection.exists(Worker.redis_worker_namespace_prefix + name) else 1
        )

    queue = Queue("memory", connection=connection, serializer="json")
    dispatcher = get_context("spawn").Process(target=dispatch, daemon=True)
    dispatcher.start()
    try:
        Worker([queue], connection=connection, name=name, serializer="json").work()
    finally:
        dispatcher.terminate()
        dispatcher.join()


if __name__ == "__main__":
    main()
