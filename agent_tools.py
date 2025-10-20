from typing import List
from langchain_core.tools import tool
from pathlib import Path
import json

# Simple, safe tools to demonstrate agentic calls

@tool("check_domain", return_direct=False)
def check_domain(domain: str) -> str:
    """Check domain WHOIS info to infer availability (best-effort)."""
    try:
        import whois  # python-whois
        info = whois.whois(domain)
        # If creation_date exists, domain likely taken
        taken = bool(getattr(info, "creation_date", None))
        if taken:
            return f"Domain '{domain}' appears to be registered."
        return f"Domain '{domain}' may be available (no creation date)."
    except Exception as e:
        return f"Could not check domain '{domain}': {e}"


@tool("calculate_profit", return_direct=False)
def calculate_profit(cost: float, price: float, units: int) -> str:
    """Calculate profit = (price - cost) * units."""
    try:
        profit = (float(price) - float(cost)) * int(units)
        return f"Estimated profit: {profit:.2f}"
    except Exception as e:
        return f"Error calculating profit: {e}"


@tool("suggest_palette", return_direct=False)
def suggest_palette(theme: str) -> str:
    """Suggest a simple HEX color palette based on a theme keyword."""
    t = (theme or "").lower()
    if "wellness" in t or "calm" in t:
        return "#3AAFA9, #DEF2F1, #17252A, #FEFFFF, #2B7A78"
    if "tech" in t or "modern" in t:
        return "#0F172A, #1E293B, #334155, #10A37F, #A3E635"
    if "fashion" in t or "luxury" in t:
        return "#0A0A0A, #111827, #4B5563, #D1D5DB, #F5F5F5"
    return "#1F2937, #374151, #6B7280, #D1D5DB, #F3F4F6"

@tool("analyze_instagram", return_direct=False)
def analyze_instagram(username: str) -> str:
    """Load cached social media analysis for an Instagram username if available and summarize key points."""
    try:
        if not username:
            return "Please provide an Instagram username (e.g., @influencer)."
        uname = username.lstrip("@").strip()
        base = Path("wizard_designer") / "cache" / "social_media_analysis"
        if not base.exists():
            return f"No saved analysis found for {uname}. Try running social media analysis first."
        # Find the most recent analysis file for this username
        files = sorted(base.glob(f"{uname}_*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
        if not files:
            return f"No saved analysis found for {uname}."
        with files[0].open("r", encoding="utf-8") as f:
            data = json.load(f)
        analysis = data.get("analysis") or data
        # Extract a few highlights if present
        arche = (analysis.get("inferred_archetype") or {}).get("name") if isinstance(analysis, dict) else None
        tone = (analysis.get("brand_design_guidance") or {}).get("sentiment") if isinstance(analysis, dict) else None
        palette = (analysis.get("brand_design_guidance") or {}).get("color_palette_hex") if isinstance(analysis, dict) else None
        hints = []
        if arche:
            hints.append(f"Archetype: {arche}")
        if tone:
            hints.append(f"Design sentiment: {tone}")
        if palette:
            hints.append(f"Palette: {palette}")
        if hints:
            return "; ".join(hints)
        return f"Loaded analysis for {uname}, but no highlights available."
    except Exception as e:
        return f"Error reading analysis for {username}: {e}"

@tool("logo_ideas", return_direct=False)
def logo_ideas(brand: str) -> str:
    """Suggest three logo concepts for a brand with style and icon guidance."""
    b = (brand or "").strip() or "your brand"
    return (
        f"Logo concepts for {b}:\n"
        "1) Minimal monogram: stylized initials with geometric sans-serif type.\n"
        "2) Icon + wordmark: simple line icon (abstract shape) paired with clean lettering.\n"
        "3) Badge emblem: rounded shield with modern outline and compact typography."
    )

@tool("recommend_products", return_direct=False)
def recommend_products(context: str) -> str:
    """Recommend 5 product ideas aligned to a brand or audience context."""
    t = (context or "").lower()
    if "fitness" in t or "gym" in t:
        return "Whey isolate, creatine, electrolytes, BCAAs, shaker bundle"
    if "beauty" in t or "wellness" in t:
        return "Collagen, multivitamin, adaptogen blend, greens powder, glow gummies"
    if "tech" in t or "productivity" in t:
        return "Nootropic capsules, focus tea, omega-3, magnesium, sleep support"
    return "Multivitamin, protein, greens, immunity booster, hydration mix"

def get_default_tools() -> List:
    """Return default tools for the agent to use."""
    return [check_domain, calculate_profit, suggest_palette, analyze_instagram, logo_ideas, recommend_products]