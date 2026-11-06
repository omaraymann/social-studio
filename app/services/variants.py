from dataclasses import dataclass
import re


@dataclass(frozen=True)
class PlatformProfile:
    max_characters: int
    max_hashtags: int
    tone: str
    forbidden_tone_terms: tuple[str, ...]


PROFILES = {
    "instagram": PlatformProfile(max_characters=2200, max_hashtags=5, tone="friendly", forbidden_tone_terms=()),
    "x": PlatformProfile(max_characters=280, max_hashtags=3, tone="concise", forbidden_tone_terms=()),
    "linkedin": PlatformProfile(
        max_characters=3000,
        max_hashtags=5,
        tone="professional",
        forbidden_tone_terms=(" lol ", " omg ", " lmao "),
    ),
    "discord": PlatformProfile(max_characters=2000, max_hashtags=5, tone="conversational", forbidden_tone_terms=()),
}


def validate_variant(platform: str, content: str):
    profile = PROFILES[platform]
    errors = []
    if len(content) > profile.max_characters:
        errors.append(f"content exceeds {profile.max_characters} characters")
    hashtag_count = sum(1 for word in content.split() if word.startswith("#") and len(word) > 1)
    if hashtag_count > profile.max_hashtags:
        errors.append(f"content exceeds {profile.max_hashtags} hashtags")
    if not content.strip():
        errors.append("content cannot be empty")
    padded_content = f" {content.lower()} "
    if any(term in padded_content for term in profile.forbidden_tone_terms):
        errors.append(f"content breaks the {profile.tone} tone rule")
    if errors:
        raise ValueError("; ".join(errors))


def generate_variant(platform: str, title: str, source: str, source_url: str | None) -> str:
    normalized_source = " ".join(source.split())
    sentences = [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", normalized_source) if sentence.strip()]

    if platform == "x":
        prefix = "Quick take: "
        suffix = f"\n{source_url}" if source_url else ""
        suffix += "\n#Insights"
        available = PROFILES["x"].max_characters - len(prefix) - len(suffix)
        insight = sentences[0][:available].rstrip(" ,;:-")
        return f"{prefix}{insight}{suffix}"

    if platform == "discord":
        points = sentences[:2] or [normalized_source]
        summary = " ".join(points)
        link = f"\n\nRead more: {source_url}" if source_url else ""
        return f"**{title}**\n\n{summary}{link}\n\nWhat do you think?"

    if platform == "instagram":
        return f"{title}\n\n{' '.join(sentences[:2])}\n\nLearn more. #ProductUpdate"[:2200]

    points = sentences[:3] or [normalized_source]
    bullet_list = "\n".join(f"- {point}" for point in points)
    link = f"\n\nRead the full article: {source_url}" if source_url else ""
    return (
        f"{title}\n\n"
        f"A closer look at why this topic matters.\n\n"
        f"Key takeaways from the article:\n{bullet_list}\n\n"
        f"Which takeaway stands out to you?"
        f"{link}\n\n#Insights #ProfessionalDevelopment"
    )
