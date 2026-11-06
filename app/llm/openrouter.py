import json

import httpx

from app.llm.base import GenerationSettings, LLMError, LLMResult


class OpenRouterProvider:
    name = "openrouter"

    def __init__(self, api_key: str | None, base_url: str, timeout_seconds: float, transport=None):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def generate(self, *, system_prompt, user_prompt, platforms, settings, images=None,
                       visual_platforms=None, image_search=False) -> LLMResult:
        if not self.api_key:
            raise LLMError("OpenRouter is not configured")
        properties = {platform: {"type": "string", "minLength": 1} for platform in platforms}
        if image_search:
            properties['image_search_query'] = {'type':'string','minLength':1,'maxLength':300}
        if visual_platforms:
            plan_properties = {key: {"type": "string", "minLength": 1, "maxLength": limit}
                               for key, limit in (("headline", 100), ("call_to_action", 150), ("visual_brief", 3000))}
            properties["visuals"] = {"type": "object", "properties": {
                platform: {"type": "object", "properties": plan_properties,
                           "required": list(plan_properties), "additionalProperties": False}
                for platform in visual_platforms
            }, "required": visual_platforms, "additionalProperties": False}
        payload = {
            "model": settings.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": ([{"type": "text", "text": user_prompt}] + [
                    {"type": "image_url", "image_url": {"url": image}} for image in images
                ]) if images else user_prompt},
            ],
            "temperature": settings.temperature,
            "top_p": settings.top_p,
            "max_tokens": settings.max_output_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "social_variants",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": properties,
                        "required": list(properties),
                        "additionalProperties": False,
                    },
                },
            },
        }
        if settings.top_k is not None:
            payload["top_k"] = settings.top_k
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, transport=self.transport) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
            content = body["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            search_query = parsed.pop('image_search_query', None) if image_search else None
            if image_search and (not isinstance(search_query,str) or not search_query.strip()):
                raise ValueError('missing image search query')
            briefs = parsed.pop("visuals", {}) if visual_platforms else {}
            if visual_platforms and (not isinstance(briefs, dict) or set(briefs) != set(visual_platforms)):
                raise ValueError("missing platform visual plans")
            if set(parsed) != set(platforms) or not all(isinstance(value, str) for value in parsed.values()):
                raise ValueError("response does not match requested platforms")
            usage = body.get("usage", {})
            return LLMResult(
                variants=parsed,
                input_tokens=usage.get("prompt_tokens"),
                output_tokens=usage.get("completion_tokens"),
                cost_usd=usage.get("cost"),
                visual_briefs=briefs,
                image_search_query=search_query.strip() if search_query else None,
            )
        except httpx.TimeoutException as exc:
            raise LLMError("LLM provider timed out") from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                message = "LLM provider rate limit reached"
            elif exc.response.status_code in (401, 403):
                message = "LLM provider authentication failed"
            else:
                message = "LLM provider request failed"
            raise LLMError(message) from exc
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LLMError("LLM provider returned invalid structured output") from exc
