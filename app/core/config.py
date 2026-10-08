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
    tts_api_key: str = ""
    tts_timeout_seconds: float = 60
    # 이야기 음성 업체: "openai" (기본) | "elevenlabs". 청취 비교 후 팀이 고른다
    tts_provider: str = "openai"
    elevenlabs_api_key: str = ""
    # ElevenLabs 목소리 id (콘솔 Voices 에서 복사). 비어 있으면 음성 없이 진행한다
    elevenlabs_voice_id: str = ""
    # 한국어를 읽는 모델. 품질은 multilingual_v2, 속도·비용은 flash_v2_5
    elevenlabs_model_id: str = "eleven_multilingual_v2"

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
