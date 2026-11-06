# ABO product-photo sample

This is a small input collection for the FlyRank campaign-image prototype.
It contains **20 products / 20 original-resolution catalog images**, with four
products in each category: shoes, chairs, lamps, kitchen items, and office products.
It is a prototype sample, not a representative benchmark or a model-training set.

## Files

- `images/<category>/<item_id>.jpg`: local product photos (excluded from Git).
- `manifest.json`: image paths, source URLs, checksums, product names, and original
  product metadata. Descriptions are historic listing data, not verified current
  availability, prices, or performance claims.
- `LICENSE-CC-BY-4.0.txt`: the dataset's full license.
- `cache/`: downloaded listing and image metadata (excluded from Git).

Each manifest entry connects one product's text to its corresponding image.
Use the product photo as a visual reference and the listing text as the starting
point for a campaign brief. Generated campaign outputs and review labels are not
included yet.

## Download / restore

From the repository root:

```bash
python3 scripts/download_abo_sample.py
```

No Kaggle or AWS account is required. The downloader uses the public ABO S3
bucket, caches metadata, and downloads selected images individually. It does not
download the full image archive. Selection uses a fixed random seed of 42, the
first metadata shard, US English listings, unique main image IDs, and original
images with both dimensions at least 512 pixels. Selection is reproducible while
the upstream files remain unchanged; this is not random sampling of the entire
ABO collection.

## Source and attribution

- Dataset: [Amazon Berkeley Objects](https://registry.opendata.aws/amazon-berkeley-objects/).
- Data, including images: **Amazon.com**.
- Dataset construction: Matthieu Guillaumin (Amazon.com), Thomas Dideriksen
  (Amazon.com), Kenan Deng (Amazon.com), Himanshu Arora (Amazon.com), Jasmine
  Collins (UC Berkeley), and Jitendra Malik (UC Berkeley).
- License: [Creative Commons Attribution 4.0](https://creativecommons.org/licenses/by/4.0/).
- Downloaded photos are unmodified. Credit the source and indicate changes when
  using edited/generated derivatives. This sample does not imply endorsement by
  Amazon or the dataset authors.
