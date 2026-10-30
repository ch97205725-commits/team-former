"""시험용 아주 작은 PostgreSQL 클라이언트(psycopg2 를 설치할 수 없는 환경에서 service.py 를 시험하려고 만듦).
trust 인증, simple query 프로토콜만 지원함. 앱은 이 파일을 쓰지 않음."""
import datetime as dt
import socket
import struct
from decimal import Decimal


class Error(Exception):
    pass


class IntegrityError(Error):
    pass


def _quote(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float, Decimal)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def _conv(oid, raw):
    if raw is None:
        return None
    s = raw.decode()
    if oid in (20, 21, 23):
        return int(s)
    if oid == 16:
        return s == "t"
    if oid == 1700:
        return Decimal(s)
    if oid == 1184:
        return dt.datetime.fromisoformat(s if len(s.split("+")[-1]) != 2 else s + ":00")
    return s


class Connection:
    def __init__(self, host="127.0.0.1", port=5433, user="postgres", dbname="postgres"):
        self.sock = socket.create_connection((host, port))
        params = b"user\0" + user.encode() + b"\0database\0" + dbname.encode() + b"\0\0"
        body = struct.pack("!i", 196608) + params
        self.sock.sendall(struct.pack("!i", len(body) + 4) + body)
        self._read_until_ready()
        self.closed = 0
        self._in_tx = False

    def _recv(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise Error("connection closed")
            buf += chunk
        return buf

    def _msg(self):
        t = self._recv(1)
        ln = struct.unpack("!i", self._recv(4))[0]
        return t, self._recv(ln - 4)

    def _read_until_ready(self):
        results, err = [], None
        cols, rows, tag = None, [], None
        while True:
            t, body = self._msg()
            if t == b"T":
                n = struct.unpack("!h", body[:2])[0]
                pos, cols = 2, []
                for _ in range(n):
                    end = body.index(b"\0", pos)
                    pos = end + 1
                    oid = struct.unpack("!i", body[pos + 6:pos + 10])[0]
                    cols.append(oid)
                    pos += 18
                rows = []
            elif t == b"D":
                n = struct.unpack("!h", body[:2])[0]
                pos, row = 2, []
                for i in range(n):
                    ln = struct.unpack("!i", body[pos:pos + 4])[0]
                    pos += 4
                    if ln == -1:
                        row.append(None)
                    else:
                        row.append(_conv(cols[i], body[pos:pos + ln]))
                        pos += ln
                rows.append(tuple(row))
            elif t == b"C":
                tag = body[:-1].decode()
                results.append((rows if cols is not None else None, tag))
                cols, rows = None, []
            elif t == b"E":
                fields = {}
                for part in body.split(b"\0"):
                    if part:
                        fields[chr(part[0])] = part[1:].decode()
                err = fields
            elif t == b"Z":
                if err:
                    code = err.get("C", "")
                    cls = IntegrityError if code.startswith("23") else Error
                    raise cls(f"{code} {err.get('M')}")
                return results
            elif t == b"R":
                if struct.unpack("!i", body[:4])[0] != 0:
                    raise Error("only trust auth supported")

    def execute(self, sql):
        q = sql.encode() + b"\0"
        self.sock.sendall(b"Q" + struct.pack("!i", len(q) + 4) + q)
        return self._read_until_ready()

    def cursor(self):
        if not self._in_tx:
            self.execute("BEGIN")
            self._in_tx = True
        return Cursor(self)

    def commit(self):
        if self._in_tx:
            self.execute("COMMIT")
            self._in_tx = False

    def rollback(self):
        if self._in_tx:
            self.execute("ROLLBACK")
            self._in_tx = False


class Cursor:
    def __init__(self, conn):
        self.conn, self._rows, self.rowcount = conn, [], -1

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        if params:
            parts = sql.split("%s")
            assert len(parts) == len(params) + 1, sql
            sql = parts[0] + "".join(_quote(p) + rest for p, rest in zip(params, parts[1:]))
        results = self.conn.execute(sql)
        rows, tag = results[-1] if results else (None, "")
        self._rows = list(rows or [])
        last = tag.split()[-1] if tag else ""
        self.rowcount = int(last) if last.isdigit() else -1

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        r, self._rows = self._rows, []
        return r
