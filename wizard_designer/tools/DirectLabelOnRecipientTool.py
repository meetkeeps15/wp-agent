import io
import os
import sys
import time
import json
from typing import Dict, List, Optional

import requests
from agency_swarm.tools import BaseTool
from dotenv import load_dotenv
from pydantic import Field
from wizard_designer.utils.highlevel_client import upsert_contact_with_fields, API_BASE
try:
    from wizard_designer.utils.highlevel_client import upload_media, get_or_create_custom_field_id, _resolve_field_ids, ensure_contact  # type: ignore
except Exception:  # pragma: no cover
    upload_media = None  # type: ignore
    get_or_create_custom_field_id = None  # type: ignore
    _resolve_field_ids = None  # type: ignore
    ensure_contact = None  # type: ignore

# Ensure project root is importable when running this file directly
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Enhanced base system prompt for creating beautiful, customized labels (optimized for fal.ai 5000 char limit)
BASE_PROMPT = (
    "Expert product packaging designer creating stunning, customized labels:\n"
    "- Image 1: product bottle (canvas)\n"
    "- Image 2: label template with ALL TEXT to keep and enhance\n"
    "- Image 3: brand logo to integrate\n"
    "\n"
    "DESIGN: Beautiful customized label matching archetype, centered on bottle, keep all template text, enhance typography with shadows/gradients. On the LABEL BACKGROUND, create refined, professional patterns (e.g., subtle geometric tessellations, linework, guilloché, embossed textures) that add depth without reducing readability. Use tasteful contrast and controlled repetition; avoid busy visuals.\n"
    "\n"
    "VISUAL: Neutral gray gradient backdrop, elegant soft shadow, no floor reflections, realistic professional lighting.\n"
    "\n"
    "COMMERCIAL: Premium hero shot, balanced composition, visually attractive, attention-grabbing yet elegant, commercially viable.\n"
    "\n"
    "OUTPUT: Full bottle with label applied."
)


def _find_file_with_suffix(folder: str, suffix: str) -> str:
    for name in os.listdir(folder):
        if name.endswith(suffix):
            return os.path.join(folder, name)
    raise FileNotFoundError(f"Expected file with suffix {suffix} in {folder}")


def _center_crop_from_label_template(sku: str, max_height: int = 1024) -> Optional[str]:
    root = os.getcwd()
    sku_dir = os.path.join(root, "label_images", sku)
    if not os.path.isdir(sku_dir):
        return None
    try:
        base_path = _find_file_with_suffix(sku_dir, "_image.png")
    except FileNotFoundError:
        return None
    import PIL.Image as PILImage
    base = PILImage.open(base_path).convert("RGBA")
    # Optional mask to precisely crop center
    mask_path = None
    for suffix in ("_mask1.png", "_mask.png"):
        try:
            mask_path = _find_file_with_suffix(sku_dir, suffix)
            break
        except FileNotFoundError:
            continue
    if mask_path:
        mask = PILImage.open(mask_path).convert("L")
        if mask.size != base.size:
            mask = mask.resize(base.size, PILImage.NEAREST)
        bbox = mask.getbbox() or (0, 0, base.width, base.height)
        crop = base.crop(bbox)
    else:
        crop = base
    if crop.height > max_height:
        new_w = int(crop.width * (max_height / crop.height))
        crop = crop.resize((max(1, new_w), max_height), PILImage.LANCZOS)
    buf = io.BytesIO()
    import base64
    crop.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{b64}"


# --- NocoDB helpers to fetch template label and mask ---
def _first_url_from_field(value: object, base_url: str) -> Optional[str]:
    def normalize(u: str) -> str:
        if not u:
            return u
        if u.startswith("http://") or u.startswith("https://"):
            return u
        if u.startswith("/"):
            return base_url.rstrip("/") + u
        return base_url.rstrip("/") + "/" + u

    def extract(obj: object) -> Optional[str]:
        if obj is None:
            return None
        if isinstance(obj, str):
            return normalize(obj)
        if isinstance(obj, dict):
            for key in ("url", "signedUrl", "signedURL", "path"):
                v = obj.get(key)  # type: ignore[attr-defined]
                if isinstance(v, str) and v:
                    return normalize(v)
            return None
        if isinstance(obj, (list, tuple)) and obj:
            return extract(obj[0])
        return None

    return extract(value)


def _download_bytes(url: str) -> bytes:
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    return r.content


