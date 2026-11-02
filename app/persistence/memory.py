"""In-memory stores with the same interface as the Azure adapters. Used by tests."""
from __future__ import annotations

import copy
from typing import Any


class InMemoryDocumentStore:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], dict[str, Any]] = {}

    def create(self, record: dict[str, Any]) -> dict[str, Any]:
        key = (record["tenantId"], record["id"])
        if key in self._items:
            raise ValueError(f"document {record['id']} already exists")
        self._items[key] = copy.deepcopy(record)
        return copy.deepcopy(record)

    def get(self, tenant_id: str, doc_id: str) -> dict[str, Any] | None:
        item = self._items.get((tenant_id, doc_id))
        return copy.deepcopy(item) if item is not None else None

    def list_recent(self, tenant_id: str, limit: int) -> list[dict[str, Any]]:
        items = [v for (t, _), v in self._items.items() if t == tenant_id]
        items.sort(key=lambda d: d["createdAt"], reverse=True)
        return [copy.deepcopy(i) for i in items[:limit]]

    def replace(self, record: dict[str, Any]) -> dict[str, Any]:
        key = (record["tenantId"], record["id"])
        if key not in self._items:
            raise KeyError(record["id"])
        self._items[key] = copy.deepcopy(record)
        return copy.deepcopy(record)

    def delete(self, tenant_id: str, doc_id: str) -> None:
        self._items.pop((tenant_id, doc_id), None)

    def ping(self) -> None:
        return None


class InMemoryBlobStore:
    def __init__(self) -> None:
        self._blobs: dict[tuple[str, str], bytes] = {}

    def put(self, container: str, name: str, data: bytes, content_type: str) -> None:
        self._blobs[(container, name)] = bytes(data)

    def get(self, container: str, name: str) -> bytes:
        try:
            return self._blobs[(container, name)]
        except KeyError as exc:
            raise FileNotFoundError(f"{container}/{name}") from exc

    def delete(self, container: str, name: str) -> None:
        self._blobs.pop((container, name), None)

    def ping(self, container: str) -> None:
        return None
