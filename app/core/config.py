from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"

    database_url: str = "postgresql+psycopg://drawtale:drawtale@127.0.0.1:5433/drawtale"

    ai_service_url: str = "http://127.0.0.1:8001"
    ai_use_mock: bool = True
    ai_timeout_seconds: float = 60

    public_base_url: str = "http://127.0.0.1:8000"
    storage_backend: str = "local"
    local_storage_dir: str = "./storage"
    max_upload_mb: int = 10
    # 「원본 그림 보관」을 끈 그림은 프론트가 지우라고 하지 않아도 이 시간이 지나면 지운다
    original_retention_hours: int = 24

    # STORAGE_BACKEND=azure 일 때만 쓴다. 컨테이너는 환경마다 하나 (drawtale-dev · drawtale-prod)
    azure_storage_connection_string: str = ""
    azure_blob_container: str = "drawtale-dev"
    # 파일 주소(SAS)의 유효 시간. 응답마다 새로 발급하므로 짧아도 된다
    azure_sas_hours: int = 2

    openai_api_key: str = ""
    openai_timeout_seconds: float = 30
    # 긴 이야기(length=long)의 쪽 수. 7~11 로 맞춘다 (services/book.py).
    # 쪽 수는 아직 정하지 않았다 (2026-10-12)
    story_pages: int = 8
    # 긴 이야기 대본을 쓰는 모델. 네 문장 판은 story_writer.STORY_MODEL(gpt-4o-mini) 그대로.
    # gpt-4o-mini 는 카드 뜻을 뒤집고(도와줬어요 → 도움을 요청) 쪽 배치를 어겨서 바꿨다
    # (2026-10-12 4편 비교)
    story_book_model: str = "gpt-5.4-mini"
    # 긴 이야기 대본 업체: "anthropic"(기본) | "openai".
    # Claude Opus 5.5 로 바꿨다 (2026-10-27, 30편 비교: 대본 30/30 · 장면 계획 30/30 ·
    # 위험한 아이 말을 상상으로 바꿈. gpt-5.4-mini 는 29/30 · 20/30 —
    # hankan-story/experiments/storybook-script/compare_1027).
    # ANTHROPIC_API_KEY 가 없거나 Claude 호출이 실패하면 위 story_book_model(OpenAI)로,
    # 그것도 안 되면 틀 문장 그림책. 검열(moderation)은 그대로 OpenAI 라서
    # OPENAI_API_KEY 가 필요하다
    story_book_provider: str = "anthropic"
    anthropic_api_key: str = ""
    anthropic_timeout_seconds: float = 180
    story_book_claude_model: str = "claude-opus-5-5"
    # 생각 정도: low | medium | high. medium 이 low 보다 첫 시도 실수가 적었다
    # (10편 시험, 편당 약 40초 · $0.10)
    story_book_effort: str = "medium"
    tts_api_key: str = ""
    tts_timeout_seconds: float = 60
    # 이야기 음성 업체: "openai" (기본) | "elevenlabs". 청취 비교 후 팀이 고른다
    tts_provider: str = "openai"
    elevenlabs_api_key: str = ""
    # ElevenLabs 목소리 id (콘솔 Voices 에서 복사). 비어 있으면 음성 없이 진행한다
    elevenlabs_voice_id: str = ""
    # 한국어를 읽는 모델. 청취 비교로 v4 를 골랐다 (2026-10-08, multilingual_v2 보다 자연스러움).
    # 속도·비용을 줄이려면 flash_v2_5
    elevenlabs_model_id: str = "eleven_v4"
    # 캐릭터 목소리 판의 인물 목소리는 app/services/voices.py 목록에 있다

    # Social login (authorization code flow). Secrets live only here, never in the frontend.
    # A provider with an empty client id answers 503 PROVIDER_NOT_CONFIGURED.
    kakao_client_id: str = ""
    kakao_client_secret: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    auth_token_days: int = 30

    cors_origins: str = "http://localhost:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
