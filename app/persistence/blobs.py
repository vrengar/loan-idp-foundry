"""Azure Blob Storage adapter for raw uploads and processed results."""
from __future__ import annotations

from typing import Any

from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobServiceClient, ContentSettings


class AzureBlobStore:
    def __init__(self, account_url: str, credential: Any) -> None:
        self._service = BlobServiceClient(account_url=account_url, credential=credential)

    def put(self, container: str, name: str, data: bytes, content_type: str) -> None:
        self._service.get_blob_client(container=container, blob=name).upload_blob(
            data,
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
        )

    def get(self, container: str, name: str) -> bytes:
        try:
            return self._service.get_blob_client(container=container, blob=name).download_blob().readall()
        except ResourceNotFoundError as exc:
            raise FileNotFoundError(f"{container}/{name}") from exc

    def delete(self, container: str, name: str) -> None:
        try:
            self._service.get_blob_client(container=container, blob=name).delete_blob()
        except ResourceNotFoundError:
            return

    def ping(self, container: str) -> None:
        self._service.get_container_client(container).get_container_properties()
