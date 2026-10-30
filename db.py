"""DB 연결. 연결 풀을 한 번만 만들고, 질의할 때마다 풀에서 연결을 빌려 씀(공고 4.2절)."""
from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path

import psycopg2
import psycopg2.pool

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


MAX_CONN = 6


class Pool:
    """연결 풀 + 세마포어. 풀이 다 차면 오류를 내지 않고 빈 연결이 생길 때까지 기다림."""

    def __init__(self, dsn: str):
        # 여러 학생(브라우저)이 동시에 써도 트랜잭션이 섞이지 않도록 연결을 여러 개 둔 풀을 씀
        self._pool = psycopg2.pool.ThreadedConnectionPool(
            1, MAX_CONN, dsn, connect_timeout=10,
            keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3,
        )
        self._sem = threading.BoundedSemaphore(MAX_CONN)

    def getconn(self):
        self._sem.acquire()
        try:
            return self._pool.getconn()
        except Exception:
            self._sem.release()
            raise

    def putconn(self, conn, close=False):
        try:
            self._pool.putconn(conn, close=close)
        finally:
            self._sem.release()


def make_pool(dsn: str):
    return Pool(dsn)


@contextmanager
def _borrow(pool):
    conn = pool.getconn()
    broken = False
    try:
        yield conn
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        broken = True
        raise
    finally:
        pool.putconn(conn, close=broken or bool(conn.closed))


def run(pool, fn, *args, **kwargs):
    """fn(cur, ...) 를 한 트랜잭션으로 실행하고 결과를 돌려줌.
    오래 쉬어서 끊긴 연결이면 버리고 한 번 더 시도함(그 사이 커밋된 것은 없으므로 안전함)."""
    for attempt in range(2):
        committing = False
        try:
            with _borrow(pool) as conn:
                try:
                    with conn.cursor() as cur:
                        out = fn(cur, *args, **kwargs)
                    committing = True
                    conn.commit()
                    return out
                except Exception:
                    if not conn.closed:
                        conn.rollback()
                    raise
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            # 커밋 도중에 끊겼으면 반영됐는지 알 수 없으므로 다시 하지 않음
            if attempt == 1 or committing:
                raise


def ensure_schema(cur):
    cur.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
