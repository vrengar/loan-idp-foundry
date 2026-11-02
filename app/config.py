"""Configuration loaded from env vars / .env.

All settings come from environment variables (set by Container Apps in prod
or a local `.env` file in dev). Pydantic validates and types them on startup.
Unset values default to empty string so the app can boot and surface a 503
at request time with a helpful message instead of crashing on import.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Azure DI account endpoint, e.g. https://ai-demo-xxxx.cognitiveservices.azure.com/
    di_endpoint: str = ""
    # Optional: only used if managed identity is unavailable. Prod uses MI -> leave blank.
    di_key: str = ""
    # ----- Azure Content Understanding (Foundry Tools) -----
    # If empty, defaults to `di_endpoint` (same Foundry account hosts both DI and CU).
    cu_endpoint: str = ""
    # Optional: only used if managed identity is unavailable. Prod uses MI -> leave blank.
    cu_key: str = ""
    # ID of a custom CU "router" analyzer that classifies + segments + extracts in one
    # call (Option B). When empty, the `cu` strategy falls back to per-segment routing
    # using the existing splitter + ACU prebuilts (Option A).
    cu_router_analyzer_id: str = ""
    # Content Understanding model deployments. CU supports only specific models (see service limits).
    claude_base_url: str = ""
    claude_haiku_deployment: str = "claude-haiku-5-5"
    claude_sonnet_deployment: str = "claude-sonnet-5-5"
    claude_opus_deployment: str = "claude-opus-5-5"
    cu_completion_deployment: str = "gpt-4.1"
    cu_embedding_deployment: str = "text-embedding-3-large"

    # ----- Document library (Cosmos DB NoSQL + Blob Storage, Entra ID auth) -----
    cosmos_endpoint: str = ""
    cosmos_database: str = "idp"
    cosmos_documents_container: str = "documents"
    storage_blob_url: str = ""
    blob_raw_container: str = "documents"
    blob_processed_container: str = "processed"
    # Largest PDF the library API accepts, in MB.
    max_upload_mb: int = 50

    # ----- Azure AI Search + Foundry IQ knowledge base (policy and packet passages) -----
    search_endpoint: str = ""
    search_index_name: str = "loan-documents"
    knowledge_source_name: str = "loan-docs-ks"
    knowledge_base_name: str = "loan-kb"
    # App Insights connection string (auto-injected by Container Apps when enabled).
    applicationinsights_connection_string: str = ""
    # Header name the API reads to identify the calling SaaS tenant for cost allocation.
    tenant_id_header: str = "x-tenant-id"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


# Single shared instance imported throughout the app.
settings = Settings()
