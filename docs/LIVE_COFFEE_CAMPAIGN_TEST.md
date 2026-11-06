# Live coffee-and-tea campaign check — 25 October 2026

Completed one real LinkedIn draft/image generation using the app API and the
existing owner's 20-photo library. Campaign ID 9, generation run 9, visual attempt
10. The post remains a draft with its image attached; no publishing was requested.

## Inputs and behavior

- Title: Make room for a coffee-and-tea break.
- Source: general inspiration for a home coffee-and-tea corner; drinkware, cups,
  teapots, or kettles may serve as references. No brand, specific product, venue,
  price, or health benefit was advertised.
- RAG disabled to isolate image retrieval/selection. One platform: LinkedIn.
- Query: `home coffee tea corner with drinkware and kettle, cozy, calm composition`.
- Retrieved candidates: 16, 17, 15, 7, 14.
- Selector prompt: `library-select-plan-v4`; model `google/gemini-2.5-flash`.
- Selected 16 (moka coffee maker and patterned mug) and 17 (teapot and tea cup).
- Adaptation notes requested improved lighting and simpler backgrounds. Notes were
  retained in the visual audit and forwarded to generation with reference positions.
- Image model: `google/gemini-2.5-flash-image`; square output with the headline and
  CTA rendered by the existing post-media layer.
- The generated image was successfully attached to the LinkedIn draft.

## Measured timing and reported provider cost

| Stage | Measurement |
|---|---:|
| Campaign drafts plus retrieval/selection | 8.25 seconds |
| Retrieval/selection stage | 5.364 seconds |
| Image visual job | 18.672 seconds |
| End-to-end API run, including polling | 28.44 seconds |
| Campaign text and selection combined | $0.0022737 |
| Selection alone, included above | $0.0014888 |
| Image generation | $0.038961 |
| Total reported cost | $0.0412347 |

These are one-run observations, not latency percentiles or a complete quality
benchmark. Cost values are reported by the provider, not an estimated exchange rate.

## Visual inspection and limitations

Both selected reference images are relevant to drinkware and coffee/tea preparation.
The output primarily resembles reference 16, with a cleaner/brighter background,
a recognisable moka maker and patterned mug, and legible application-rendered text.
Both selected photos were not required to appear as separate objects; this does not
establish how much each reference influenced the generated output.

The coffee maker's visible branding was carried from the source reference into the
output despite the campaign being generic. This is a review issue for scene-reference
mode: future prompts should explicitly remove incidental brand marks for generic
campaigns, while preserving verified branding for the manual product-reference path.
No automatic brand-removal fix or additional paid regeneration was performed here.

This verifies the live handoff and output for one different topic. It does not
establish general selector accuracy, exact product fidelity, or publishing readiness.

Raw audit and final image (Git-ignored):

- `data/image_retrieval/results/live_coffee_campaign.json`
- `data/image_retrieval/results/live_coffee_post.png`
- `data/image_retrieval/results/live_coffee_references.jpg`
