# DrawTale API 계약서 (초안 v0.1)

> 작성: Backend · 2026-09-28
> 대상: Frontend, A1(AI Runtime) — **⚠ 표시 항목은 합의가 필요합니다.**
> 실제 동작하는 명세는 Backend 실행 후 `http://127.0.0.1:8000/docs` (Swagger)에서 확인할 수 있습니다.

---

## 0. 전체 흐름

```text
Frontend                         Backend                              AI (A1)
   │ POST /api/v1/characters (그림)   │                                   │
   │ ─────────────────────────────▶ │ 파일 저장 + analyze Job 생성          │
   │ ◀── 202 {character_id, job_id} │ ── POST /internal/v1/analyze ────▶ │
   │                                │ ◀── bbox, mask, 15 joints ──────── │
   │ GET /api/v1/jobs/{job_id} (반복) │                                   │
   │ ◀── status: succeeded          │                                   │
   │ GET /api/v1/characters/{id}    │                                   │
   │ ◀── 이미지, 관절 15개            │                                   │
   │ PATCH .../joints (사용자 보정)    │                                   │
   │ POST /api/v1/stories (4단계 선택) │ story Job 생성                     │
   │ ◀── 202 {story_id, job_id}     │ ── GPT/TTS + POST /internal/v1/render ▶ │
   │ GET /api/v1/jobs/{job_id} (반복) │                                   │
   │ GET /api/v1/stories/{id}       │                                   │
   │ ◀── 텍스트, 음성, 애니메이션 URL   │                                   │
```

- 오래 걸리는 작업(분석, 이야기 생성)은 **즉시 `202` + `job_id`**를 반환하고, Frontend는 `GET /api/v1/jobs/{job_id}`를 **1~2초 간격으로 polling**한다.
- `status`가 `succeeded` 또는 `failed`가 되면 polling을 멈춘다.

---

## 1. 공통 규칙

### 1-1. 관절 15개 (프로젝트 표준) ⚠ A1 확인 필요

Meta AnimatedDrawings skeleton에서 `root`를 뺀 15개. **배열 순서도 아래 순서로 고정**한다.

| # | name | 설명 |
|---|---|---|
| 1 | `hip` | 골반 중심 |
| 2 | `torso` | 몸통 중심 |
| 3 | `neck` | 목 |
| 4 | `right_shoulder` | 오른쪽 어깨 |
| 5 | `right_elbow` | 오른쪽 팔꿈치 |
| 6 | `right_hand` | 오른손 |
| 7 | `left_shoulder` | 왼쪽 어깨 |
| 8 | `left_elbow` | 왼쪽 팔꿈치 |
| 9 | `left_hand` | 왼손 |
| 10 | `right_hip` | 오른쪽 엉덩이 |
| 11 | `right_knee` | 오른쪽 무릎 |
| 12 | `right_foot` | 오른발 |
| 13 | `left_hip` | 왼쪽 엉덩이 |
| 14 | `left_knee` | 왼쪽 무릎 |
| 15 | `left_foot` | 왼발 |

> `right` / `left`는 **그림 속 캐릭터 기준**이다. (화면에서 보면 캐릭터의 오른손은 왼쪽에 있다.)

### 1-2. 좌표 기준 (`coordinate_space: "image_px"`)

- **업로드한 원본 이미지의 픽셀 좌표**. 원점은 왼쪽 위, x는 오른쪽, y는 아래로 증가.
- Frontend는 화면에 맞게 축소해서 그릴 때 `화면좌표 = 원본좌표 × (표시 너비 / image_width)`로 변환하고, **서버에 보낼 때는 다시 원본 좌표로** 보낸다.
- AI도 crop/resize 내부 좌표를 쓰더라도 **응답은 반드시 원본 이미지 좌표로 변환**해서 준다.

```json
{ "name": "neck", "x": 200.0, "y": 165.6 }
```

### 1-3. Job 상태

| status | 의미 | Frontend 처리 |
|---|---|---|
| `pending` | 대기 중 | 로딩 표시, polling 계속 |
| `running` | 처리 중 | 로딩 표시, polling 계속 |
| `succeeded` | 완료 | polling 중단, 결과 조회 |
| `failed` | 실패 | polling 중단, `error.message` 표시 + 재시도 버튼 |