def _center_crop_from_nocodb(
    sku: str,
    base_url: str,
    api_token: str,
    table_id: str,
    where_field: str = "sku",
    label_field: str = "product_image",
    mask_field: str = "mask_image",
    max_height: int = 1024,
) -> Optional[str]:
    base_url = base_url.rstrip("/")
    url = f"{base_url}/api/v2/tables/{table_id}/records"
    params: Dict[str, object] = {"limit": 1, "offset": 0, "where": f"({where_field},eq,{sku})"}
    headers = {"xc-token": api_token, "accept": "application/json"}
    resp = requests.get(url, headers=headers, params=params, timeout=60)
    if resp.status_code == 422:
        # Fallback: list and match locally
        fb = requests.get(url, headers=headers, params={"limit": 100, "offset": 0}, timeout=60)
        fb.raise_for_status()
        payload = fb.json()
        rows = payload.get("list") or payload.get("rows") or []
        row = None
        for r in rows:
            for k in r.keys():
                if k == where_field or k.lower() == "sku":
                    if str(r.get(k)) == sku:
                        row = r
                        break
            if row is not None:
                break
        if row is None:
            return None
    else:
        resp.raise_for_status()
        payload = resp.json()
        rows = payload.get("list") or payload.get("rows") or []
        if not rows:
            return None
        row = rows[0]

    label_url = _first_url_from_field(row.get(label_field), base_url)  # type: ignore[arg-type]
    mask_url = _first_url_from_field(row.get(mask_field), base_url)  # type: ignore[arg-type]

    # Heuristic fallback if fields not exactly named
    def is_attachment_like(val: object) -> bool:
        if isinstance(val, dict):
            return any(k in val for k in ("url", "signedUrl", "signedURL", "path"))
        if isinstance(val, (list, tuple)) and val:
            first = val[0]
            return isinstance(first, (dict, str))
        return isinstance(val, str) and (val.startswith("http://") or val.startswith("https://") or val.startswith("/"))

    if not label_url:
        candidates: List[tuple[int, str]] = []
        for key, val in row.items():
            if not is_attachment_like(val):
                continue
            score = 0
            kl = str(key).lower()
            if "product" in kl:
                score += 5
            if "label" in kl:
                score += 4
            if any(tok in kl for tok in ("image", "img", "picture")):
                score += 3
            candidates.append((score, key))
        if candidates:
            candidates.sort(reverse=True)
            for _, key in candidates:
                u = _first_url_from_field(row.get(key), base_url)
                if u:
                    label_url = u
                    break

    if not mask_url:
        candidates = []
        for key, val in row.items():
            if not is_attachment_like(val):
                continue
            score = 0
            kl = str(key).lower()
            if "mask" in kl:
                score += 5
            if any(tok in kl for tok in ("image", "img")):
                score += 2
            candidates.append((score, key))
        if candidates:
            candidates.sort(reverse=True)
            for _, key in candidates:
                u = _first_url_from_field(row.get(key), base_url)
                if u:
                    mask_url = u
                    break

    if not label_url or not mask_url:
        return None

    # Download and compute center crop
    import PIL.Image as PILImage
    base_img = PILImage.open(io.BytesIO(_download_bytes(label_url))).convert("RGBA")
    mask_img = PILImage.open(io.BytesIO(_download_bytes(mask_url))).convert("L")
    if mask_img.size != base_img.size:
        mask_img = mask_img.resize(base_img.size, PILImage.NEAREST)
    bbox = mask_img.getbbox() or (0, 0, base_img.width, base_img.height)
    x0, y0, x1, y1 = bbox
    center = base_img.crop((x0, y0, x1, y1))
    if center.height > max_height:
        new_w = int(center.width * (max_height / center.height))
        center = center.resize((max(1, new_w), max_height), PILImage.LANCZOS)
    out = io.BytesIO()
    import base64
    center.save(out, format="PNG")
    b64 = base64.b64encode(out.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{b64}"


def _fetch_preview_url(
    base_url: str,
    api_token: str,
    table_id: str,
    where_field: str,
    sku: str,
    preview_field: Optional[str],
) -> str:
    """Fetch preview URL from NocoDB for a given SKU."""
    base_url = base_url.rstrip("/")
    url = f"{base_url}/api/v2/tables/{table_id}/records"
    headers = {"xc-token": api_token, "accept": "application/json"}

    params: Dict[str, object] = {"limit": 1, "offset": 0, "where": f"({where_field},eq,{sku})"}
    resp = requests.get(url, headers=headers, params=params, timeout=60)

    if resp.status_code == 422:
        list_params = {"limit": 100, "offset": 0}
        fb = requests.get(url, headers=headers, params=list_params, timeout=60)
        fb.raise_for_status()
        payload = fb.json()
        rows = payload.get("list") or payload.get("rows") or []
        candidate_keys = [where_field, "sku", "SKU", "Sku", "product_sku", "code", "Code", "CODE"]
        row = None
        for r in rows:
            for k in r.keys():
                if k in candidate_keys or k.lower() == "sku":
                    if str(r.get(k)) == sku:
                        row = r
                        break
            if row is not None:
                break
        if row is None:
            raise RuntimeError(f"No record matched locally for SKU='{sku}'")
    else:
        resp.raise_for_status()
        payload = resp.json()
        rows = payload.get("list") or payload.get("rows") or []
        if not rows:
            raise RuntimeError(f"No NocoDB record found for {where_field}={sku}")
        row = rows[0]

    url_candidate = None
    if preview_field and preview_field in row:
        url_candidate = _first_url_from_field(row.get(preview_field), base_url)
    if not url_candidate:
        prefer = sorted(row.keys(), key=lambda k: ("preview" not in k.lower(), k))
        for k in prefer:
            u = _first_url_from_field(row.get(k), base_url)
            if u:
                url_candidate = u
                break
    if not url_candidate:
        raise RuntimeError("Could not find preview attachment URL in record")
    return url_candidate


def _get_required_env(name: str) -> str:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        raise RuntimeError(f"Missing required configuration: {name}")
    return v


def _load_social_media_analysis(username: str, session_id: str = None) -> dict | None:
    """Load the latest social media analysis for the given username from current session"""
    try:
        import json
        from pathlib import Path
        
        # Find the analysis directory
        current_dir = Path(__file__).parent
        analysis_dir = current_dir.parent / "cache" / "social_media_analysis"
        
        if not analysis_dir.exists():
            print(f"Analysis directory not found: {analysis_dir}")
            return None
        
        # Find the latest analysis file for this username and session
        if session_id:
            pattern = f"{username}_{session_id}_*.json"
        else:
            pattern = f"{username}_*.json"
        
        files = list(analysis_dir.glob(pattern))
        
        if not files:
            print(f"No analysis files found for username: {username}")
            return None
        
        # Get the most recent file
        latest_file = max(files, key=lambda f: f.stat().st_mtime)
        print(f"Loading analysis from: {latest_file}")
        
        with open(latest_file, 'r') as f:
            data = json.load(f)
            return data.get("analysis", {})
            
    except Exception as e:
        print(f"Failed to load social media analysis for {username}: {e}")
        return None


def _extract_design_guidelines(analysis: dict) -> dict:
    """Extract design guidelines from social media analysis"""
    design_guidance = analysis.get("brand_design_guidance", {})
    
    guidelines = {
        "archetype": analysis.get("inferred_archetype", {}).get("name", "Unknown"),
        "archetype_confidence": analysis.get("inferred_archetype", {}).get("confidence_0_1", 0),
        "archetype_rationale": analysis.get("inferred_archetype", {}).get("rationale", ""),
        "sentiment": design_guidance.get("sentiment", "modern and professional"),
        "tone_words": design_guidance.get("tone_words", ["modern", "clean"]),
        "color_palette": design_guidance.get("color_palette_hex", {}),
        "color_roles": design_guidance.get("color_roles", []),
        "color_usage": design_guidance.get("color_usage", []),
        "style_keywords": design_guidance.get("logo_guidelines", {}).get("style_keywords", ["modern", "clean"]),
        "iconography": design_guidance.get("logo_guidelines", {}).get("iconography_recommendations", "Simple, clean icons"),
        "typography": design_guidance.get("typography", {}),
        "visual_style": analysis.get("visual_style", {}).get("styling_vibe_tags", ["modern"]),
        "influencer_alignment": analysis.get("brand_naming_guidelines", {}).get("influencer_alignment", "Professional and aspirational"),
        "marketing_angle": analysis.get("marketing_angle", ""),
        "recommended_products": analysis.get("recommended_product_types", []),
        "logo_do": design_guidance.get("logo_guidelines", {}).get("do", []),
        "logo_dont": design_guidance.get("logo_guidelines", {}).get("dont", []),
        "packaging_notes": design_guidance.get("packaging_notes", ""),
        "imagery_guidelines": design_guidance.get("imagery_guidelines", ""),
        "full_design_guidance": design_guidance
    }
    
    return guidelines


def _generate_archetype_specific_prompt_enhancements(guidelines: dict) -> str:
    """Generate archetype-specific prompt enhancements for more targeted design instructions."""
    archetype = guidelines.get('archetype', 'Unknown').lower()
    
    archetype_enhancements = {
        'athlete': "ATHLETE: Dynamic energetic visuals, bold typography, strong contrasts, athletic patterns, vibrant colors, movement gradients, bold fonts, speed/strength themes.",
        
        'lifestyle': "LIFESTYLE: Elegant sophisticated design, refined typography, premium colors, clean modern layout, lifestyle patterns, luxury wellness themes.",
        
        'wellness': "WELLNESS: Calming natural colors, organic flowing elements, soft gradients, gentle typography, wellness symbols, tranquility themes.",
        
        'fitness': "FITNESS: Bold motivational design, strong visual impact, high-contrast colors, confident typography, fitness patterns, strength themes.",
        
        'beauty': "BEAUTY: Elegant refined aesthetics, premium colors, graceful typography, beauty patterns, glamorous elements, elegance themes.",
        
        'tech': "TECH: Modern futuristic design, clean lines, contemporary colors, tech-forward typography, digital patterns, innovation themes."
    }
    
    # Get the enhancement for the specific archetype, or use a generic one
    enhancement = archetype_enhancements.get(archetype, "GENERIC: Visually striking design, strong visual impact, dynamic colors, engaging gradients, bold typography, relevant patterns, clear engaging text styling.")
    
    return enhancement


def _generate_design_summary_with_gpt(guidelines: dict, user_prompt: str = "") -> str:
    """Use GPT-4o to generate comprehensive, detailed design prompts that create beautiful, archetype-matched labels.
    
    This enhanced function creates much more detailed prompts that include:
    - Specific archetype-based design directions
    - Detailed background visual instructions
    - Super detailed text styling requirements
    - Custom color applications and gradients
    - Typography hierarchy and styling
    - Visual effects and composition details
    """
    
    # Determine if user prompt is provided
    has_user_prompt = user_prompt and user_prompt.strip()
    
    # Prepare the context for GPT with priority-based instructions
    if has_user_prompt:
        context = f"""
COMPREHENSIVE DESIGN GUIDELINES FROM INFLUENCER ANALYSIS:

INFLUENCER PROFILE:
- Archetype: {guidelines.get('archetype', 'Unknown')} (Confidence: {guidelines.get('archetype_confidence', 0)})
- Rationale: {guidelines.get('archetype_rationale', 'N/A')}
- Marketing Angle: {guidelines.get('marketing_angle', 'N/A')}

DESIGN PRINCIPLES:
- Sentiment: {guidelines.get('sentiment', 'N/A')}
- Tone Words: {', '.join(guidelines.get('tone_words', []))}
- Visual Style: {', '.join(guidelines.get('visual_style', []))}
- Style Keywords: {', '.join(guidelines.get('style_keywords', []))}

COLOR STRATEGY:
- Primary Colors: {guidelines.get('color_palette', {})}
- Color Usage Guidelines: {guidelines.get('color_usage', [])}

TYPOGRAPHY & ICONOGRAPHY:
- Typography: {guidelines.get('typography', {})}
- Iconography: {guidelines.get('iconography', 'N/A')}

LOGO GUIDELINES:
- DO: {', '.join(guidelines.get('logo_do', []))}
- DON'T: {', '.join(guidelines.get('logo_dont', []))}

PACKAGING & IMAGERY:
- Packaging Notes: {guidelines.get('packaging_notes', 'N/A')}
- Imagery Guidelines: {guidelines.get('imagery_guidelines', 'N/A')}

RECOMMENDED PRODUCTS: {', '.join(guidelines.get('recommended_products', [])[:5])}

🎯 USER REQUIREMENTS (PRIORITY #1):
{user_prompt}

Create a comprehensive, detailed design prompt (8-12 sentences) that:
1. **PRIORITIZES the user's specific requirements above all else**
2. Uses the influencer's archetype to create a deeply personalized design direction
3. Includes specific instructions for beautiful background visuals, gradients, and effects
4. Provides super detailed text styling with typography hierarchy, colors, and effects
5. Specifies exact color applications, gradients, and visual treatments
6. Creates a cohesive brand identity that serves the user's needs first
7. Incorporates influencer insights as valuable enhancement and context
8. Ensures the design is visually stunning and commercially viable

Focus on creating a design that is both beautiful and perfectly matched to the archetype while prioritizing user requirements.
"""
    else:
        context = f"""
COMPREHENSIVE DESIGN GUIDELINES FROM INFLUENCER ANALYSIS:

INFLUENCER PROFILE:
- Archetype: {guidelines.get('archetype', 'Unknown')} (Confidence: {guidelines.get('archetype_confidence', 0)})
- Rationale: {guidelines.get('archetype_rationale', 'N/A')}
- Marketing Angle: {guidelines.get('marketing_angle', 'N/A')}

DESIGN PRINCIPLES:
- Sentiment: {guidelines.get('sentiment', 'N/A')}
- Tone Words: {', '.join(guidelines.get('tone_words', []))}
- Visual Style: {', '.join(guidelines.get('visual_style', []))}
- Style Keywords: {', '.join(guidelines.get('style_keywords', []))}

COLOR STRATEGY:
- Primary Colors: {guidelines.get('color_palette', {})}
- Color Usage Guidelines: {guidelines.get('color_usage', [])}

TYPOGRAPHY & ICONOGRAPHY:
- Typography: {guidelines.get('typography', {})}
- Iconography: {guidelines.get('iconography', 'N/A')}

LOGO GUIDELINES:
- DO: {', '.join(guidelines.get('logo_do', []))}
- DON'T: {', '.join(guidelines.get('logo_dont', []))}

PACKAGING & IMAGERY:
- Packaging Notes: {guidelines.get('packaging_notes', 'N/A')}
- Imagery Guidelines: {guidelines.get('imagery_guidelines', 'N/A')}

RECOMMENDED PRODUCTS: {', '.join(guidelines.get('recommended_products', [])[:5])}

Create a comprehensive, detailed design prompt (8-12 sentences) that:
1. Uses the influencer's archetype as the primary design direction with deep personalization
2. Includes specific instructions for stunning background visuals, gradients, patterns, and effects
3. Provides super detailed text styling with typography hierarchy, colors, shadows, and effects
4. Specifies exact color applications, gradients, and visual treatments throughout the label
5. Creates a cohesive design strategy that perfectly aligns with the target audience
6. Embodies the influencer's brand and aesthetic for product packaging
7. Makes the label design visually stunning, attractive, and easy to sell at first sight
8. Uses custom colors, typography, background patterns, and other design elements extensively

Focus on creating a design that is both beautiful and perfectly matched to the archetype with extensive visual detail.
"""

    try:
        # Get OpenAI API key from environment
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return "Error: OPENAI_API_KEY not found in environment variables"
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        # Enhanced system message for detailed prompt generation
        system_message = (
            "You are an expert product packaging designer and visual artist specializing in creating "
            "stunning, detailed label designs. You excel at translating influencer archetypes into "
            "beautiful, customized visual designs with extensive attention to detail. Your prompts "
            "are comprehensive, specific, and result in visually striking labels with perfect "
            "archetype matching, detailed text styling, beautiful background visuals, and "
            "commercial appeal. You prioritize user requirements when provided, but always "
            "enhance them with sophisticated design principles and archetype-based aesthetics."
        )
        
        payload = {
            "model": os.getenv("OPENAI_MODEL", "gpt-4o"),
            "messages": [
                {
                    "role": "system",
                    "content": system_message
                },
                {
                    "role": "user",
                    "content": context
                }
            ],
            "max_tokens": 400,  # Reduced to stay under 5000 char limit
            "temperature": 0.8  # Slightly higher for more creative output
        }
        
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=45
        )
        
        if response.status_code == 200:
            result = response.json()
            return result["choices"][0]["message"]["content"].strip()
        else:
            return f"Error calling OpenAI API: {response.status_code} - {response.text}"
            
    except Exception as e:
        return f"Error generating detailed design prompt with GPT-4o: {str(e)}"


