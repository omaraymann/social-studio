"""Download a reproducible, small ABO product-photo sample (no API key needed)."""

import csv
import gzip
import hashlib
import json
import random
import subprocess
from datetime import datetime, timezone
from pathlib import Path


BASE_URL = "https://amazon-berkeley-objects.s3.amazonaws.com"
OUTPUT = Path(__file__).resolve().parents[1] / "data/product_images/abo"
CATEGORIES = ("SHOES", "CHAIR", "LAMP", "KITCHEN", "OFFICE_PRODUCTS")
PRODUCTS_PER_CATEGORY = 4
SEED = 42


def download(url: str, destination: Path) -> None:
    """Download atomically, reusing an existing nonempty file."""
    if destination.exists() and destination.stat().st_size:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    subprocess.run(
        ["curl", "-fsSL", "--max-time", "120", "--retry", "2",
         "-o", str(temporary), url],
        check=True,
    )
    temporary.replace(destination)


def english_values(record: dict, field: str) -> list[str]:
    return [entry["value"] for entry in record.get(field, [])
            if entry.get("language_tag") == "en_US"]


def main() -> None:
    listings_file = OUTPUT / "cache/listings_0.json.gz"
    images_file = OUTPUT / "cache/images.csv.gz"
    download(f"{BASE_URL}/listings/metadata/listings_0.json.gz", listings_file)
    download(f"{BASE_URL}/images/metadata/images.csv.gz", images_file)
    download(f"{BASE_URL}/LICENSE-CC-BY-4.0.txt", OUTPUT / "LICENSE-CC-BY-4.0.txt")

    candidates = []
    with gzip.open(listings_file, "rt", encoding="utf-8") as source:
        for line in source:
            record = json.loads(line)
            category = next(iter(record.get("product_type", [])), {}).get("value")
            if (record.get("domain_name") == "amazon.com"
                    and category in CATEGORIES
                    and record.get("main_image_id")
                    and english_values(record, "item_name")):
                candidates.append((category, record))

    wanted_ids = {record["main_image_id"] for _, record in candidates}
    image_metadata = {}
    with gzip.open(images_file, "rt", encoding="utf-8") as source:
        for row in csv.DictReader(source):
            if row["image_id"] in wanted_ids:
                image_metadata[row["image_id"]] = row

    random.Random(SEED).shuffle(candidates)
    selected = []
    seen_images = set()
    for category in CATEGORIES:
        count = 0
        for candidate_category, record in candidates:
            image_id = record["main_image_id"]
            image = image_metadata.get(image_id)
            if (candidate_category != category or not image or image_id in seen_images
                    or min(int(image["height"]), int(image["width"])) < 512):
                continue
            relative_path = Path("images") / category.lower() / (
                record["item_id"] + Path(image["path"]).suffix
            )
            image_url = f"{BASE_URL}/images/original/{image['path']}"
            destination = OUTPUT / relative_path
            download(image_url, destination)
            selected.append({
                "item_id": record["item_id"],
                "category": category,
                "name": english_values(record, "item_name")[0],
                "brand": english_values(record, "brand"),
                "bullet_points": english_values(record, "bullet_point"),
                "product_description": english_values(record, "product_description"),
                "image_id": image_id,
                "image_path": relative_path.as_posix(),
                "image_url": image_url,
                "width": int(image["width"]),
                "height": int(image["height"]),
                "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                "original_listing": record,
            })
            seen_images.add(image_id)
            count += 1
            print(f"{category} {count}/{PRODUCTS_PER_CATEGORY}: {record['item_id']}", flush=True)
            if count == PRODUCTS_PER_CATEGORY:
                break
        if count != PRODUCTS_PER_CATEGORY:
            raise RuntimeError(f"Not enough eligible products in {category}: {count}")

    manifest = {
        "dataset": "Amazon Berkeley Objects (ABO)",
        "source": "https://registry.opendata.aws/amazon-berkeley-objects/",
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "license": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "image_credit": "Amazon.com",
        "seed": SEED,
        "sampling": "4 per category from listings_0; US English; unique main image; both axes >=512px",
        "product_count": len(selected),
        "products": selected,
    }
    (OUTPUT / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Saved {len(selected)} products to {OUTPUT}", flush=True)


if __name__ == "__main__":
    main()