### 1-4. 에러 응답 형식

모든 에러는 같은 형식이다.

```json
{
  "error": {
    "code": "INVALID_IMAGE",
    "message": "PNG 또는 JPG 이미지만 올릴 수 있어요.",
    "detail": null
  }
}
```

- `code`: 프로그램이 분기할 때 쓰는 값 (영문 대문자)
- `message`: **사용자에게 그대로 보여줘도 되는 한국어 문장**
- `detail`: 추가 정보 (없으면 `null`)

| code | HTTP | 언제 |
|---|---|---|
| `VALIDATION_ERROR` | 422 | 요청 형식 오류 (필드 누락, 관절 개수 틀림 등) |
| `INVALID_IMAGE` | 400 | 이미지가 아니거나 PNG/JPG가 아님 |
| `FILE_TOO_LARGE` | 400 | 10MB 초과 |
| `JOINT_OUT_OF_IMAGE` | 400 | 관절 좌표가 이미지 밖 |
| `CHARACTER_NOT_FOUND` | 404 | 없는 캐릭터 |
| `JOB_NOT_FOUND` | 404 | 없는 Job |
| `STORY_NOT_FOUND` | 404 | 없는 이야기 |
| `CHARACTER_NOT_READY` | 409 | 분석이 끝나기 전에 관절 수정/이야기 생성 요청 |
| `AI_TIMEOUT` | Job error | AI 처리 시간 초과 |
| `AI_UNAVAILABLE` | Job error | AI 서버 연결 불가 |
| `AI_ERROR` 등 AI가 준 code | Job error | AI 처리 실패 (A1 정의 code 그대로 전달) |
| `INTERNAL_ERROR` | Job error | 알 수 없는 서버 오류 |

> `Job error`는 HTTP 응답이 아니라 `GET /api/v1/jobs/{id}`의 `error` 필드로 전달된다.

---

## 2. Public API (Frontend → Backend)

Base URL: 로컬 `http://127.0.0.1:8000`, 배포 `https://api.<도메인>`

### 2-1. `POST /api/v1/characters` — 그림 업로드 + 분석 시작

- Content-Type: `multipart/form-data`
- 필드: `image` (PNG 또는 JPG, 최대 10MB)

**응답 `202`**

```json
{
  "character_id": "68510684-d212-4710-a2fd-6dc76665c1d8",
  "job_id": "b2f38c23-d05e-4b97-b89a-1fd8aeb197c1",
  "status": "pending"
}
```

### 2-2. `GET /api/v1/jobs/{job_id}` — 진행 상태 조회 (polling)

**응답 `200`**

```json
{
  "id": "b2f38c23-d05e-4b97-b89a-1fd8aeb197c1",
  "type": "analyze",
  "status": "succeeded",
  "character_id": "68510684-d212-4710-a2fd-6dc76665c1d8",
  "story_id": null,
  "error": null,
  "created_at": "2026-09-28T02:20:11.123Z",
  "updated_at": "2026-09-28T02:20:12.456Z"
}
```

- `type`: `analyze` | `story`
- 실패 시 `"status": "failed"`, `"error": {"code": "AI_TIMEOUT", "message": "..."}`

### 2-3. `GET /api/v1/characters/{character_id}` — 캐릭터 조회

**응답 `200`**

```json
{
  "id": "68510684-d212-4710-a2fd-6dc76665c1d8",
  "status": "succeeded",
  "image_url": "http://127.0.0.1:8000/files/uploads/68510684-....png",
  "image_width": 400,
  "image_height": 600,
  "analysis": {
    "bbox": { "x": 80, "y": 60, "width": 240, "height": 480 },
    "mask_url": null,
    "joints": [ { "name": "hip", "x": 200.0, "y": 324.0 }, "... 15개" ],
    "model_version": "mock-v1",
    "pipeline_version": "mock",
    "coordinate_space": "image_px",
    "processing_time_ms": 0
  },
  "joints": [ { "name": "hip", "x": 200.0, "y": 324.0 }, "... 15개" ],
  "joints_corrected": false,
  "created_at": "...",
  "updated_at": "..."
}
```

