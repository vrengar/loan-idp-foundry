"""Pre-compute stored results for the bundled example packets in app/examples/.

Runs the same pipeline as POST /process on each example PDF and writes
app/examples/<id>.<mode>.json. The API serves these files, so "Open example" needs
no Azure calls. Re-run only when an example PDF or the pipeline changes.

Needs DI_ENDPOINT (CU_ENDPOINT defaults to it) and credentials for that account
(az login or another DefaultAzureCredential source).

Usage from the repo root:
    python scripts/build_examples.py                        # all examples, both strategies
    python scripts/build_examples.py --id complete-packet --mode cu
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.cu_router import router_state  # noqa: E402
from app.cu_router import start_background_setup as start_cu_router  # noqa: E402
from app.di_client import DIClient  # noqa: E402
from app.examples import EXAMPLES_DIR, load_manifest  # noqa: E402
from app.main import _run_pipeline  # noqa: E402

BUILD_TENANT = "examples"
CU_ROUTER_TIMEOUT_S = 600


def ensure_cu_router() -> None:
    start_cu_router()
    deadline = time.time() + CU_ROUTER_TIMEOUT_S
    while time.time() < deadline:
        state = router_state()
        if state["status"] == "ready":
            return
        if state["status"] in ("error", "skipped"):
            raise SystemExit(f"CU router is {state['status']}: {state['detail']}")
        time.sleep(3)
    raise SystemExit("Timed out waiting for the CU router analyzer.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Pre-compute stored results for the example packets.")
    parser.add_argument("--id", action="append", dest="ids", default=[],
                        help="Build only this example id (repeatable).")
    parser.add_argument("--mode", choices=("heuristic", "cu", "both"), default="both")
    args = parser.parse_args()

    manifest = load_manifest()
    unknown = sorted(set(args.ids) - {item["id"] for item in manifest})
    if unknown:
        parser.error(f"unknown example id(s): {', '.join(unknown)}")
    examples = [item for item in manifest if not args.ids or item["id"] in args.ids]
    modes = ("heuristic", "cu") if args.mode == "both" else (args.mode,)

    try:
        di = DIClient()
    except RuntimeError as exc:
        raise SystemExit(f"Document Intelligence is not configured: {exc}. Set DI_ENDPOINT and run az login.") from exc
    if "cu" in modes:
        ensure_cu_router()

    for example in examples:
        pdf = (EXAMPLES_DIR / example["file"]).read_bytes()
        for mode in modes:
            result = _run_pipeline(mode, di, pdf, BUILD_TENANT, example["file"])
            out = EXAMPLES_DIR / f"{example['id']}.{mode}.json"
            out.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
            types = [segment.get("doc_type") for segment in result.get("segments", [])]
            cost = (result.get("billing") or {}).get("totalCostEstimateUsd")
            print(f"{example['id']} [{mode}] {len(types)} segments: {', '.join(map(str, types))} | "
                  f"est. ${cost} | wrote {out.name}")


if __name__ == "__main__":
    main()
