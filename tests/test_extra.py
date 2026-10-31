"""시나리오 밖의 경우를 시험함: 배정안 검사, 배정 불가 안내, AI 시간 초과·빈 응답, CSV 읽기, DB 유일성 제약.
실행: python tests/test_extra.py"""
import http.server
import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import ai  # noqa: E402
import minipg  # noqa: E402
import rules  # noqa: E402
import service  # noqa: E402
from rules import Student as S  # noqa: E402

FAIL = []


def ok(label, cond, extra=""):
    print(f"  {'OK ' if cond else 'XX '} {label} {extra}")
    if not cond:
        FAIL.append(label)


print("== 규칙 함수")
pl_wp = S(1, "1", "A", "기획자", ("웹 프로그래머",))
ok("기획자+웹 보조 한 명만 있으면 필수 역할 1개만 채운 것", rules.unfilled_required([pl_wp]) == 1)
tt, pt, rw = S(2, "2", "B", "테스터"), S(3, "3", "C", "발표자"), S(4, "4", "D", "보고서 작성자")
ok("테스터+발표자 팀에 보고서 작성자 지원 → 막힘", rules.join_block_reason([tt, pt], rw) is not None)
ok("빈 팀 지원은 막히지 않음", rules.join_block_reason([], rw) is None)

print("== 배정안 검사(AI가 틀린 답을 낸 경우)")
teams = {1: [S(10, "10", "P", "기획자"), S(11, "11", "W", "웹 프로그래머")], 2: []}
un = [S(20, "20", "P2", "기획자"), S(21, "21", "W2", "웹 프로그래머"), S(22, "22", "T", "테스터")]
ok("빠진 학생을 잡아냄", any("배정되지 않은" in p for p in rules.validate_assignment(teams, un, {"20": [2], "21": [2]})))
ok("두 팀 배정을 잡아냄", any("여러 팀" in p for p in rules.validate_assignment(teams, un, {"20": [2], "21": [2, 1], "22": [2]})))
ok("필수 역할 없음을 잡아냄", any("없음" in p for p in rules.validate_assignment(teams, un, {"20": [1], "21": [2], "22": [2]})))
ok("주 역할 중복을 잡아냄", any("중복" in p for p in rules.validate_assignment(teams, un, {"20": [1], "21": [2], "22": [2]})))
ok("올바른 배정은 통과", rules.validate_assignment(teams, un, {"20": [2], "21": [2], "22": [1]}) == [])

print("== 규칙을 지키는 배정이 없음")
full = {1: [S(1, "1", "a", "테스터"), S(2, "2", "b", "발표자"), S(3, "3", "c", "보고서 작성자"),
            S(4, "4", "d", "프론트엔드 디자이너")], 2: []}
pool = [S(5, "5", "e", "기획자"), S(6, "6", "f", "웹 프로그래머")]
ok("배정 없음", rules.solve(full, pool) is None)
r = rules.infeasible_reasons(full, pool)
ok("이유에 팀1이 들어감", any("팀1" in x for x in r), str(r))
teams3 = {1: [S(1, "1", "a", "테스터"), S(2, "2", "b", "발표자")], 2: [S(3, "3", "c", "기획자"), S(4, "4", "d", "웹 프로그래머")]}
r = rules.infeasible_reasons(teams3, [S(5, "5", "e", "보고서 작성자")])
ok("웹 프로그래머를 맡을 학생이 없음 안내", any("팀1에 웹 프로그래머" in x for x in r), str(r))

print("== AI 답 읽기")
ok("코드 블록 안 JSON", ai.parse_assignments('```json\n{"assignments":[{"student_no":"20","team":"팀2"}]}\n```') == {"20": [2]})
try:
    ai.parse_assignments("배정할 수 없습니다")
    ok("JSON 없음은 ValueError", False)
except ValueError:
    ok("JSON 없음은 ValueError", True)

print("== AI 호출: 전체 대기 시간 제한(천천히 응답하는 가짜 서버)")


class Slow(http.server.BaseHTTPRequestHandler):
    mode = "slow"

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        if Slow.mode == "slow":  # 응답은 바로 시작하지만 본문을 1초에 한 글자씩 보냄
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                for _ in range(20):
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    time.sleep(1)
            except Exception:
                pass
            return
        body = {"choices": [{"message": {"content": ""}}]} if Slow.mode == "empty" else {"error": {"message": "rate limited"}}
        code = 200 if Slow.mode == "empty" else 429
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 18765), Slow)
threading.Thread(target=srv.serve_forever, daemon=True).start()
ai.API_URL = "http://127.0.0.1:18765/"
ai.TOTAL_TIMEOUT = 4
t0 = time.monotonic()
content, err, el = ai.call_model("m", "p", "k")
ok("응답이 천천히 와도 전체 4초에서 끊음", content is None and "시간 초과" in err and el < 6, f"({err}, {el:.1f}초)")
Slow.mode = "empty"
content, err, el = ai.call_model("m", "p", "k")
ok("빈 응답은 실패", content is None and err.startswith("빈 응답"), err)
Slow.mode = "429"
content, err, el = ai.call_model("m", "p", "k")
ok("HTTP 429는 실패", content is None and "429" in err, err)
srv.shutdown()

print("== CSV 읽기")
rows = service.parse_roster("﻿student_no,name,main_role,sub_roles\n1,가,기획자,\n2,나,테스터,기획자;발표자\n".encode())
ok("BOM + 빈 보조 역할 + 보조 2개", rows == [("1", "가", "기획자", []), ("2", "나", "테스터", ["기획자", "발표자"])])
try:
    service.parse_roster("student_no,name,main_role,sub_roles\n1,가,기획자\n".encode())
    ok("열 3개 CSV는 거절", False)
except ValueError as e:
    ok("열 3개 CSV는 거절", True, str(e))

print("== DB 유일성 제약(마지막 자리에 두 명)")
conn = minipg.Connection()
c = conn.cursor()
c.execute("delete from tf_members where team_id = 1 and slot_no = 4")
c.execute("select student_id from tf_members where team_id = 1 and slot_no = 1")
sid = c.fetchone()[0]
conn.commit()
c = conn.cursor()
try:
    c.execute("insert into tf_members(team_id, slot_no, student_id) values (1, 1, %s)", (sid + 1000,))
    ok("같은 자리에 두 번째 학생은 DB가 막음", False)
except minipg.Error as e:
    ok("같은 자리에 두 번째 학생은 DB가 막음", True, str(e)[:60])
conn.rollback()
c = conn.cursor()
try:
    c.execute("insert into tf_members(team_id, slot_no, student_id) values (1, 5, %s)", (sid,))
    ok("다섯 번째 자리는 DB가 막음", False)
except minipg.Error as e:
    ok("다섯 번째 자리는 DB가 막음", True, str(e)[:60])
conn.rollback()

print("\n결과:", "모두 통과" if not FAIL else f"실패 {len(FAIL)}건: {FAIL}")
sys.exit(1 if FAIL else 0)
