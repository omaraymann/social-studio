# Image detection dataset suitability review

Date: 20 October 2026 (Africa/Cairo).

## Scope and method

This is a qualitative dataset suitability review, not a model evaluation or a statistical domain-similarity measurement. No VLM was run. No files were uploaded to Drive and no campaign code was changed.

Reference data: visually inspected all 20 local Amazon Berkeley Objects (ABO) images listed in `data/product_images/abo/manifest.json`. There are four product listings in each of SHOES, CHAIR, LAMP, KITCHEN and OFFICE_PRODUCTS. Listing categories describe catalogue entries; they are not object detection ground truth.

Target workflow: user uploads an image with multiple products, reviews detected objects and boxes, and selects a product crop as a campaign-generation reference. We do not yet have a representative collection of real user uploads for this workflow. Findings distinguish the existing catalogue sample from this intended use.

Candidate inspection:

- Open Images V7: downloaded official boxable class names and validation bounding-box annotations for selection, then eight image previews. For each of Lamp, Chair, Footwear and Kitchen utensil, selected two validation images containing that class and at least two non-group annotation rows, shuffling candidate IDs with seed 42. This simple selection is intentionally audited below: multiple annotation rows do not establish multiple distinct products because of hierarchy, duplicate coverage or non-product classes.
- RPC: visually inspected the authors' published checkout example montage across easy/medium/hard clutter levels. These are illustrative examples, not a random dataset sample.
- SKU-110K: visually inspected the authors' published shelf-detection teaser. This figure includes model predictions; its displayed boxes are not used as ground truth in this review.

Temporary preview images, the exact selection script and Open Images preview manifest are under `/tmp/social-studio-dataset-comparison/`. This directory is temporary; the report records the essential provenance independently below. The full image datasets were not downloaded.

## Existing library findings

- Most references use clean, pale/white catalogue backgrounds, with products large in the frame.
- Several contain multiple physical objects sold together: two chairs, two peelers, cookware, stacked trays and a group of albums. The cookware also overlaps.
- A pot with a separate lid illustrates a product-versus-component ambiguity. Before evaluation we must decide whether to annotate an assembled product, visible physical components, or both.
- One OFFICE_PRODUCTS reference (B086LJXDVS) visually appears to contain only an AmazonBasics logo. It is useful as a no-product/branding example and should not be assumed to depict a physical office product merely because of its listing category.
- Existing metadata does not provide our needed per-object detection boxes, verified colour labels or approved crop labels.

## Comparison

| Property | Existing 20 ABO references | Open Images previews | RPC published examples | SKU-110K published example |
| --- | --- | --- | --- | --- |
| Categories | Shoes, chairs, lamps, kitchen and office products | Relevant household categories; category verification confirms Lamp, Chair, Footwear, Kitchen utensil and other useful classes exist | Packaged groceries/drinks in inspected examples | Packaged shelf goods in inspected figure |
| Composition | Mostly large products on clean backgrounds; some bundles | Room scenes, people outdoors, close-ups and mixed backgrounds | Products laid out on a checkout surface, viewed from above | Dense rows of shelf stock |
| Multiple-product relevance | Some bundles; no detection labels | Mixed; a class-only filter is insufficient | Strong for clutter/overlap; weaker for our category range and lifestyle photos | Strong extreme-density stress case; weak match for selecting a few campaign products |
| Object visibility | Often large and mostly visible; some overlap | Some products small, worn, dark or occluded | Rotations and overlap increase with clutter level | Many small, adjacent products |
| Evaluation coverage | Requires custom boxes | Public class/box annotations, with hierarchy and special-case evaluation rules | Public SKU categories and localization annotations | Intended for item localization rather than our broad semantic product labels |
| Suitability judgment | Useful application examples, insufficient labelled benchmark | Best candidate of these for a filtered household/product detection subset | Secondary option if grocery tabletop uploads become a target | Exclude from the first benchmark |

These judgments apply to inspected examples and documented dataset design. They are not measured percentages or proof that a selected dataset matches future user uploads.

## Open Images sample audit

| Category filter | Validation image ID | Visual finding |
| --- | --- | --- |
| Lamp | aaca2745929bc274 | Large table lamp in a dark setting; annotated with a table as well. Closer product scale, different lighting/background. |
| Lamp | 924d4695abfca81a | Several hanging lamps; useful multi-instance example, different lamp style from our catalogue sample. |
| Chair | 46d613d6127cfa59 | Cluttered interior with a person and many object/hierarchy annotations; not a clean product bundle. |
| Chair | 601f47f5b112ecf9 | Several chairs in a furnished commercial interior; useful for scene-level detection. |
| Footwear | 1e22e1fbfa73a3ca | People climbing outdoors; shoes small in context. Weak match to product-reference uploads. |
| Footwear | ec3dc2f4c8662d5e | Person wearing footwear outdoors; weak match to isolated product/bundle photos. |
| Kitchen utensil | bfb906dcdef46ad3 | Close-up pan; multiple labels can describe one object (e.g. pan, utensil, tableware). Not evidence of multiple products. |
| Kitchen utensil | 10fb73ee47a33a83 | Coffee maker on a stove; contextual product/component scene rather than a catalogue bundle. |

For dataset filtering, count distinct spatial object instances after resolving label hierarchy; do not use annotation-row count as the number of products. Match our evaluation categories explicitly and handle groups/occlusion/truncation according to dataset rules.

## Decision and next selection criteria

Use a filtered Open Images subset for general localization testing. Do not select arbitrary images just because a desired class occurs.

Proposed selection rules:

1. Prioritize approved product categories and explicitly map synonymous labels.
2. Require a useful number of distinct target objects (initial target: 2–6), with exceptions retained as negative/single-object checks.
3. Include both clear large products and deliberately harder small/overlapping cases, recorded as separate strata.
4. Avoid letting people-wearing-footwear images dominate a product-reference benchmark; include them only if that is a supported upload type.
5. Separate catalogue/bundle, furnished-room/lifestyle and promotional-collage inputs. The inspected public sets do not establish promotional-collage coverage.
6. Reserve disjoint development/test image IDs before prompt tuning.
7. Add a smaller manually labelled set of actual intended campaign uploads, including our bundle and logo-only cases. Report this separately from public benchmark performance.

No public candidate inspected here removes the need for the application-specific check. Detection boxes also do not establish exact product/SKU identity, correct colour attributes, crop suitability or fidelity of generated products.

## Sources

- Local reference sample and attribution: `data/product_images/abo/README.md` and `manifest.json`.
- Open Images download and format reference: https://storage.googleapis.com/openimages/web/download_v7.html
- Official class list: https://storage.googleapis.com/openimages/v7/oidv7-class-descriptions-boxable.csv
- Official validation boxes: https://storage.googleapis.com/openimages/v5/validation-annotations-bbox.csv
- Open Images preview source URL pattern: `https://open-images-dataset.s3.amazonaws.com/validation/<image_id>.jpg`.
- RPC project, examples, statistics and licensing: https://rpc-dataset.github.io/
- RPC checkout example montage: https://rpc-dataset.github.io/imgs/test.png
- SKU-110K authors' repository: https://github.com/eg4000/SKU110K_CVPR19
- SKU-110K authors' teaser: https://raw.githubusercontent.com/eg4000/SKU110K_CVPR19/master/figures/teaser_width.jpg

RPC's project lists CC BY-NC-SA 4.0 and academic-use conditions. Confirm dataset/image terms and attribution before redistributing examples or using them in a public demonstration. Preview inspection does not constitute a redistribution clearance.
