from dataclasses import dataclass, field
from typing import Protocol


class LLMError(RuntimeError):
    """A safe, provider-independent generation failure."""


@dataclass(frozen=True)
class GenerationSettings:
    model: str
    temperature: float
    top_p: float
    top_k: int | None
    max_output_tokens: int


@dataclass(frozen=True)
class LLMResult:
    variants: dict[str, str]
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    visual_briefs: dict[str, dict[str, str]] = field(default_factory=dict)
    image_search_query: str | None = None


class LLMProvider(Protocol):
    name: str

    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        platforms: list[str],
        settings: GenerationSettings,
        images: list[str] | None = None,
        visual_platforms: list[str] | None = None,
        image_search: bool = False,
    ) -> LLMResult: ...