- `analysis.joints`: **AI가 준 원래 관절** (변하지 않음)
- `joints`: **현재 사용할 관절** — 사용자가 보정했으면 보정값, 아니면 AI 값
- `joints_corrected`: 사용자가 한 번이라도 보정했는지
- 분석 전(`pending`/`running`)에는 `analysis`, `joints`가 `null`

### 2-4. `PATCH /api/v1/characters/{character_id}/joints` — 관절 보정 저장

**요청** — 15개 전부를 보낸다 (움직이지 않은 관절 포함). 순서는 상관없고 서버가 표준 순서로 정렬한다.

```json
{
  "joints": [
    { "name": "hip", "x": 205.0, "y": 330.0 },
    "... 15개"
  ]
}
```

**응답 `200`**: 2-3과 같은 캐릭터 객체 (`joints_corrected: true`)

- 분석이 끝나기 전이면 `409 CHARACTER_NOT_READY`
- 관절이 이미지 밖이면 `400 JOINT_OUT_OF_IMAGE`

### 2-5. `POST /api/v1/stories` — 이야기 생성 시작

**요청**

```json
{
  "character_id": "68510684-d212-4710-a2fd-6dc76665c1d8",
  "place": "학교",
  "problem": "친구와 다퉜어요",
  "action": "먼저 사과해요",
  "result": "다시 사이좋게 놀아요"
}
```

> ⚠ **Frontend와 합의 필요:** 지금은 각 단계 값을 **자유 문자열(최대 50자)**로 받는다.
> 선택지를 서버가 내려줄지(`GET /api/v1/story-options`), 코드 값(`school`, `fight` 등)으로 주고받을지 정해야 한다.

**응답 `202`**

```json
{ "story_id": "...", "job_id": "...", "status": "pending" }
```

### 2-6. `GET /api/v1/stories/{story_id}` — 이야기 조회

**응답 `200`**

```json
{
  "id": "...",
  "character_id": "...",
  "status": "succeeded",
  "place": "학교",
  "problem": "친구와 다퉜어요",
  "action": "먼저 사과해요",
  "result": "다시 사이좋게 놀아요",
  "text": "오늘 나는 학교에 갔어요. ...",
  "audio_url": null,
  "animation_url": "http://127.0.0.1:8000/files/uploads/....png",
  "created_at": "...",
  "updated_at": "..."
}
```

> 현재 Mock 단계: `text`는 템플릿 문장, `audio_url`은 `null`, `animation_url`은 원본 그림 URL.
> OpenAI/TTS/AI render 연동 후 실제 값으로 바뀐다. **응답 형식은 바뀌지 않는다.**

### 2-7. `GET /health`

```json
{ "status": "ok", "env": "local" }
```

---

## 3. Internal API (Backend → AI, A1 담당)

> **2026-09-28 갱신:** A1이 올린 AI 서버(`drawtale-ai` `runtime/`) 기준으로 analyze 형식을 맞췄다.
> Backend가 AI 응답을 계약 형식으로 변환하므로, 아래 **"현재"** 형식이면 연결된다. **"필요"** 항목은 A1이 추가해야 한다.

- AI 서버는 외부에 공개하지 않는다. Backend만 `AI_SERVICE_URL`(운영: `http://ai:8001`)로 호출한다.
- **파일 전달: 당분간 파일 직접 전송(multipart)**. Azure Blob을 붙일 때 URL 방식으로 바꿀지 다시 정한다.
- 에러: HTTP 200 + `"success": false` + `"message": "CODE: 설명"` 형식을 허용한다. Backend가 `CODE`를 읽어 사용자용 한국어 메시지로 바꾼다. (설명 문구는 사용자에게 보여주지 않는다)

### 3-1. `GET /internal/v1/health`

```json
{ "status": "ok", "model_loaded": true, "model_version": "meta-animated-drawings-pretrained", "pipeline_version": "0.1.0" }
```

- 현재: `status`, `model_loaded`(고정값 `false`), `runtime`
- 필요: `model_loaded`를 실제 TorchServe 상태로, `model_version` · `pipeline_version` 추가

### 3-2. `POST /internal/v1/analyze`

**요청** — `multipart/form-data`, 필드 `file` (그림 파일)

**응답 `200`**

