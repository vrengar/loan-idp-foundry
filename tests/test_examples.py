import re

import pytest

from app.examples import EXAMPLES_DIR, MODES, ExampleNotFound, get_example, load_manifest, stored_result

ID_PATTERN = re.compile(r"^[a-z0-9-]+$")
MANIFEST = load_manifest()


def test_manifest_ids_are_unique_and_url_safe():
    ids = [item["id"] for item in MANIFEST]
    assert ids
    assert len(ids) == len(set(ids))
    assert all(ID_PATTERN.match(item_id) for item_id in ids)


@pytest.mark.parametrize("example", MANIFEST, ids=lambda item: item["id"])
def test_example_pdf_is_a_pdf(example):
    assert (EXAMPLES_DIR / example["file"]).read_bytes().startswith(b"%PDF-")


@pytest.mark.parametrize("example", MANIFEST, ids=lambda item: item["id"])
def test_example_has_stored_result_for_each_strategy(example):
    modes = [mode for mode in MODES if (EXAMPLES_DIR / f"{example['id']}.{mode}.json").is_file()]
    assert modes, "run scripts/build_examples.py to create the stored results"
    for mode in modes:
        result = stored_result(example, mode)
        assert result["splitStrategy"] == mode
        assert result["segments"]
        assert result["totalPages"] >= 1


def test_unknown_example_id_is_rejected():
    with pytest.raises(ExampleNotFound):
        get_example("../secrets")


def test_unknown_strategy_has_no_stored_result():
    with pytest.raises(ExampleNotFound):
        stored_result(MANIFEST[0], "classifier")