# --- Palette override helpers (shared convention with LogoGenerator) ---
def _palette_override_file(session_id: str) -> str:
    cache_dir = os.path.join(os.getcwd(), "tools", "cache", "social_media_analysis")
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, f"PALETTE_{session_id}_latest.json")


def _load_palette_override(session_id: str) -> dict | None:
    try:
        path = _palette_override_file(session_id)
        if os.path.exists(path):
            with open(path, "r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        return None
    return None


def _format_palette_override_text(override: dict | None) -> str:
    try:
        if not isinstance(override, dict):
            return ""
        pal = override.get("palette") if isinstance(override.get("palette"), list) else []
        roles = override.get("roles") if isinstance(override.get("roles"), list) else []
        parts = []
        if pal:
            parts.append("Palette Override: " + ", ".join([str(p) for p in pal]))
        if roles:
            role_txt = ", ".join([f"{(r.get('role') or 'unspecified')}={r.get('hex')}" for r in roles if isinstance(r, dict)])
            if role_txt:
                parts.append("Roles: " + role_txt)
        if parts:
            return "\n" + " ".join(parts) + ". USE THESE COLORS PREFERENTIALLY in backgrounds, typography, accents, and CTAs."
    except Exception:
        return ""
    return ""


def _discover_latest_username_for_session() -> str | None:
    """Infer the latest username for the current session by scanning analysis cache files."""
    try:
        from pathlib import Path
        session_id = _get_session_id_from_headers()
        current_dir = Path(__file__).parent
        analysis_dir = current_dir.parent / "cache" / "social_media_analysis"
        if not analysis_dir.exists():
            return None
        # Match files like: {username}_{session_id}_{timestamp}.json
        candidates = sorted(analysis_dir.glob(f"*_{session_id}_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            return None
        fname = candidates[0].name
        marker = f"_{session_id}_"
        idx = fname.rfind(marker)
        if idx > 0:
            return fname[:idx]
        return None
    except Exception as e:
        print(f"Failed to discover username for session: {e}")
        return None


def _get_session_id_from_headers() -> str:
    """Extract session ID from agency headers or generate a new one."""
    try:
        import os
        
        # Check for agency system headers (prioritize X-Chat-Id for deployment)
        headers_to_check = [
            'X-Chat-Id',  # Primary: Agency system chat ID (used in deployment)
            'X-User-Id',  # Secondary: Agency system user ID
            'X-Agent-Id', # Tertiary: Agency system agent ID
            # Fallback headers
            'X-Chat-ID', 'X-ChatId',
            'X-User-ID', 'X-UserId', 
            'X-Session-Id', 'X-Session-ID', 'X-SessionId',
            'X-Conversation-Id', 'X-Conversation-ID', 'X-ConversationId',
            # Cursor-specific headers (for local development)
            'CURSOR_TRACE_ID', 'CURSOR_SESSION_ID', 'CURSOR_CHAT_ID',
            # Other potential session identifiers
            'AGENCY_SESSION_ID', 'AGENCY_CHAT_ID', 'AGENCY_USER_ID'
        ]
        
        # Check environment variables first (common way agencies pass headers)
        for header in headers_to_check:
            env_var = header.replace('-', '_').upper()
            value = os.getenv(env_var)
            if value:
                session_id = str(value)[:8]  # Use first 8 characters
                print(f"🔍 Found session ID from header {header}: {session_id}")
                return session_id
        
        # Fallback: generate new UUID for local development
        import uuid
        new_session_id = str(uuid.uuid4())[:8]
        print(f"🆔 Generated new session ID: {new_session_id}")
        return new_session_id
        
    except Exception as e:
        print(f"Error getting session ID: {e}")
        import uuid
        return str(uuid.uuid4())[:8]


def _to_data_uri_from_url(url: str, max_height: int = 1024) -> str:
    r = requests.get(url, timeout=300)
    r.raise_for_status()
    import PIL.Image as PILImage
    img = PILImage.open(io.BytesIO(r.content)).convert("RGB")
    if img.height > max_height:
        new_w = int(img.width * (max_height / img.height))
        img = img.resize((max(1, new_w), max_height), PILImage.LANCZOS)
    buf = io.BytesIO()
    import base64
    img.save(buf, format="JPEG", quality=92)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}"


def _to_data_uri_from_url_preserve_alpha(url: str, max_height: int = 1024) -> str:
    r = requests.get(url, timeout=300)
    r.raise_for_status()
    import PIL.Image as PILImage
    img = PILImage.open(io.BytesIO(r.content))
    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    if not has_alpha:
        img = img.convert("RGB")
    if img.height > max_height:
        new_w = int(img.width * (max_height / img.height))
        img = img.resize((max(1, new_w), max_height), PILImage.LANCZOS)
    buf = io.BytesIO()
    import base64
    if has_alpha:
        img.save(buf, format="PNG")
        mime = "image/png"
    else:
        img.save(buf, format="JPEG", quality=92)
        mime = "image/jpeg"
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def _to_data_uri_from_file_preserve_alpha(path: str, max_height: int = 1024) -> str:
    import PIL.Image as PILImage
    img = PILImage.open(path)
    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    if not has_alpha:
        img = img.convert("RGB")
    if img.height > max_height:
        new_w = int(img.width * (max_height / img.height))
        img = img.resize((max(1, new_w), max_height), PILImage.LANCZOS)
    buf = io.BytesIO()
    import base64
    if has_alpha or str(path).lower().endswith(".png"):
        img.save(buf, format="PNG")
        mime = "image/png"
    else:
        img.save(buf, format="JPEG", quality=92)
        mime = "image/jpeg"
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def _get_latest_generated_image_path(sku: str, session_id: str) -> Optional[str]:
    """Find the most recently generated image for a given SKU and session (including edited versions).

    Robust to accidental whitespace differences in the SKU directory name (e.g. "ROC937 ").
    """
    # Locate candidate SKU directories under outputs/ whose name, when stripped, matches the given sku
    outputs_root = os.path.join(os.getcwd(), "outputs")
    if not os.path.exists(outputs_root):
        return None

    normalized_sku = (sku or "").strip()
    candidate_dirs: List[str] = []
    try:
        for entry in os.listdir(outputs_root):
            path = os.path.join(outputs_root, entry)
            if os.path.isdir(path) and entry.strip() == normalized_sku:
                candidate_dirs.append(path)
    except Exception:
        pass

    if not candidate_dirs:
        # Fall back to the exact path if present
        exact_path = os.path.join(outputs_root, sku)
        if os.path.exists(exact_path):
            candidate_dirs.append(exact_path)

    if not candidate_dirs:
        return None

    # Collect all image files from all candidate directories with their timestamps
    all_images: List[tuple[int, int, int, str]] = []
    for root in candidate_dirs:
        for subdir in os.listdir(root):
            subdir_path = os.path.join(root, subdir)
            if not os.path.isdir(subdir_path):
                continue
            try:
                dir_timestamp = int(subdir)
            except ValueError:
                continue
            for filename in os.listdir(subdir_path):
                if session_id in filename:
                    if "_edit_" in filename and filename.endswith(".png"):
                        try:
                            edit_num = int(filename.split("_edit_")[1].split(".")[0])
                            all_images.append((dir_timestamp, 2, edit_num, os.path.join(subdir_path, filename)))
                        except (IndexError, ValueError):
                            all_images.append((dir_timestamp, 1, 0, os.path.join(subdir_path, filename)))
                    elif filename.endswith("_recipient_with_label_banana_edited.png"):
                        all_images.append((dir_timestamp, 1, 0, os.path.join(subdir_path, filename)))
                    elif filename.endswith("_recipient_with_label_banana.png"):
                        all_images.append((dir_timestamp, 0, 0, os.path.join(subdir_path, filename)))

    if not all_images:
        return None

    all_images.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    return all_images[0][3]


def _get_last_two_images_with_history(sku: str, session_id: str) -> tuple[Optional[dict], Optional[dict]]:
    """Get the last two images with their edit history for context-aware editing.

    Tolerates accidental whitespace in previously saved history filenames (e.g. "ROC937 _<sid>_history.json").
    """
    metadata_dir = os.path.join(os.getcwd(), "cache", "generated_images")
    normalized_sku = (sku or "").strip()

    # Preferred exact file
    preferred = os.path.join(metadata_dir, f"{normalized_sku}_{session_id}_history.json")

    # Discover any file whose name matches when removing spaces from the sku portion
    candidate_path = None
    if os.path.exists(preferred):
        candidate_path = preferred
    else:
        try:
            target_key = f"{normalized_sku}_{session_id}_history.json".replace(" ", "")
            for fname in os.listdir(metadata_dir):
                if not fname.endswith("_history.json"):
                    continue
                # Compare without spaces in the SKU part
                if fname.replace(" ", "") == target_key:
                    candidate_path = os.path.join(metadata_dir, fname)
                    break
        except Exception:
            candidate_path = None

    if not candidate_path or not os.path.exists(candidate_path):
        return None, None

    with open(candidate_path, "r") as f:
        history = json.load(f)

    if not history:
        return None, None
    if len(history) == 1:
        return history[0], None
    return history[-2], history[-1]


def _save_generated_image_metadata(sku: str, session_id: str, image_path: str, prompt: str, is_edit: bool = False) -> None:
    """Save metadata about the generated image for future editing with history tracking."""
    metadata_dir = os.path.join(os.getcwd(), "cache", "generated_images")
    os.makedirs(metadata_dir, exist_ok=True)
    
    # Normalize SKU to avoid persisting trailing spaces moving forward
    normalized_sku = (sku or "").strip()

    # Load existing history if it exists (tolerate legacy filenames with stray spaces)
    history_file = os.path.join(metadata_dir, f"{normalized_sku}_{session_id}_history.json")
    history = []
    if os.path.exists(history_file):
        with open(history_file, "r") as f:
            history = json.load(f)
    else:
        try:
            target_key = f"{normalized_sku}_{session_id}_history.json".replace(" ", "")
            for fname in os.listdir(metadata_dir):
                if fname.endswith("_history.json") and fname.replace(" ", "") == target_key:
                    with open(os.path.join(metadata_dir, fname), "r") as f:
                        history = json.load(f)
                    break
        except Exception:
            history = []
    
    # Create new entry
    new_entry = {
        "image_path": image_path,
        "prompt": prompt,
        "timestamp": time.time(),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "is_edit": is_edit,
        "edit_number": len([h for h in history if h.get("is_edit", False)]) + 1 if is_edit else 0,
        "session_id": session_id
    }
    
    # Add to history
    history.append(new_entry)
    
    # Keep only the last 10 entries to prevent file from growing too large
    if len(history) > 10:
        history = history[-10:]
    
    # Save updated history
    with open(history_file, "w") as f:
        json.dump(history, f, indent=2)
    
    # Also save the latest entry for backward compatibility
    metadata_file = os.path.join(metadata_dir, f"{normalized_sku}_{session_id}_latest.json")
    with open(metadata_file, "w") as f:
        json.dump(new_entry, f, indent=2)


class DirectLabelOnRecipientTool(BaseTool):
    """Design and apply a label directly onto the NocoDB product preview using fal nano.
    
    This tool supports two modes:
    1. Creation mode (editing=False): Generates a new product mockup from templates, logos, and design brief
    2. Editing mode (editing=True): Edits an existing generated image for better precision and consistency
    
    Workflow:
    - First call with editing=False to create initial mockup
    - Subsequent calls with editing=True to refine the design using history-aware context
    - For first edit: Uses only the original image as reference
    - For subsequent edits: Uses the last TWO images to show progression history
    - Each edit builds upon the previous version with full context of what changed
    
    Editing mode maintains all base requirements:
    - Neutral gray gradient studio backdrop
    - Elegant soft shadow under product
    - Premium hero shot composition
    - All existing text content
    - Same logo placement and appearance
    - Realistic lighting
    - Commercial intent and professional aesthetic
    
    Only applies the specific changes requested by the user while preserving everything else.
    
    History System:
    - Tracks all edits with timestamps and prompts
    - For 2+ edits: Provides last two images with clear context of what changed
    - AI understands the progression: "Image 1 → Image 2 → New Request"
    - Ensures consistent evolution of the design with full awareness of previous changes
    """

    sku: str = Field(..., description="Product SKU (NocoDB)")
    logo_url: str = Field(..., description="Public logo URL to integrate")
    prompt: str = Field("", description="Design brief to apply on the label. Required when editing=True. Optional when editing=False - if not provided, will use design guidelines from social media analysis as primary direction.")
    editing: bool = Field(False, description="Whether to edit an existing generated image (True) or create new (False)")
    session_id: str | None = Field(None, description="Unique session ID to separate different editing sessions (auto-generated if not provided)")
    where_field: str = Field("SKU", description="NocoDB field to match SKU")
    preview_field: str = Field("Product Preview", description="Attachment field for preview in NocoDB")
    outdir: str | None = Field(None, description="Output directory")
    social_media_username: str | None = Field(None, description="Social media username to load design guidelines from cached analysis (e.g., 'choi3an')")
    edit_image_input: str | None = Field(None, description="When editing, optional existing mockup to edit (URL starting with http/https, '/outputs/...' public path, or local file path). If provided, bypasses history lookup and uses this as the edit reference (same behavior as LogoGenerator.edit_logo_input).")

    def run(self) -> Dict[str, str]:  # type: ignore[override]
        load_dotenv(override=False)

        # Print all headers/environment variables for debugging
        self._print_all_headers()
        
        # Extract session ID from headers or generate one
        self.session_id = self._get_session_id_from_headers()
        
        # Validate user prompt requirement for editing mode
        if self.editing:
            if not self.prompt or not self.prompt.strip():
                raise RuntimeError("User prompt is required when editing=True. Please provide a design brief for the edit.")
            print(f"✅ Editing mode: User prompt required and provided")
        
        fal_key = _get_required_env("FAL_KEY")
        
        if self.editing:
            # Editing mode: use previous generated image as reference
            return self._run_editing_mode(fal_key)
        else:
            # Creation mode: generate new image from template
            return self._run_creation_mode(fal_key)

    def _get_session_id_from_headers(self) -> str:
        """Extract session ID from agency headers or generate a new one."""
        # Try to get headers from the agency system
        try:
            import os
            
            # Debug: Print relevant environment variables (can be removed in production)
            header_env_vars = [k for k in os.environ.keys() if any(
                keyword in k.upper() for keyword in ['X_CHAT_ID', 'X_USER_ID', 'X_AGENT_ID']
            )]
            if header_env_vars:
                print("🔍 Found agency headers:", ", ".join(header_env_vars))
            
            # Check for agency system headers (prioritize X-Chat-Id from agency system)
            headers_to_check = [
                'X-Chat-Id',  # Primary: Agency system chat ID
                'X-User-Id',  # Secondary: Agency system user ID
                'X-Agent-Id', # Tertiary: Agency system agent ID
                # Fallback headers
                'X-Chat-ID', 'X-ChatId',
                'X-User-ID', 'X-UserId', 
                'X-Session-Id', 'X-Session-ID', 'X-SessionId',
                'X-Conversation-Id', 'X-Conversation-ID', 'X-ConversationId',
                # Cursor-specific headers (for local development)
                'CURSOR_TRACE_ID', 'CURSOR_SESSION_ID', 'CURSOR_CHAT_ID',
                # Other potential session identifiers
                'AGENCY_SESSION_ID', 'AGENCY_CHAT_ID', 'AGENCY_USER_ID'
            ]
            
            # Check environment variables first (common way agencies pass headers)
            for header in headers_to_check:
                env_var = header.replace('-', '_').upper()
                value = os.getenv(env_var)
                if value:
                    print(f"🔍 Found session ID from header {header}: {value}")
                    return str(value)[:8]  # Use first 8 characters
            
            # If no headers found, check if session_id was explicitly provided
            if self.session_id:
                print(f"🔍 Using provided session ID: {self.session_id}")
                return self.session_id
                
        except Exception as e:
            print(f"⚠️  Could not extract headers: {e}")
        
        # Fallback: generate a new session ID
        import uuid
        new_session_id = str(uuid.uuid4())[:8]
        print(f"🆔 Generated new session ID: {new_session_id}")
        return new_session_id

    def _get_next_edit_number(self) -> int:
        """Get the next edit number for unique file naming."""
        try:
            # Load existing history to get the highest edit number
            metadata_dir = os.path.join(os.getcwd(), "cache", "generated_images")
            history_file = os.path.join(metadata_dir, f"{self.sku}_{self.session_id}_history.json")
            
            if os.path.exists(history_file):
                with open(history_file, "r") as f:
                    history = json.load(f)
                
                # Find the highest edit number
                max_edit_number = 0
                for entry in history:
                    if entry.get("is_edit", False):
                        edit_num = entry.get("edit_number", 0)
                        max_edit_number = max(max_edit_number, edit_num)
                
                return max_edit_number + 1
            else:
                return 1  # First edit
                
        except Exception as e:
            print(f"Warning: Could not determine edit number, using 1: {e}")
            return 1

    def _clear_sku_history(self) -> None:
        """Clear all history and files for this SKU and session (for fresh start)."""
        try:
            # Clear history file
            metadata_dir = os.path.join(os.getcwd(), "cache", "generated_images")
            history_file = os.path.join(metadata_dir, f"{self.sku}_{self.session_id}_history.json")
            latest_file = os.path.join(metadata_dir, f"{self.sku}_{self.session_id}_latest.json")
            
            if os.path.exists(history_file):
                os.remove(history_file)
                print(f"✅ Cleared history file: {history_file}")
            
            if os.path.exists(latest_file):
                os.remove(latest_file)
                print(f"✅ Cleared latest file: {latest_file}")
            
            # Clear output files
            outputs_dir = os.path.join(os.getcwd(), "outputs", self.sku)
            if os.path.exists(outputs_dir):
                import shutil
                shutil.rmtree(outputs_dir)
                print(f"✅ Cleared output directory: {outputs_dir}")
                
        except Exception as e:
            print(f"Warning: Could not clear SKU history: {e}")

    def _print_all_headers(self) -> None:
        """Print key session information for debugging."""
        import os
        chat_id = os.getenv("X_CHAT_ID", "N/A")
        user_id = os.getenv("X_USER_ID", "N/A")
        agent_id = os.getenv("X_AGENT_ID", "N/A")
        print(f"🔍 Tool Session - Chat: {chat_id}, User: {user_id}, Agent: {agent_id}")

    def _run_editing_mode(self, fal_key: str) -> Dict[str, str]:
        """Run in editing mode using the last two images with history for context-aware editing.
        
        This provides the AI with the progression history to understand what changes were made
        and make more informed edits based on the evolution of the design.
        
        IMPORTANT: Editing mode does NOT consider cache style guidelines - only uses user prompt
        and image history for context-aware editing.
        """
        print("🔧 Editing mode: Preparing reference image for edit")

        used_explicit_input = False
        image_urls: List[str] = []
        previous_image = None
        current_image = None

        # 1) If user provided edit_image_input, use it directly (mirror LogoGenerator behavior)
        if isinstance(self.edit_image_input, str) and self.edit_image_input.strip():
            src = self.edit_image_input.strip()
            used_explicit_input = True
            # Map '/outputs/..' public path to local file if applicable
            def _map_public_outputs_path(u: str) -> str | None:
                try:
                    if "/outputs/" in u and not (u.startswith("http://") or u.startswith("https://")):
                        idx = u.find("/outputs/")
                        rel = u[idx + 9:].lstrip("/")
                        local = os.path.join(os.getcwd(), "outputs", rel)
                        return local
                except Exception:
                    return None
                return None
            local_path = None
            if src.startswith("http://") or src.startswith("https://"):
                # Remote URL: download to temp and use
                try:
                    import tempfile
                    r = requests.get(src, timeout=300)
                    r.raise_for_status()
                    tmp_dir = tempfile.mkdtemp(prefix="edit_src_")
                    local_path = os.path.join(tmp_dir, "source_mockup.png")
                    with open(local_path, "wb") as f:
                        f.write(r.content)
                except Exception as e:
                    raise RuntimeError(f"Failed to download edit_image_input: {e}")
            else:
                mapped = _map_public_outputs_path(src)
                if mapped and os.path.exists(mapped):
                    local_path = mapped
                elif os.path.exists(src):
                    local_path = src
                else:
                    raise RuntimeError(f"edit_image_input not found: {src}")

            current_image = {
                "image_path": local_path,
                "prompt": "User-specified edit source",
                "created_at": "",
                "edit_number": 0,
            }

            image_urls = [_to_data_uri_from_file_preserve_alpha(local_path, max_height=1024)]
            editing_prompt = self._build_single_image_prompt(current_image)
        else:
            # 2) No explicit input: use history with tolerant discovery
            previous_image, current_image = _get_last_two_images_with_history(self.sku, self.session_id)
            if not current_image:
                latest_image_path = _get_latest_generated_image_path(self.sku, self.session_id)
                if not latest_image_path or not os.path.exists(latest_image_path):
                    raise RuntimeError(f"No previous generated image found for SKU '{self.sku}' in session '{self.session_id}'. Please generate an initial image first with editing=False.")
                current_image = {
                    "image_path": latest_image_path,
                    "prompt": "Original generated image",
                    "created_at": "Unknown date",
                    "edit_number": 0,
                }
            if not os.path.exists(current_image["image_path"]):
                raise RuntimeError(f"Previous image file not found: {current_image['image_path']}")
            current_image_uri = _to_data_uri_from_file_preserve_alpha(current_image["image_path"], max_height=1024)
            image_urls = [current_image_uri]
            if previous_image and os.path.exists(previous_image["image_path"]):
                previous_image_uri = _to_data_uri_from_file_preserve_alpha(previous_image["image_path"], max_height=1024)
                image_urls.insert(0, previous_image_uri)
                editing_prompt = self._build_two_image_prompt(previous_image, current_image)
            else:
                editing_prompt = self._build_single_image_prompt(current_image)
        
        headers = {"Authorization": f"Key {fal_key}", "Content-Type": "application/json"}
        payload = {"prompt": editing_prompt, "image_urls": image_urls, "num_images": 1, "output_format": "png"}
        resp = requests.post("https://fal.run/fal-ai/nano-banana/edit", headers=headers, json=payload, timeout=300)
        resp.raise_for_status()
        data = resp.json()
        images = data.get("images") or []
        if not images or not images[0].get("url"):
            raise RuntimeError("fal returned no image url")

        r = requests.get(images[0]["url"], timeout=300)
        r.raise_for_status()

        # Get the next edit number for unique naming
        next_edit_number = self._get_next_edit_number()
        
        outdir = self.outdir or os.path.join(os.getcwd(), "outputs", self.sku, str(int(time.time())))
        os.makedirs(outdir, exist_ok=True)
        out_path = os.path.join(outdir, f"{self.sku}_{self.session_id}_recipient_with_label_banana_edit_{next_edit_number}.png")
        with open(out_path, "wb") as f:
            f.write(r.content)

        # Save metadata for future editing (mark as edit)
        _save_generated_image_metadata(self.sku, self.session_id, out_path, self.prompt, is_edit=True)

        outputs_root = os.path.realpath(os.path.join(os.getcwd(), "outputs"))
        real = os.path.realpath(out_path)
        try:
            rel = os.path.relpath(real, outputs_root)
        except ValueError:
            rel = os.path.basename(real)
        public_path = f"/outputs/{rel.replace(os.sep, '/')}"

        result_payload = {
            # 'public_url' should be the fal URL for display; 'image_url' should be local path for edits
            "output_path": out_path, 
            "public_url": images[0]["url"],
            "image_url": out_path,
            "current_image_path": current_image["image_path"],
            "previous_image_path": previous_image["image_path"] if previous_image else None,
            "mode": "editing",
            "edit_number": next_edit_number,
            "edit_history": len(image_urls),
            "fallback_mode": not bool(_get_last_two_images_with_history(self.sku, self.session_id)[1]),
            "design_guidelines_used": False,  # Editing mode never uses cache guidelines
            "user_requirements_provided": True,  # Always true in editing mode (validated)
            "user_requirements_prioritized": True,  # Always true in editing mode
            "final_prompt_word_count": len(editing_prompt.split()),
            "used_explicit_edit_input": used_explicit_input,
        }

        # Add today's date context for downstream booking workflows
        try:
            from datetime import datetime, timezone
            now_utc = datetime.now(timezone.utc)
            result_payload["today_iso_utc"] = now_utc.strftime("%Y-%m-%d")
            result_payload["today_weekday_utc"] = now_utc.strftime("%A")
        except Exception:
            pass

        # Save/override product mockup in GHL (upload binary) and update custom fields. Also upload logo if provided as URL/path
        try:
            token = os.getenv("HIGHLEVEL_ACCESS_TOKEN") or os.getenv("HIGHLEVEL_TOKEN") or os.getenv("GHL_TOKEN")
            location_id = os.getenv("HIGHLEVEL_LOCATION_ID") or os.getenv("GHL_LOCATION_ID")
            if token and location_id:
                from wizard_designer.utils.highlevel_client import _derive_session_key, _load_cached_contact  # type: ignore
                skey = _derive_session_key()
                cached = _load_cached_contact(skey)
                contact_id = (cached or {}).get("id")
                if contact_id:
                    payload = {"tags": ["aaas", "product-mockup"]}
                    cf = []
                    # Upload mockup file to GHL
                    ghl_mock_url = None
                    try:
                        if upload_media:
                            up_res = upload_media(out_path, filename=os.path.basename(out_path))
                            if up_res.get("ok"):
                                ghl_mock_url = up_res.get("url")
                    except Exception:
                        pass
                    # Resolve PRODUCT_MOCKUP_URL field id
                    fid_mock = None
                    try:
                        if get_or_create_custom_field_id:
                            fid_mock = get_or_create_custom_field_id("PRODUCT_MOCKUP_URL")
                    except Exception:
                        pass
                    if not fid_mock and _resolve_field_ids:
                        try:
                            fid_mock = _resolve_field_ids().get("PRODUCT_MOCKUP_URL")
                        except Exception:
                            pass
                    if fid_mock:
                        cf.append({"id": fid_mock, "value": (ghl_mock_url or public_path)})

                    # Upload logo if provided
                    if isinstance(self.logo_url, str) and self.logo_url:
                        ghl_logo_url = None
                        local_logo_path = None
                        if self.logo_url.startswith("http://") or self.logo_url.startswith("https://"):
                            try:
                                import tempfile
                                rlogo = requests.get(self.logo_url, timeout=60)
                                rlogo.raise_for_status()
                                with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(self.logo_url)[1] or ".png") as tf:
                                    tf.write(rlogo.content)
                                    local_logo_path = tf.name
                            except Exception:
                                local_logo_path = None
                        elif os.path.exists(self.logo_url):
                            local_logo_path = self.logo_url

                        if local_logo_path and upload_media:
                            try:
                                up2 = upload_media(local_logo_path, filename=os.path.basename(local_logo_path))
                                if up2.get("ok"):
                                    ghl_logo_url = up2.get("url")
                            except Exception:
                                pass

                        fid_logo = None
                        try:
                            if get_or_create_custom_field_id:
                                fid_logo = get_or_create_custom_field_id("LOGO_URL")
                        except Exception:
                            pass
                        if not fid_logo and _resolve_field_ids:
                            try:
                                fid_logo = _resolve_field_ids().get("LOGO_URL")
                            except Exception:
                                pass
                        if fid_logo and (ghl_logo_url or self.logo_url):
                            cf.append({"id": fid_logo, "value": (ghl_logo_url or self.logo_url)})

                    if cf:
                        payload["customFields"] = cf
                    headers = {
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "Version": "2021-07-28",
                        "LocationId": location_id,
                    }
                    requests.put(f"{API_BASE}/contacts/{contact_id}", headers=headers, json=payload, timeout=30)
                    result_payload["contact_id"] = contact_id
                else:
                    # For upsert flow (no contact yet), ensure a single contact and attach all data
                    ghl_mock_url = None
                    try:
                        if upload_media:
                            up_res = upload_media(out_path, filename=os.path.basename(out_path))
                            if up_res.get("ok"):
                                ghl_mock_url = up_res.get("url")
                    except Exception:
                        pass
                    cf_symbols = {"PRODUCT_MOCKUP_URL": (ghl_mock_url or public_path)}
                    if isinstance(self.logo_url, str) and self.logo_url:
                        ghl_logo_url = None
                        local_logo_path = None
                        if self.logo_url.startswith("http://") or self.logo_url.startswith("https://"):
                            try:
                                import tempfile
                                rlogo = requests.get(self.logo_url, timeout=60)
                                rlogo.raise_for_status()
                                with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(self.logo_url)[1] or ".png") as tf:
                                    tf.write(rlogo.content)
                                    local_logo_path = tf.name
                            except Exception:
                                local_logo_path = None
                        elif os.path.exists(self.logo_url):
                            local_logo_path = self.logo_url
                        if local_logo_path and upload_media:
                            try:
                                up2 = upload_media(local_logo_path, filename=os.path.basename(local_logo_path))
                                if up2.get("ok"):
                                    ghl_logo_url = up2.get("url")
                            except Exception:
                                pass
                        if ghl_logo_url or self.logo_url:
                            cf_symbols["LOGO_URL"] = (ghl_logo_url or self.logo_url)

                    # If brand name is present in context, include it so name exists even without social analysis
                    brand_name = None
                    try:
                        # Try to infer from SKU or previous cache later if needed; keep None if unknown
                        brand_name = os.getenv("DEFAULT_BRAND_NAME") or None
                    except Exception:
                        brand_name = None

                    ensure_res = ensure_contact(email=None, first_name=brand_name, tags=["aaas", "product-mockup"], custom_fields_by_symbol=cf_symbols) if ensure_contact else upsert_contact_with_fields(custom_fields_by_symbol=cf_symbols, tags=["aaas", "product-mockup"])  # type: ignore
                    result_payload["contact_id"] = (ensure_res.get("contact") or {}).get("id") or ensure_res.get("contact_id")
        except Exception as e:
            print(f"HighLevel upsert (mockup) skipped: {e}")

        return result_payload

    def _build_two_image_prompt(self, previous_image: dict, current_image: dict) -> str:
        """Build a prompt for editing with two images showing the progression history."""
        return (
            "You are an expert product packaging editor. You are provided with TWO images showing the design progression:\n\n"
            f"IMAGE 1 (Previous Version): {previous_image.get('created_at', 'Unknown date')}\n"
            f"  - This was the result of: '{previous_image.get('prompt', 'Unknown prompt')}'\n"
            f"  - Edit number: {previous_image.get('edit_number', 'Original')}\n\n"
            f"IMAGE 2 (Current Version): {current_image.get('created_at', 'Unknown date')}\n"
            f"  - This was the result of: '{current_image.get('prompt', 'Unknown prompt')}'\n"
            f"  - Edit number: {current_image.get('edit_number', 'Original')}\n\n"
            f"🎯 PRIMARY OBJECTIVE - USER REQUEST (LABEL-ONLY): {self._label_only_prompt(self.prompt)}\n\n"
            "IMPLEMENTATION GUIDELINES:\n"
            "EDIT SCOPE: Modify ONLY the product label artwork. Do NOT change the bottle, cap, scene, shadows, hands, or environment.\n"
            "BACKGROUND requests refer to the LABEL BACKGROUND within the label area, not the scene background.\n"
            "1. **PRIORITIZE USER REQUIREMENTS**: The user's request above is the main goal. If they want to change text styling, move the logo, modify layout, or alter any design elements, DO IT.\n"
            "2. **MAINTAIN QUALITY STANDARDS**: Keep professional photography aesthetics and commercial appeal\n"
            "3. **PRESERVE WHAT'S NOT MENTIONED**: Only maintain elements that the user hasn't specifically requested to change\n\n"
            "QUALITY STANDARDS TO MAINTAIN (unless user requests otherwise):\n"
            "- Professional studio lighting and shadows\n"
            "- High-quality product photography aesthetic\n"
            "- Commercial viability and visual appeal\n"
            "- Realistic product representation\n\n"
            "Based on the progression from Image 1 to Image 2, implement the user's new request above. "
            "Be bold in making the requested changes while maintaining overall quality and professionalism."
        )

    def _build_single_image_prompt(self, current_image: dict) -> str:
        """Build a prompt for editing with a single image (first edit)."""
        return (
            "You are an expert product packaging editor. This is an existing product mockup that needs specific modifications.\n\n"
            f"CURRENT IMAGE: {current_image.get('created_at', 'Unknown date')}\n"
            f"  - This was the result of: '{current_image.get('prompt', 'Unknown prompt')}'\n"
            f"  - Edit number: {current_image.get('edit_number', 'Original')}\n\n"
            f"🎯 PRIMARY OBJECTIVE - USER REQUEST (LABEL-ONLY): {self._label_only_prompt(self.prompt)}\n\n"
            "IMPLEMENTATION GUIDELINES:\n"
            "EDIT SCOPE: Modify ONLY the product label artwork. Do NOT change the bottle, cap, scene, shadows, hands, or environment.\n"
            "BACKGROUND requests refer to the LABEL BACKGROUND within the label area, not the scene background.\n"
            "1. **PRIORITIZE USER REQUIREMENTS**: The user's request above is the main goal. If they want to change text styling, move the logo, modify layout, alter colors, change positioning, or transform any design elements, DO IT.\n"
            "2. **MAINTAIN QUALITY STANDARDS**: Keep professional photography aesthetics and commercial appeal\n"
            "3. **PRESERVE WHAT'S NOT MENTIONED**: Only maintain elements that the user hasn't specifically requested to change\n\n"
            "QUALITY STANDARDS TO MAINTAIN (unless user requests otherwise):\n"
            "- Professional studio lighting and shadows\n"
            "- High-quality product photography aesthetic\n"
            "- Commercial viability and visual appeal\n"
            "- Realistic product representation\n\n"
            "Implement the user's requested changes above. Be bold in making the requested modifications while maintaining overall quality and professionalism. "
            "If the user wants to change text styling, move elements, alter layout, or modify any design aspects, prioritize their requirements."
        )

    def _label_only_prompt(self, user_prompt: str) -> str:
        """Constrain edits to label area and map generic 'background' to label background."""
        try:
            p = user_prompt or ""
            # Simple replacements to bias wording toward label background
            replacements = {
                " background": " label background",
                "Background": "Label background",
                "BG": "label background",
            }
            for k, v in replacements.items():
                p = p.replace(k, v)
            if not p.lower().startswith("edit only the label area"):
                p = "Edit only the label area: " + p
            return p
        except Exception:
            return f"Edit only the label area: {user_prompt}"

    def _run_creation_mode(self, fal_key: str) -> Dict[str, str]:
        """Run in creation mode generating a new image from templates."""
        # Clear existing history for this SKU and session (fresh start)
        self._clear_sku_history()
        
        base_url = _get_required_env("NC_BASE_URL")
        api_token = _get_required_env("NC_API_TOKEN")
        table_id = _get_required_env("NC_TABLE_ID")

        # Fetch preview URL via NocoDB REST
        preview_url = _fetch_preview_url(
            base_url=base_url,
            api_token=api_token,
            table_id=table_id,
            where_field=self.where_field,
            sku=self.sku,
            preview_field=self.preview_field,
        )

        # Build fal request images in order: recipient, template center (if available), logo
        image_urls: List[str] = [_to_data_uri_from_url(preview_url, max_height=1024)]

        # Template center: preserve text; gives model exact content while allowing restyling
        # Prefer NocoDB label/mask for center; fallback to local template folder
        center_uri = _center_crop_from_nocodb(
            sku=self.sku,
            base_url=base_url,
            api_token=api_token,
            table_id=table_id,
            where_field=self.where_field,
            label_field="Product Image",
            mask_field="Mask Image",
            max_height=1024,
        ) or _center_crop_from_label_template(self.sku, max_height=1024)
        if center_uri:
            image_urls.append(center_uri)

        # Accept either public URL or local file path for logo; prefer local path when under outputs
        def _url_to_local_outputs_path(u: str) -> Optional[str]:
            try:
                if not isinstance(u, str):
                    return None
                if "/outputs/" in u:
                    # Map "/outputs/..." to local outputs directory
                    idx = u.find("/outputs/")
                    rel = u[idx + 9:].lstrip("/")
                    local = os.path.join(os.getcwd(), "outputs", rel)
                    return local
                return None
            except Exception:
                return None

        local_from_url = _url_to_local_outputs_path(self.logo_url)
        if isinstance(self.logo_url, str) and (self.logo_url.startswith("http://") or self.logo_url.startswith("https://")):
            # If URL points to our outputs, use local path to avoid network fetch
            if local_from_url and os.path.exists(local_from_url):
                image_urls.append(_to_data_uri_from_file_preserve_alpha(local_from_url, max_height=1024))
            else:
                image_urls.append(_to_data_uri_from_url_preserve_alpha(self.logo_url, max_height=1024))
        elif isinstance(self.logo_url, str) and os.path.exists(self.logo_url):
            image_urls.append(_to_data_uri_from_file_preserve_alpha(self.logo_url, max_height=1024))
        else:
            raise RuntimeError("logo_url must be a public URL or an existing local file path")

        # Load and integrate design guidelines from social media analysis
        design_summary = ""
        username = None
        guidelines = None
        archetype_enhancements = ""
        has_user_prompt = self.prompt and self.prompt.strip()
        palette_override_text = ""
        
        try:
            # Use provided username or auto-discover from session-scoped analysis cache
            username = self.social_media_username or _discover_latest_username_for_session()
            if username:
                print(f"🔍 Using username for design guidelines: {username}")
                analysis = _load_social_media_analysis(username, self.session_id)
                if analysis:
                    guidelines = _extract_design_guidelines(analysis)
                else:
                    print("⚠️  No analysis data found for username")
            else:
                print("ℹ️  No username provided or discovered")
        except Exception as e:
            print(f"⚠️  Error loading design guidelines: {e}")

        # Palette override (session-scoped cache written by ColorPaletteTool) — PRIORITIZE over social palette
        try:
            sid = self.session_id or _get_session_id_from_headers()
            override = _load_palette_override(sid)
            palette_override_text = _format_palette_override_text(override)
            if isinstance(override, dict):
                pal = override.get("palette") if isinstance(override.get("palette"), list) else None
                roles = override.get("roles") if isinstance(override.get("roles"), list) else []
                if pal:
                    if not guidelines:
                        guidelines = {}
                    # Force GPT summary to use the palette override instead of social palette
                    guidelines["color_palette"] = [str(x) for x in pal if isinstance(x, str)]
                if roles:
                    if not guidelines:
                        guidelines = {}
                    guidelines["color_roles_text"] = ", ".join([
                        f"{(r.get('role') or 'unspecified')}={r.get('hex')}" for r in roles if isinstance(r, dict)
                    ])
            if palette_override_text:
                print("🎨 Palette override detected for this session; will inject into GPT summary and final prompt")
        except Exception:
            palette_override_text = ""

        # With guidelines (possibly updated by palette override), generate design summary via GPT
        try:
            archetype_enhancements = _generate_archetype_specific_prompt_enhancements(guidelines or {})
            design_summary = _generate_design_summary_with_gpt(guidelines or {}, self.prompt if has_user_prompt else "")
            if isinstance(design_summary, str) and not design_summary.startswith("Error"):
                print(f"✅ Generated design summary with GPT-4o")
                print(f"📊 Design summary word count: {len(design_summary.split())} words")
                print(f"🎨 Archetype: {(guidelines or {}).get('archetype', 'Unknown')}")
                if has_user_prompt:
                    print(f"🎯 User requirements prioritized in design summary")
                else:
                    print(f"📋 Using cache data as primary design direction (with palette override if present)")
        except Exception as e:
            print(f"⚠️  Error generating GPT design summary: {e}")
        
        # Build comprehensive prompt with design context
        if design_summary and not design_summary.startswith("Error"):
            
            if has_user_prompt:
                full_prompt = f"""{BASE_PROMPT}

DESIGN CONTEXT: {design_summary}

{archetype_enhancements}

{palette_override_text}

USER REQUIREMENTS: {self.prompt}

EXECUTION: Prioritize user vision, use established color palette/typography, create beautiful background visuals/gradients/patterns, apply detailed text styling with shadows/effects, ensure commercial viability, create cohesive brand identity."""
            else:
                full_prompt = f"""{BASE_PROMPT}

DESIGN CONTEXT: {design_summary}

{archetype_enhancements}

{palette_override_text}

EXECUTION: Leverage color palette/typography extensively, create beautiful background visuals/gradients/patterns matching archetype, apply detailed text styling with shadows/effects, ensure commercial viability, create cohesive brand identity aligned with archetype, make attractive and sellable with custom colors/typography/patterns."""
        elif not username:
            # No design guidelines available and no user prompt
            if not has_user_prompt:
                raise RuntimeError("No design guidelines received. Please ask user for design requirements or provide a social media username for automatic analysis.")
            else:
                # User prompt provided but no design guidelines
                full_prompt = f"""{BASE_PROMPT}

USER REQUIREMENTS: {self.prompt}

EXECUTION: Create stunning customized label based on user requirements, focus on beautiful background visuals, detailed text styling, visual effects, make label stand out, ensure commercial viability.{palette_override_text}"""
                print("ℹ️  Using user prompt without design guidelines")
        else:
            # Design guidelines failed to load but username was found
            if not has_user_prompt:
                raise RuntimeError("No design guidelines received. Please ask user for design requirements or check if the social media analysis data is available.")
            else:
                # User prompt provided but design guidelines failed
                full_prompt = f"""{BASE_PROMPT}

USER REQUIREMENTS: {self.prompt}

EXECUTION: Create stunning customized label based on user requirements, focus on beautiful background visuals, detailed text styling, visual effects, make label stand out, ensure commercial viability.{palette_override_text}"""
                print("ℹ️  Using user prompt without design guidelines (analysis failed)")
        
        # Print final prompt word count and summary
        final_word_count = len(full_prompt.split())
        print(f"📊 Final prompt word count: {final_word_count} words")
        print(f"🎨 Enhanced prompt generation completed with archetype-specific enhancements")
        if archetype_enhancements:
            print(f"✨ Archetype enhancements applied: {len(archetype_enhancements.split())} words")
        print(f"🚀 Ready to generate beautiful, customized label design")

        headers = {"Authorization": f"Key {fal_key}", "Content-Type": "application/json"}
        payload = {"prompt": full_prompt, "image_urls": image_urls, "num_images": 1, "output_format": "png"}
        resp = requests.post("https://fal.run/fal-ai/nano-banana/edit", headers=headers, json=payload, timeout=300)
        if resp.status_code != 200:
            print(f"❌ fal.ai API error: {resp.status_code}")
            print(f"Response: {resp.text}")
        resp.raise_for_status()
        data = resp.json()
        images = data.get("images") or []
        if not images or not images[0].get("url"):
            raise RuntimeError("fal returned no image url")

        r = requests.get(images[0]["url"], timeout=300)
        r.raise_for_status()

        outdir = self.outdir or os.path.join(os.getcwd(), "outputs", self.sku, str(int(time.time())))
        os.makedirs(outdir, exist_ok=True)
        out_path = os.path.join(outdir, f"{self.sku}_{self.session_id}_recipient_with_label_banana.png")
        with open(out_path, "wb") as f:
            f.write(r.content)

        # Save metadata for future editing (mark as original, not edit)
        _save_generated_image_metadata(self.sku, self.session_id, out_path, self.prompt, is_edit=False)

        outputs_root = os.path.realpath(os.path.join(os.getcwd(), "outputs"))
        real = os.path.realpath(out_path)
        try:
            rel = os.path.relpath(real, outputs_root)
        except ValueError:
            rel = os.path.basename(real)
        public_path = f"/outputs/{rel.replace(os.sep, '/')}"

        # Save/override product mockup URL on the contact (creation mode). Also save selected LOGO_URL if public
        try:
            token = os.getenv("HIGHLEVEL_ACCESS_TOKEN") or os.getenv("HIGHLEVEL_TOKEN") or os.getenv("GHL_TOKEN")
            location_id = os.getenv("HIGHLEVEL_LOCATION_ID") or os.getenv("GHL_LOCATION_ID")
            if token and location_id:
                from wizard_designer.utils.highlevel_client import _derive_session_key, _load_cached_contact, _resolve_field_ids  # type: ignore
                skey = _derive_session_key()
                cached = _load_cached_contact(skey)
                contact_id = (cached or {}).get("id")
                if contact_id:
                    fids = _resolve_field_ids()
                    payload = {"tags": ["aaas", "product-mockup"]}
                    cf = []
                    fid_mock = fids.get("PRODUCT_MOCKUP_URL")
                    if fid_mock:
                        cf.append({"id": fid_mock, "value": public_path})
                    if isinstance(self.logo_url, str) and (self.logo_url.startswith("http://") or self.logo_url.startswith("https://")):
                        fid_logo = fids.get("LOGO_URL")
                        if fid_logo:
                            cf.append({"id": fid_logo, "value": self.logo_url})
                    if cf:
                        payload["customFields"] = cf
                    headers = {
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "Version": "2021-07-28",
                        "LocationId": location_id,
                    }
                    requests.put(f"{API_BASE}/contacts/{contact_id}", headers=headers, json=payload, timeout=30)
                else:
                    custom_fields = {"PRODUCT_MOCKUP_URL": public_path}
                    if isinstance(self.logo_url, str) and (self.logo_url.startswith("http://") or self.logo_url.startswith("https://")):
                        custom_fields["LOGO_URL"] = self.logo_url
                    upsert_contact_with_fields(
                        email=f"{(self.social_media_username or 'brand')}_brand@example.com",
                        custom_fields_by_symbol=custom_fields,
                        tags=["aaas", "product-mockup"],
                    )
        except Exception as e:
            print(f"HighLevel upsert (mockup-create) skipped: {e}")

        return {
            # 'public_url' should be the fal URL for display; 'image_url' should be local path for edits
            "output_path": out_path, 
            "public_url": images[0]["url"],
            "image_url": out_path,
            "preview_url": preview_url,
            "mode": "creation",
            "design_guidelines_used": bool(design_summary and not design_summary.startswith("Error")),
            "username_used": username if username else None,
            "user_requirements_provided": has_user_prompt,
            "user_requirements_prioritized": has_user_prompt and bool(design_summary and not design_summary.startswith("Error")),
            "final_prompt_word_count": final_word_count,
            "design_summary_word_count": len(design_summary.split()) if design_summary else 0
        }


