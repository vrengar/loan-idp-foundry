"""Bundled example packets for demos.

Each example is a committed PDF in app/examples/ plus pre-computed pipeline results
(<id>.heuristic.json and <id>.cu.json) written by scripts/build_examples.py. Loading an
example therefore needs no Document Intelligence or Content Understanding calls.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

EXAMPLES_DIR = Path(__file__).resolve().parent / "examples"
MODES = ("heuristic", "cu")


class ExampleNotFound(LookupError):
    """Unknown example id, or no stored result for the requested strategy."""


def load_manifest() -> list[dict[str, Any]]:
    data = json.loads((EXAMPLES_DIR / "manifest.json").read_text(encoding="utf-8"))
    return list(data["examples"])


def get_example(example_id: str) -> dict[str, Any]:
    # Only ids from the committed manifest are accepted, so no request value is ever used as a file path.
    for example in load_manifest():
        if example["id"] == example_id:
            return example
    raise ExampleNotFound(f"Unknown example: {example_id}")


def example_doc_id(example_id: str, mode: str) -> str:
    # Stable library id: loading the same example again returns the same record.
    return f"example-{example_id}-{mode}"


def summarize_example(example: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": example["id"],
        "title": example["title"],
        "fileName": example["file"],
        "description": example["description"],
        "tryIt": example["tryIt"],
        "modes": [mode for mode in MODES if _result_file(example, mode).is_file()],
    }


def pdf_bytes(example: dict[str, Any]) -> bytes:
    return (EXAMPLES_DIR / example["file"]).read_bytes()


def stored_result(example: dict[str, Any], mode: str) -> dict[str, Any]:
    if mode not in MODES or not _result_file(example, mode).is_file():
        raise ExampleNotFound(f"No stored {mode} result for {example['id']}. Use Process live instead.")
    return json.loads(_result_file(example, mode).read_text(encoding="utf-8"))


def _result_file(example: dict[str, Any], mode: str) -> Path:
    return EXAMPLES_DIR / f"{example['id']}.{mode}.json"
