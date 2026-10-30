# 팀 프로젝트 팀 편성 앱 (Team Former)

작성자: 최현지

## 1. 배포 주소
https://team-former-3mbxvqp7rqwhshvlgtfreg.streamlit.app/

## 2. PRD와 구현 계획의 위치
- PRD: `docs/PRD.md`
- 구현 계획: `docs/구현 계획.md`
- DB 스키마: `schema.sql`

## 3. 고른 해결 방법
| 기능 | 고른 해결 | 설명 |
|---|---|---|
| 다른 사람의 변경 보기 | 어려운 해결 | `st.fragment(run_every=3)`로 팀 목록과 투표 영역만 3초마다 다시 그림 |
| 필수 포함 역할을 지킬 수 없는 팀 | 어려운 해결 | 지원할 때, 들어간 뒤 남는 자리 수가 아직 못 채운 필수 역할 수보다 적으면 막음 |
| 같은 팀에 대기 중인 지원이 여럿 | 어려운 해결 | 모든 팀원이 찬성한 순간(투표가 끝난 순서) 규칙을 다시 검사하고, 어기면 거절 |
| 마지막 자리에 두 명이 동시에 들어감 | 어려운 해결 | `tf_members`의 `(team_id, slot_no)` 기본 키와 `student_id` 유일성 제약(`schema.sql`) |
| AI 응답이 늦을 때 | 어려운 해결 | 요청을 별도 스레드로 보내고 모델마다 전체 120초를 직접 재서 끊음(`ai.call_model`) |
| 3차 모델까지 실패 | 어려운 해결 | 앱이 모든 경우를 따져(백트래킹) 다섯 가지 검사를 모두 통과하는 배정을 찾음(`rules.solve`) |
| AI 대기 중 화면 | 어려운 해결 | "2차 모델(이름)의 응답을 기다리는 중… 37초 / 120초"처럼 몇 차 모델인지와 경과 시간 표시 |
| DB 일시정지 막기 | 어려운 해결 | 깃허브 액션(`.github/workflows/db-keepalive.yml`)이 매일 09:17(한국 시각) DB에 질의 |

## 4. 쓴 모델 3개
- 1차 `nvidia/nemotron-3-ultra-550b-a55b:free`
- 2차 `google/gemma-4-31b-it:free`
- 3차 `dots-studio/dots-3-note-preview:free`

## 5. 오픈라우터 AI 모델 호출 실패 사례
관리자 화면 맨 아래 "AI 호출 기록"(DB의 `tf_ai_logs` 테이블)에 남은 기록임. 시각은 한국 시각.

- **사례 1: 429 → 다음 모델로 넘어가 성공** (10/31 02:03, 쉬움 시나리오 6단계)
  1차 `google/gemma-4-31b-it:free`가 HTTP 429(Provider returned error)로 실패 → 앱이 자동으로 2차
  `nvidia/nemotron-3-ultra-550b-a55b:free`로 넘어감 → 28.0초 만에 8명 배정안을 받고 검사 통과.
  화면 `img/ai-log-timeout-403.png`(아래에서 4~5번째 줄)
- **사례 2: 120초 시간 초과와 3차까지 실패** (10/31 02:06~02:08)
  1차 HTTP 429 → 2차는 120.1초 동안 응답이 끝나지 않아 앱이 끊음(시간 초과) → 3차
  `thinkingmachines/inkling:free`는 HTTP 403(에이전트 앱에서만 쓸 수 있는 모델) → 앱이 모든 경우를 따져 스스로 배정함.
  화면 `img/ai-log-timeout-403.png`(위의 세 줄)
- **사례 3: 무료 버전이 없는 모델(HTTP 404)** (10/31 01:46, 중간 시나리오 11단계)
  처음 고른 모델 3개가 모두 "This model is unavailable for free"로 실패 → 앱이 스스로 배정해 확정한 팀 구성도 규칙을 지킴.
  화면 `img/ai-log-404.png`, 확정 뒤 팀 `img/middle-step11-teams.png`

## 6. 클로드가 잘못된 추론이나 답변을 한 사례
- **사례 1: 쓸 수 없는 무료 모델을 기본값으로 넣음**
  클로드는 무료 모델로 `nvidia/nemotron-3-super-120b-a12b:free`, `openai/gpt-oss-120b:free`, `z-ai/glm-4.5-air:free`를
  골라 코드의 기본값으로 넣었지만, 세 모델 모두 지금은 무료 버전이 없어 HTTP 404가 남(`img/ai-log-404.png`).
  중간 시나리오 11단계에서 AI 호출 기록을 보고 알아챔. 관리자 화면에 "지금 무료인 모델 목록"을 오픈라우터에서 불러오는 기능을
  넣어(커밋 `561ec09`, 화면 `img/free-models-list.png`) 그 목록에서 다시 고름(커밋 `140d4d5`).
  그런데 이때 클로드가 고른 3차 모델 `thinkingmachines/inkling:free`도 에이전트 앱 전용이라 HTTP 403이 남
  (`img/ai-log-timeout-403.png`) → 커밋 `93f1838`에서 다른 모델로 바꿈.
- **사례 2: 깃허브에 올리면 Streamlit이 다시 배포한다고 잘못 안내함**
  클로드는 "깃허브에 올리면 1~2분 안에 자동으로 다시 배포된다"고 했지만, 커밋 `63623c0`에서 `db.py`에 새 함수를 넣고 올리자
  배포한 앱이 `AttributeError`(db 모듈에 `diagnose`가 없음)를 냄. Streamlit은 `streamlit_app.py`만 다시 읽고
  이미 불러온 `db.py`는 예전 것을 계속 썼기 때문임. 오류 화면을 보고 알아챘고, Manage app → Reboot app으로 해결함.
  이후 코드를 올릴 때마다 재부팅함.

## 7. 발견한 문제점이나 개선 필요 사항
- **AI 배정이 최대 6분 걸리고, 그동안 관리자 화면을 쓸 수 없음**
  쉬움 시나리오 6단계(10/31 02:06)에서 1차가 실패하고 2차가 120초를 다 채운 뒤 끊겨, 배정안이 나오기까지 2분 넘게 기다림
  (`img/ai-log-timeout-403.png`의 "시간 초과 · 120.1초"). 세 모델이 모두 시간 초과면 6분을 기다려야 하고,
  그동안 같은 관리자 창의 다른 단추는 누를 수 없음.
  개선: 규칙을 지키는 배정은 앱이 미리 찾아 두므로(배정 불가 확인용), AI가 늦으면 "앱이 찾은 배정안 바로 쓰기" 단추를 함께 보여 주면 좋겠음.
- **무료 모델은 같은 날에도 결과가 들쭉날쭉함**
  같은 1차 모델이 02:03과 02:06에 모두 429였고, 같은 2차 모델이 02:03에는 28초 만에 성공했지만 02:06에는 120초 시간 초과였음.
  하루 호출 한도(50회)도 있어 시험을 많이 하면 채점 때 쓸 횟수가 모자랄 수 있음.

## 부록: 실행 방법
```bash
pip install -r requirements.txt
cp .env.example .env      # 값 채우기(이름은 정확히 .env)
streamlit run streamlit_app.py
```
시험(로컬 PostgreSQL 필요): `python tests/run_scenarios.py`, `python tests/test_extra.py`