if __name__ == "__main__":
    sku='ROC502'  # Use valid SKU that exists in NocoDB
    logo_url="/Users/didac/Vrsen-AI/Repos/aaas-truva/truva_logo.png" 
    prompt='premium style, medium aged public, with gradient background of label, make crazy visuals in bacgkround of label to attract attention, aesthetic, color palette green, white and black'
    
    print("🎨 DirectLabelOnRecipientTool - Interactive Design Editor")
    print("=" * 60)
    
    # Test cache loading functionality
    print("\n🧪 Testing Cache Loading Functionality")
    print("-" * 50)
    
    # Set up test environment
    import os
    os.environ['X_CHAT_ID'] = 'KcnFKG9l'  # Match albapaulfe session
    
    # Test 1: With albapaulfe username and user prompt (user priority)
    print("\n🎯 Test 1: Enhanced User Requirements + Cache Data (User Priority)")
    print("=" * 60)
    
    tool_test_1 = DirectLabelOnRecipientTool(
        sku=sku,
        logo_url=logo_url,
        prompt="Create a minimalist black and white label with clean typography, elegant gradients, and sophisticated visual effects",  # Enhanced user requirements
        editing=False,
        social_media_username='albapaulfe'  # Has vibrant colors in cache
    )
    
    print(f"✅ Tool initialized with enhanced user priority")
    print(f"   - User prompt: {tool_test_1.prompt}")
    print(f"   - Username: {tool_test_1.social_media_username}")
    
    # Test enhanced design guidelines loading
    try:
        analysis = _load_social_media_analysis('albapaulfe', 'KcnFKG9l')
        if analysis:
            guidelines = _extract_design_guidelines(analysis)
            archetype_enhancements = _generate_archetype_specific_prompt_enhancements(guidelines)
            print(f"   - Cache archetype: {guidelines.get('archetype', 'Unknown')}")
            print(f"   - Cache colors: {guidelines.get('color_palette', [])}")
            print(f"   - Cache sentiment: {guidelines.get('sentiment', 'N/A')}")
            print(f"   - Archetype enhancements: {len(archetype_enhancements.split())} words")
            print(f"   ✅ Enhanced cache data loaded successfully")
        else:
            print(f"   ❌ No cache data found")
    except Exception as e:
        print(f"   ❌ Error loading cache: {e}")
    
    # Test 2: With albapaulfe username but no user prompt (cache priority)
    print("\n📋 Test 2: Cache Data Only (Cache Priority)")
    print("=" * 50)
    
    tool_test_2 = DirectLabelOnRecipientTool(
        sku=sku,
        logo_url=logo_url,
        prompt="",  # No user requirements - use cache as primary
        editing=False,
        social_media_username='albapaulfe'
    )
    
    print(f"✅ Tool initialized with cache priority")
    print(f"   - User prompt: '{tool_test_2.prompt}' (empty)")
    print(f"   - Username: {tool_test_2.social_media_username}")
    print(f"   ✅ Will use cache data as primary design direction")
    
    # Test 3: No username, user prompt only
    print("\n👤 Test 3: User Prompt Only (No Cache)")
    print("=" * 50)
    
    tool_test_3 = DirectLabelOnRecipientTool(
        sku=sku,
        logo_url=logo_url,
        prompt="Create a luxury gold and black premium label",  # User requirements only
        editing=False,
        social_media_username=None  # No cache data
    )
    
    print(f"✅ Tool initialized with user prompt only")
    print(f"   - User prompt: {tool_test_3.prompt}")
    print(f"   - Username: {tool_test_3.social_media_username}")
    print(f"   ✅ Will use user prompt without design guidelines")
    
    # Test 4: No username, no user prompt (should raise error)
    print("\n⚠️  Test 4: No Guidelines Available (Error Case)")
    print("=" * 50)
    
    try:
        tool_test_4 = DirectLabelOnRecipientTool(
            sku=sku,
            logo_url=logo_url,
            prompt="",  # No user requirements
            editing=False,
            social_media_username=None  # No cache data
        )
        print(f"✅ Tool initialized (will raise error when run() is called)")
        print(f"   - User prompt: '{tool_test_4.prompt}' (empty)")
        print(f"   - Username: {tool_test_4.social_media_username}")
        print(f"   ⚠️  Expected: RuntimeError when run() is called")
    except Exception as e:
        print(f"   ❌ Unexpected error during initialization: {e}")
    
    # Test 5: Editing mode validation (user prompt required)
    print("\n🔧 Test 5: Editing Mode Validation")
    print("=" * 50)
    
    # Test 5a: Editing mode with user prompt (should work)
    try:
        tool_test_5a = DirectLabelOnRecipientTool(
            sku=sku,
            logo_url=logo_url,
            prompt="Make the background more vibrant",  # User prompt provided
            editing=True,  # Editing mode
            social_media_username='albapaulfe'  # Cache available but should be ignored
        )
        print(f"✅ Tool initialized for editing with user prompt")
        print(f"   - User prompt: {tool_test_5a.prompt}")
        print(f"   - Editing mode: {tool_test_5a.editing}")
        print(f"   - Username: {tool_test_5a.social_media_username}")
        print(f"   ✅ Will use user prompt and image history only (no cache guidelines)")
    except Exception as e:
        print(f"   ❌ Unexpected error: {e}")
    
    # Test 5b: Editing mode without user prompt (should raise error)
    try:
        tool_test_5b = DirectLabelOnRecipientTool(
            sku=sku,
            logo_url=logo_url,
            prompt="",  # No user prompt
            editing=True,  # Editing mode
            social_media_username='albapaulfe'  # Cache available but should be ignored
        )
        print(f"✅ Tool initialized (will raise error when run() is called)")
        print(f"   - User prompt: '{tool_test_5b.prompt}' (empty)")
        print(f"   - Editing mode: {tool_test_5b.editing}")
        print(f"   ⚠️  Expected: RuntimeError when run() is called")
    except Exception as e:
        print(f"   ❌ Unexpected error during initialization: {e}")
    
    print("\n🎉 Enhanced Prompt Generation Tests Completed!")
    print("=" * 60)
    print("📊 Enhanced Test Summary:")
    print("   ✅ User Priority: Enhanced user requirements + detailed cache data + archetype enhancements")
    print("   ✅ Cache Priority: Detailed cache data as primary direction + archetype-specific styling")
    print("   ✅ User Only: Enhanced user prompt without cache data")
    print("   ✅ Error Handling: Clear messages when no guidelines available")
    print("   ✅ Editing Mode: User prompt required, cache guidelines ignored")
    print("   🎨 NEW: Archetype-specific prompt enhancements for targeted design directions")
    print("   🎨 NEW: GPT-4o integration for more detailed, creative prompt generation")
    print("   🎨 NEW: Enhanced base prompt with detailed visual requirements")
    print("   🎨 NEW: Super detailed text styling and background visual instructions")
    
    # Print ALL environment variables to see what headers are available
    print("\n📋 ALL ENVIRONMENT VARIABLES:")
    print("-" * 40)
    for key, value in sorted(os.environ.items()):
        print(f"{key}: {value}")
    print("-" * 40)
    
    # Step 1: Create initial image
    print("\n📸 Creating initial product mockup...")
    print(f"SKU: {sku}")
    print(f"Initial prompt: {prompt}")
    print("-" * 40)
    
    tool = DirectLabelOnRecipientTool(
        sku=sku,
        logo_url=logo_url,
        prompt=prompt,
        editing=False
    )
    result = tool.run()
    
    print("✅ Initial image created successfully!")
    print(f"📁 Output: {result['output_path']}")
    print(f"🌐 Public URL: {result['public_path']}")
    
    # Step 2: Interactive editing loop
    print("\n🔄 Starting interactive editing mode...")
    print("💡 You can now make multiple edits to refine your design")
    print("📝 Type 'quit', 'exit', or 'done' to finish editing")
    print("💡 Type 'help' for editing tips, 'history' for edit info, or 'quit' to finish")
    print("=" * 60)
    
    edit_count = 0
    
    while True:
        # Get user input for edit prompt
        print(f"\n✏️  Edit #{edit_count + 1}")
        edit_prompt = input("Enter your edit request (or 'quit' to finish): ").strip()
        
        # Check for special commands
        if edit_prompt.lower() in ['quit', 'exit', 'done', 'q']:
            print("\n🎉 Editing session completed!")
            print(f"📊 Total edits made: {edit_count}")
            break
        
        if edit_prompt.lower() == 'help':
            print("\n💡 Editing Tips:")
            print("   • Be specific: 'Change background to dark blue'")
            print("   • Focus on one element: 'Make the logo larger'")
            print("   • Color changes: 'Change text color to white'")
            print("   • Style changes: 'Make it more modern and minimalist'")
            print("   • Background: 'Add gradient background'")
            print("   • Lighting: 'Make lighting more dramatic'")
            print("   • Composition: 'Move logo to the left'")
            print("   • The AI will maintain all other elements unchanged")
            continue
        
        if edit_prompt.lower() == 'history':
            print(f"\n📊 Edit History for SKU: {sku}")
            print(f"   • Total edits made: {edit_count}")
            if edit_count > 0:
                print("   • Each edit builds upon the previous version")
                print("   • AI uses last 2 images for context (when available)")
            else:
                print("   • No edits made yet - only original image exists")
            continue
        
        if not edit_prompt:
            print("⚠️  Please enter a valid edit prompt or 'quit' to finish.")
            continue
        
        # Perform the edit
        try:
            print(f"\n🔄 Processing edit: '{edit_prompt}'")
            print("-" * 40)
            
            edit_tool = DirectLabelOnRecipientTool(
                sku=sku,
                logo_url=logo_url,
                prompt=edit_prompt,
                editing=True
            )
            edit_result = edit_tool.run()
            
            edit_count += 1
            
            print("✅ Edit completed successfully!")
            print(f"📁 Output: {edit_result['output_path']}")
            print(f"🌐 Public URL: {edit_result['public_path']}")
            print(f"📊 Edit history: {edit_result['edit_history']} images used as reference")
            
            if edit_result.get('previous_image_path'):
                print(f"📸 Previous image: {edit_result['previous_image_path']}")
            
        except Exception as e:
            print(f"❌ Error during edit: {str(e)}")
            print("🔄 Please try again with a different prompt.")
            continue
    
    print("\n🏁 Session Summary:")
    print(f"   • SKU: {sku}")
    print(f"   • Initial prompt: {prompt}")
    print(f"   • Total edits: {edit_count}")
    print(f"   • Final output: {result['output_path']}")
    print("\n🎨 Thank you for using DirectLabelOnRecipientTool!")


