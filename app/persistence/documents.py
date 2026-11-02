"""Cosmos DB (NoSQL) store for document records. Partition key: tenantId."""
from __future__ import annotations

from typing import Any

from azure.cosmos import CosmosClient
from azure.cosmos.exceptions import CosmosResourceNotFoundError


class CosmosDocumentStore:
    def __init__(self, endpoint: str, database: str, container: str, credential: Any) -> None:
        client = CosmosClient(endpoint, credential=credential)
        self._database = client.get_database_client(database)
        self._container = self._database.get_container_client(container)

    def create(self, record: dict[str, Any]) -> dict[str, Any]:
        return self._container.create_item(body=record)

    def get(self, tenant_id: str, doc_id: str) -> dict[str, Any] | None:
        try:
            return self._container.read_item(item=doc_id, partition_key=tenant_id)
        except CosmosResourceNotFoundError:
            return None

    def list_recent(self, tenant_id: str, limit: int) -> list[dict[str, Any]]:
        query = (
            "SELECT * FROM c WHERE c.type = 'document' "
            "ORDER BY c.createdAt DESC OFFSET 0 LIMIT @limit"
        )
        return list(self._container.query_items(
            query=query,
            parameters=[{"name": "@limit", "value": limit}],
            partition_key=tenant_id,
        ))

    def replace(self, record: dict[str, Any]) -> dict[str, Any]:
        return self._container.replace_item(item=record["id"], body=record)

    def delete(self, tenant_id: str, doc_id: str) -> None:
        try:
            self._container.delete_item(item=doc_id, partition_key=tenant_id)
        except CosmosResourceNotFoundError:
            return

    def ping(self) -> None:
        self._database.read()