```json
{
  "success": true,
  "bbox": { "left": 80, "top": 60, "right": 320, "bottom": 540 },
  "joints": [ { "name": "hip", "x": 200, "y": 324 }, "... 15개, 1-1 순서" ],
  "mask": { "width": 240, "height": 480 },
  "message": "Analysis complete",
  "model_version": "meta-animated-drawings-pretrained",
  "pipeline_version": "0.1.0",
  "coordinate_space": "image_px",
  "processing_time_ms": 3120
}
```

| 항목 | 현재 | 필요 |
|---|---|---|
| 관절 이름 · 순서 | ✅ 1-1과 같음 | - |
| bbox | ✅ `left/top/right/bottom` (Backend가 `x/y/width/height`로 변환) | - |
| **좌표 기준** | ❌ 최대 1000px로 줄인 이미지 기준 | **원본 이미지 px로 변환** |
| 마스크 | ❌ 크기만 반환, 이미지는 버림 | **마스크 이미지 저장** (render에서 사용) |
| 메타데이터 4개 | ❌ 없음 (Backend가 `unknown`으로 채움) | `model_version`, `pipeline_version`, `coordinate_space`, `processing_time_ms` |

**실패 응답 `200`**

```json
{ "success": false, "message": "NO_CHARACTER_DETECTED: No drawn humanoid detected" }
```

| AI code | Backend Job error code | 사용자 메시지 |
|---|---|---|
| `NO_CHARACTER_DETECTED`, `INVALID_BBOX` | `NO_CHARACTER_DETECTED` | 그림에서 사람 모양 캐릭터를 찾지 못했어요. |
| `INVALID_IMAGE` | `INVALID_IMAGE` | 이미지를 읽을 수 없어요. |
| `MODEL_UNAVAILABLE` | `AI_UNAVAILABLE` | AI 서버가 준비되지 않았어요. |
| 그 외 / code 없음 | `AI_ERROR` | 그림 분석 중 오류가 발생했어요. |

### 3-3. `POST /internal/v1/render` ⬜ AI 미구현 — ⚠ A1과 형식 합의 필요

> 핵심 조건: **analyze가 준 관절이 아니라, Backend가 보낸 관절(사용자 보정 반영)로 렌더링**해야 한다.
> 결과 형식은 **MP4**로 한다. 아래는 초안이며, 파일 전달 방식(직접 전송 vs URL)에 맞춰 바뀔 수 있다.

**요청**

```json
{
  "request_id": "...",
  "image_url": "https://<storage>/uploads/....png?<SAS>",
  "mask_url": "https://<storage>/results/.../mask.png?<SAS>",
  "joints": [ "... 현재 관절 15개 (사용자 보정 반영)" ],
  "motion": "wave_hello",
  "output_upload_url": "https://<storage>/results/.../animation.gif?<SAS>"
}
```

**응답 `200`**

```json
{
  "format": "gif",
  "duration_ms": 3000,
  "output_uploaded": true,
  "model_version": "meta-animated-drawings-pretrained",
  "pipeline_version": "0.1.0",
  "coordinate_space": "image_px",
  "processing_time_ms": 8400
}
```

> ⚠ `motion` 값 목록 (Meta 예시 motion 기준 후보: `wave_hello`, `jumping`, `jumping_jacks`, `dab`, `zombie`)과
> 이야기의 `action` → `motion` 매핑 방식은 A1과 정해야 한다.

---

## 4. 합의 필요 항목 정리

| # | 항목 | 현재 초안 | 누구와 |
|---|---|---|---|
| 1 | 관절 15개 이름·순서 | Meta skeleton − root — ✅ A1 코드와 일치 | A1 |
| 2 | 좌표 기준 | 원본 이미지 px, 왼쪽 위 원점 | A1, Frontend |
| 3 | 이야기 4단계 값 형식 | 자유 문자열 50자 | Frontend |
| 4 | polling 간격 | 1~2초 | Frontend |
| 5 | 파일 전달 방식 | 당분간 파일 직접 전송, Blob 연결 시 재검토 | A1 |
| 6 | motion 목록, action→motion 매핑 | Meta 예시 motion | A1 |
| 7 | 애니메이션 형식 | MP4 (A1 PoC 기준) | A1, Frontend |
| 8 | AI 에러 code 목록 | 3-2 표 (A1 코드 기준) | A1 |
| 9 | 로그인/세션 | 없음 (비회원) | 전원 |
