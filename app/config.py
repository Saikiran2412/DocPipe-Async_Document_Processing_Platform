from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    database_url: str
    broker_url: str
    result_backend: str
    s3_endpoint: str
    s3_access_key: str
    s3_secret_key: str
    s3_bucket: str = "uploads"
    redis_url: str
    webhook_secret: str
    webhook_allow_private: bool = False
    groq_api_key: str = ""
    llm_enabled: bool = True
    llm_model: str = "llama-3.1-8b-instant"
    llm_timeout: float = 20.0
    sweeper_enabled: bool = True


settings = Settings()
