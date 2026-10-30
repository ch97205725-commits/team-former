"""오픈라우터로 배정안을 받음. 모델마다 '부른 때부터 응답을 다 받을 때까지' 120초를 직접 재서 끊음."""
from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

import rules

API_URL = "https://openrouter.ai/api/v1/chat/completions"
TOTAL_TIMEOUT = 120  # 초. 모델 하나에 주는 전체 대기 시간

DEFAULT_MODELS = [
    "nvidia/nemotron-3-super-120b-a12b:free",
    "openai/gpt-oss-120b:free",
    "z-ai/glm-4.5-air:free",
]

_executor = ThreadPoolExecutor(max_workers=12)


def build_prompt(teams: dict, unassigned: list) -> str:
    lines = ["너는 대학 수업의 팀 편성을 돕는다. 아래 '팀이 없는 학생'을 기존 팀에 배정하라.", "",
             "[규칙]",
             "1. 팀이 없는 학생은 모두, 정확히 한 팀에 배정한다. 이미 팀에 있는 학생은 옮기지 않는다.",
             "2. 배정 뒤 모든 팀은 2~4명이어야 한다(빈 팀이 있으면 안 됨).",
             "3. 한 팀 안에서 주 역할이 같은 학생이 2명 이상이면 안 된다.",
             "4. 모든 팀에는 '기획자'를 맡을 학생과 '웹 프로그래머'를 맡을 학생이 서로 다른 사람으로 1명씩 있어야 한다.",
             "   주 역할이나 보조 역할 가운데 하나로 그 역할을 할 수 있으면 맡을 수 있다. 한 사람이 두 역할을 동시에 맡을 수는 없다.",
             "", "[지금 팀 구성]"]
    for t in sorted(teams):
        ms = teams[t]
        if ms:
            desc = "; ".join(f"{m.name}(주 역할 {m.main_role}, 보조 역할 {', '.join(m.sub_roles) or '없음'})"
                             for m in ms)
        else:
            desc = "(빈 팀)"
        lines.append(f"- 팀{t} ({len(ms)}명): {desc}")
    lines += ["", "[팀이 없는 학생]"]
    for s in unassigned:
        lines.append(f"- 학번 {s.student_no}, {s.name}, 주 역할 {s.main_role}, "
                     f"보조 역할 {', '.join(s.sub_roles) or '없음'}")
    example = ", ".join(f'{{"student_no": "{s.student_no}", "team": 1}}' for s in unassigned[:2])
    lines += ["", "[답 형식]",
              "설명 없이 JSON 하나만 답한다. team 은 팀 번호(정수)다.",
              f'{{"assignments": [{example}, ...]}}']
    return "\n".join(lines)


def _post(model: str, prompt: str, api_key: str, cancel: threading.Event):
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 6000,
        "temperature": 0.2,
        "reasoning": {"effort": "low"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
               "X-Title": "Team Former"}
    resp = requests.post(API_URL, json=body, headers=headers, timeout=(10, TOTAL_TIMEOUT + 10))
    if cancel.is_set():
        return None, "시간 초과 뒤 도착한 응답(버림)"
    if resp.status_code != 200:
        msg = resp.text[:300]
        try:
            msg = resp.json().get("error", {}).get("message", msg)
        except Exception:
            pass
        return None, f"HTTP {resp.status_code}: {msg}"
    data = resp.json()
    if "error" in data:
        return None, f"오류: {data['error'].get('message', data['error'])}"
    try:
        content = data["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError):
        return None, "응답 형식이 이상함(choices 없음)"
    if not content.strip():
        return None, "빈 응답"
    return content, None


def call_model(model: str, prompt: str, api_key: str, on_tick=None):
    """돌려주는 값: (본문 또는 None, 실패 이유 또는 None, 걸린 초).
    요청은 별도 스레드에서 보내고, 이 함수가 1초마다 전체 경과 시간을 재서 120초가 넘으면 끊음."""
    cancel = threading.Event()
    start = time.monotonic()
    fut = _executor.submit(_post, model, prompt, api_key, cancel)
    while True:
        elapsed = time.monotonic() - start
        if fut.done():
            try:
                content, err = fut.result()
            except requests.RequestException as e:
                content, err = None, f"연결 오류: {type(e).__name__}"
            except Exception as e:  # noqa: BLE001
                content, err = None, f"오류: {type(e).__name__}"
            return content, err, time.monotonic() - start
        if elapsed >= TOTAL_TIMEOUT:
            cancel.set()  # 스레드는 남지만 결과는 버림
            return None, f"{TOTAL_TIMEOUT}초 안에 응답하지 않음(시간 초과)", elapsed
        if on_tick:
            on_tick(elapsed)
        time.sleep(1)


def parse_assignments(text: str) -> dict:
    """AI 답에서 {학번: [팀번호,...]} 를 꺼냄. 형식이 틀리면 ValueError."""
    t = re.sub(r"```(?:json)?", "", text)
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j < i:
        raise ValueError("JSON을 찾지 못함")
    obj = json.loads(t[i:j + 1])
    items = obj.get("assignments") if isinstance(obj, dict) else None
    if not isinstance(items, list):
        raise ValueError("assignments 목록이 없음")
    out = {}
    for it in items:
        no = str(it.get("student_no", "")).strip()
        team = it.get("team")
        if isinstance(team, str):
            m = re.search(r"\d+", team)
            team = int(m.group()) if m else None
        if not no or not isinstance(team, int):
            raise ValueError(f"항목 형식이 틀림: {it}")
        out.setdefault(no, []).append(team)
    return out


def list_free_models(timeout=15) -> list:
    """오픈라우터에서 지금 무료로 쓸 수 있는 텍스트 생성 모델 목록(키 필요 없음).
    돌려주는 값: [(모델 이름, 문맥 길이)] — 문맥 길이가 긴 순."""
    resp = requests.get("https://openrouter.ai/api/v1/models", timeout=timeout)
    resp.raise_for_status()
    out = []
    for m in resp.json().get("data", []):
        mid = m.get("id", "")
        arch = m.get("architecture") or {}
        outputs = arch.get("output_modalities") or ["text"]
        if mid.endswith(":free") and mid != "openrouter/free" and "text" in outputs:
            out.append((mid, int(m.get("context_length") or 0)))
    return sorted(out, key=lambda x: -x[1])


def get_models(secret_getter) -> list:
    models = []
    for i, default in enumerate(DEFAULT_MODELS, start=1):
        models.append((secret_getter(f"OPENROUTER_MODEL_{i}") or default).strip())
    return models


def validate(teams, unassigned, proposal):
    return rules.validate_assignment(teams, unassigned, proposal)
