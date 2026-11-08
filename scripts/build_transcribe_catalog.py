"""Generate the bundled transcribe.cpp model catalog digest.

Fetches ``catalog/*.json`` from the handy-computer/transcribe.cpp repository
and distills the fields the model manager needs into
``src/puripuly_heart/data/transcribe_catalog.json``.

Regenerate after upstream catalog changes:

    .venv\\Scripts\\python.exe scripts\\build_transcribe_catalog.py
"""

from __future__ import annotations

import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

REPO = "handy-computer/transcribe.cpp"
API_URL = f"https://api.github.com/repos/{REPO}/contents/catalog"
RAW_URL = f"https://raw.githubusercontent.com/{REPO}/main/catalog/"
OUT_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "puripuly_heart"
    / "data"
    / "transcribe_catalog.json"
)

CAPABILITY_KEYS = (
    "transcribe",
    "translate",
    "lang_detect",
    "timestamps",
    "streaming",
    "diarize",
    "batching",
)


def fetch_json(url: str) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": "puripuly-heart-catalog"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def digest_accuracy(record: dict) -> dict[str, dict[str, dict]]:
    headline = record.get("headline_benchmark") or {}
    headline_dataset = headline.get("dataset")
    rows: dict[str, dict[str, dict]] = {}
    fallback: dict[str, dict[str, dict]] = {}
    for row in record.get("accuracy_benchmarks") or ():
        language = row.get("language")
        quant = row.get("quant")
        if not language or not quant:
            continue
        metric = row.get("metric")
        cell = {"metric": metric}
        if metric == "accuracy":
            cell["acc_pct"] = row.get("acc_pct")
        else:
            cell["err_pct"] = row.get("err_pct")
        target = rows if row.get("dataset") == headline_dataset else fallback
        target.setdefault(language, {})[quant] = cell
    for language, quants in fallback.items():
        rows.setdefault(language, {}).update(
            {quant: cell for quant, cell in quants.items() if quant not in rows.get(language, {})}
        )
    return rows


def digest_model(record: dict) -> dict | None:
    published_repo = record.get("published_repo")
    if not published_repo:
        return None
    capabilities = [
        key
        for key in CAPABILITY_KEYS
        if (record.get("capabilities") or {}).get(key, {}).get("supported")
    ]
    return {
        "id": record["variant"],
        "family": record.get("family") or "",
        "role": record.get("role") or "asr",
        "name": record.get("display_name") or record["variant"],
        "params": int(record.get("params") or 0),
        "license": (record.get("license") or {}).get("display") or "",
        "languages": list(record.get("languages") or ()),
        "repo": published_repo,
        "capabilities": capabilities,
        "downloads": [
            {
                "quant": item["quant"],
                "filename": item["filename"],
                "size_bytes": int(item["size_bytes"]),
            }
            for item in record.get("downloads") or ()
        ],
        "accuracy": digest_accuracy(record),
    }


def main() -> None:
    listing = fetch_json(API_URL)
    names = sorted(
        item["name"]
        for item in listing
        if item.get("type") == "file" and item["name"].endswith(".json")
        and not item["name"].startswith("_")
    )
    print(f"catalog entries: {len(names)}")

    def load(name: str) -> dict:
        return fetch_json(RAW_URL + name)

    with ThreadPoolExecutor(max_workers=8) as pool:
        records = list(pool.map(load, names))

    models = [digest for digest in (digest_model(record) for record in records) if digest]
    models.sort(key=lambda model: (model["role"], model["id"]))
    payload = {
        "schema": "puripuly-transcribe-catalog-digest-v1",
        "source": f"{REPO}:/catalog",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "models": models,
    }
    OUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {OUT_PATH} ({len(models)} models)")


if __name__ == "__main__":
    main()
