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


def diagnose(dsn: str, err: Exception) -> list:
    """연결 실패의 원인을 짐작하는 안내. 비밀번호는 절대 그대로 내보내지 않음."""
    from urllib.parse import urlsplit

    out = []
    dsn = (dsn or "").strip()
    if not dsn.startswith(("postgresql://", "postgres://")):
        out.append("주소가 postgresql:// 로 시작하지 않음(앞뒤 공백이나 따옴표가 잘못 들어갔는지 확인)")
    if "[" in dsn or "]" in dsn or "YOUR-PASSWORD" in dsn:
        out.append("주소에 [ ] 또는 YOUR-PASSWORD 가 남아 있음 → 대괄호까지 지우고 실제 비밀번호를 넣어야 함")
    password = None
    try:
        u = urlsplit(dsn)
        password = u.password
        host, port, user = u.hostname or "", u.port, u.username or ""
        out.append(f"호스트: `{host}` · 포트: `{port}` · 사용자 이름 형식: "
                   f"`{user.split('.')[0]}{'.(프로젝트ID)' if '.' in user else ''}` · "
                   f"비밀번호: {'있음, ' + str(len(password)) + '글자' if password else '없음'}")
        if host.startswith("db.") and host.endswith("supabase.co"):
            out.append("Direct connection 주소임 → Streamlit에서는 연결이 안 됨. Connect 화면의 **Session pooler** 주소를 쓸 것")
        if "pooler.supabase.com" in host and "." not in user:
            out.append("pooler 주소의 사용자 이름은 `postgres.프로젝트ID` 꼴이어야 함")
    except ValueError:
        out.append("주소를 읽을 수 없음 → 비밀번호에 @ # / ? % : 같은 특수문자가 있으면 문제가 됨"
                   "(Supabase에서 비밀번호를 영문·숫자로만 다시 정하는 것이 가장 쉬움)")
    if password and any(c in password for c in "@#/?%:[] "):
        out.append("비밀번호에 특수문자가 있음 → Supabase에서 영문·숫자로만 된 비밀번호로 바꾸고 주소도 고칠 것")
    import re
    raw_pw = re.search(r"://[^:/@]*:(.*)@[^@]*$", dsn)  # 주소를 못 읽었을 때도 비밀번호 부분을 찾아 가림
    msg = str(err).strip().splitlines()[0] if str(err).strip() else ""
    for secret_part in filter(None, [password, raw_pw.group(1) if raw_pw else None, dsn]):
        msg = msg.replace(secret_part, "***")
    hints = [
        ("password authentication failed", "비밀번호가 틀림 → 주소의 비밀번호를 확인(또는 Supabase에서 재설정)"),
        ("Tenant or user not found", "사용자 이름(postgres.프로젝트ID)이나 리전 주소가 틀림 → Session pooler 주소를 다시 복사"),
        ("could not translate host name", "호스트 이름이 틀림 → 주소를 다시 복사"),
        ("Network is unreachable", "IPv6 전용 주소(Direct connection)임 → Session pooler 주소를 쓸 것"),
        ("Cannot assign requested address", "IPv6 전용 주소(Direct connection)임 → Session pooler 주소를 쓸 것"),
        ("timeout", "응답이 없음 → Supabase 프로젝트가 일시정지되었거나 아직 준비 중인지 확인"),
    ]
    for key, hint in hints:
        if key.lower() in msg.lower():
            out.append("👉 " + hint)
    if msg:
        out.append(f"원래 오류(비밀번호 가림): `{msg[:250]}`")
    return out


def ensure_schema(cur):
    cur.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
