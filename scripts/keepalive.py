"""Supabase 무료 프로젝트가 일시정지되지 않도록 DB에 가벼운 질의를 보냄.
깃허브 액션(.github/workflows/db-keepalive.yml)이 매일 실행함. DATABASE_URL 은 깃허브 저장소의 Secrets 에서 받음."""
import os
import sys
from datetime import datetime, timezone

import psycopg2

dsn = os.environ.get("DATABASE_URL")
if not dsn:
    print("DATABASE_URL: 없음")
    sys.exit(1)
print("DATABASE_URL: 있음")
with psycopg2.connect(dsn, connect_timeout=20) as conn, conn.cursor() as cur:
    cur.execute("select count(*) from tf_students")
    n = cur.fetchone()[0]
    cur.execute(
        "insert into tf_settings(key, value) values ('last_keepalive', %s) "
        "on conflict (key) do update set value = excluded.value",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"),),
    )
print(f"질의 성공: tf_students {n}행")
