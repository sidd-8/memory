import os
from collections.abc import Iterator

import psycopg
from psycopg.rows import dict_row


def connect() -> psycopg.Connection:
    return psycopg.connect(
        os.environ["DATABASE_URL"], connect_timeout=3, row_factory=dict_row
    )


def request_database() -> Iterator[psycopg.Connection]:
    with connect() as connection:
        yield connection
