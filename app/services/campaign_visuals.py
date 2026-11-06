"""Generate one visual from an immutable campaign/evidence snapshot."""

import json
from time import perf_counter

from sqlalchemy import select, update

from app.database import SessionLocal
from app.llm.base import LLMError
from app.llm.vision import create_visual_brief, generate_image
from app.models import CampaignImage, CampaignVisual, utcnow
from app.services.image_storage import as_data_url, save_generated
from app.services.visual_profiles import PLATFORM_VISUALS
from app.services.post_media import automatically_attach


def campaign_visual_context(post, run, drafts: dict, *, platform="campaign", style, aspect_ratio, instructions) -> dict:
    from app.config import settings

    return {"prompt_version": "campaign_visual_v2", "title": post.title,
            "source": post.content[:settings.llm_max_input_characters],
            "drafts": {platform: drafts[platform]} if platform in drafts else drafts,
            "platform": platform, "platform_guidance": PLATFORM_VISUALS[platform]["guidance"],
            "retrieved_passages": (run.rag_context or []) if run else [],
            "generation_run_id": run.id if run else None,
            "audience": run.audience if run else "general audience",
            "goal": run.goal if run else "awareness", "tone": run.tone if run else "professional",
            "campaign_call_to_action": run.call_to_action if run else "Learn more",
            "visual_style": style, "aspect_ratio": aspect_ratio, "design_instructions": instructions}


def queue_platform_visuals(db, post, run, payload, drafts: dict, plans: dict, *, retrieval=None) -> None:
    if set(plans) != set(drafts):
        raise ValueError("The model must plan one image for every selected platform")
    for platform, plan in plans.items():
        if not isinstance(plan, dict) or set(plan) != {"headline", "call_to_action", "visual_brief"}:
            raise ValueError(f"Invalid image plan for {platform}")
        for key, limit in (("headline", 100), ("call_to_action", 150), ("visual_brief", 3000)):
            if not isinstance(plan[key], str) or not 1 <= len(plan[key].strip()) <= limit:
                raise ValueError(f"Invalid {key} in the {platform} image plan")
    for platform, plan in plans.items():
        ratio = PLATFORM_VISUALS[platform]["aspect_ratio"]
        context = campaign_visual_context(post, run, drafts, platform=platform, style=payload.image_style,
                                          aspect_ratio=ratio, instructions=payload.image_instructions)
        if retrieval is not None:
            context['image_retrieval'] = retrieval
            context['platform_guidance'] = context['platform_guidance'].replace('product', 'subject')
        db.add(CampaignVisual(
            source_post_id=post.id, platform=platform, vision_model=run.model, image_model=payload.image_model,
            aspect_ratio=ratio, style=payload.image_style, reference_image_ids=payload.reference_image_ids,
            context=context,
            headline=plan["headline"].strip(), call_to_action=plan["call_to_action"].strip(),
            visual_brief=plan["visual_brief"].strip(),
            usage={"planning": {"included_in_generation_run": run.id, "cost": 0}},
        ))


def selected_references(db, post_id: int, ids: list[int]) -> list[CampaignImage]:
    unique = list(dict.fromkeys(ids))
    rows = db.scalars(select(CampaignImage).where(
        CampaignImage.source_post_id == post_id, CampaignImage.id.in_(unique))).all()
    by_id = {row.id: row for row in rows}
    if len(by_id) != len(unique):
        raise ValueError("One or more reference images do not belong to this campaign")
    return [by_id[identifier] for identifier in unique]


async def process_visual(visual_id: int) -> None:
    with SessionLocal() as db:
        claimed = db.execute(update(CampaignVisual).where(
            CampaignVisual.id == visual_id, CampaignVisual.status == "queued"
        ).values(status="processing", step="planning"))
        db.commit()
        if not claimed.rowcount:
            return
        visual = db.get(CampaignVisual, visual_id)
        started = perf_counter()
        try:
            retrieval = visual.context.get('image_retrieval')
            if retrieval and not retrieval['selected'] and retrieval['no_match_policy'] == 'stop':
                raise LLMError('No suitable library photo: ' + retrieval['no_match_reason'])
            references = selected_references(db, visual.source_post_id, visual.reference_image_ids)
            scene_references = bool(retrieval) or any(image.reference_role == 'scene' for image in references)
            visual.context = {**visual.context, 'reference_roles': [image.reference_role for image in references]}
            if scene_references:
                visual.context = {**visual.context, 'platform_guidance': visual.context['platform_guidance'].replace('product','subject')}
            images = [as_data_url(image.prepared_key) for image in references]
            context = json.dumps(visual.context, ensure_ascii=False)
            if visual.visual_brief:
                brief_usage = (visual.usage or {}).get("planning", {})
            else:
                brief, brief_usage = await create_visual_brief(visual.vision_model, context, images,
                    **({'scene_references': True} if scene_references else {}))
                visual.headline = brief["headline"].strip()
                visual.call_to_action = brief["call_to_action"].strip()
                visual.visual_brief = brief["visual_brief"].strip()
                visual.usage = {"planning": brief_usage}
            visual.step = "generating"
            db.commit()
            prompt = (
                f"Create a {visual.platform} campaign visual in {visual.aspect_ratio} aspect ratio. "
                f"Platform design guidance: {visual.context['platform_guidance']} "
                + (("Use these attached photos as scene and composition references only. Their exact product, event "
                    "and venue identities are unverified; do not imply they depict the advertised item or actual event. "
                    if scene_references else "Use these attached photos as product references. ") if images else
                   "Create a topic illustration; do not invent an exact product appearance, screenshot, logo or packaging. ")
                + f"Style: {visual.style}. "
                + ("Keep the referenced product's shape, color, proportions, logos and packaging consistent. "
                   if images and not scene_references else "")
                + "Do not invent labels, product features, prices, or guarantees. "
                "Do not add lettering or watermarks; leave the bottom quarter clear for separately rendered text.\n"
                + visual.visual_brief
            )
            if retrieval:
                reference_positions = {image.id: index + 1 for index, image in enumerate(references)}
                adaptations = [{'reference': reference_positions[item['campaign_image_id']],
                                'changes': item['adaptation_notes']}
                               for item in retrieval['selected']
                               if item.get('campaign_image_id') in reference_positions
                               and item.get('adaptation_notes')]
                if adaptations:
                    prompt += ('\nSuggested reference adaptations for the generated scene. '
                               'Apply only where consistent with the original campaign and explicit user requirements:\n'
                               + json.dumps(adaptations, ensure_ascii=False))
            if visual.context.get('design_instructions'):
                prompt += '\nExplicit user design requirements take priority over adaptation suggestions:\n' + json.dumps(
                    visual.context['design_instructions'], ensure_ascii=False)
            raw, image_usage = await generate_image(visual.image_model, prompt, images, visual.aspect_ratio)
            visual.usage = {"planning": brief_usage, "generation": image_usage}
            visual.step = "saving"
            db.commit()
            visual.image_key, visual.width, visual.height = save_generated(raw)
            visual.status, visual.step = "succeeded", "ready"
            automatically_attach(db, visual)
        except Exception as exc:
            visual.status, visual.step = "failed", "failed"
            visual.error_message = str(exc)[:500] if isinstance(exc, (LLMError, ValueError)) else "Image processing failed. Try a new generation."
        visual.latency_ms = round((perf_counter() - started) * 1000)
        visual.completed_at = utcnow()
        db.commit()
