from agency_swarm.tools import BaseTool
from pydantic import Field
import json
import os
import asyncio
import logging
import requests
from dotenv import load_dotenv
from enum import Enum
from typing import List, Dict, Tuple
from wizard_designer.utils.highlevel_client import upsert_contact_with_fields, API_BASE
try:
    # Optional helpers for media upload, field creation, and contact ensuring
    from wizard_designer.utils.highlevel_client import upload_media, get_or_create_custom_field_id, _resolve_field_ids, ensure_contact  # type: ignore
except Exception:  # pragma: no cover
    upload_media = None  # type: ignore
    get_or_create_custom_field_id = None  # type: ignore
    _resolve_field_ids = None  # type: ignore
    ensure_contact = None  # type: ignore


def setup_logger(name: str) -> logging.Logger:
    """Create a simple console logger if not already configured."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


logger = setup_logger(__name__)

def _get_session_id_from_headers() -> str:
    """Extract session ID from agency headers or generate a new one.
    
    For deployment, this function prioritizes X-Chat-Id header which is used
    by the agency system to maintain session consistency across requests.
    """
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
                logger.info(f"🔍 Found session ID from header {header}: {session_id}")
                return session_id
        
        # Fallback: generate new UUID for local development
        import uuid
        new_session_id = str(uuid.uuid4())[:8]
        logger.info(f"🆔 Generated new session ID: {new_session_id}")
        return new_session_id
        
    except Exception as e:
        logger.warning(f"Error getting session ID: {e}")
        import uuid
        return str(uuid.uuid4())[:8]

def _load_social_media_analysis(username: str) -> dict | None:
    """Load the latest social media analysis for the given username from current session"""
    try:
        import json
        from pathlib import Path
        
        # Get current session ID
        session_id = _get_session_id_from_headers()
        
        # Find the analysis directory
        current_dir = Path(__file__).parent
        analysis_dir = current_dir.parent / "cache" / "social_media_analysis"
        
        if not analysis_dir.exists():
            logger.warning(f"Analysis directory not found: {analysis_dir}")
            return None
        
        # Find the latest analysis file for this username and session
        pattern = f"{username}_{session_id}_*.json"
        files = list(analysis_dir.glob(pattern))
        
        if not files:
            logger.warning(f"No analysis files found for username: {username} and session: {session_id}")
            return None
        
        # Get the most recent file
        latest_file = max(files, key=lambda f: f.stat().st_mtime)
        logger.info(f"Loading analysis from: {latest_file}")
        
        with open(latest_file, 'r') as f:
            data = json.load(f)
            return data.get("analysis", {})
            
    except Exception as e:
        logger.warning(f"Failed to load social media analysis for {username}: {e}")
        return None

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
        logger.warning(f"Failed to discover username for session: {e}")
        return None

def load_style_guide() -> dict[str, dict[str, str]]:
    """Load the style guide from the prompts file."""
    prompts_dir = os.path.dirname(os.path.abspath(__file__))
    prompts_file = os.path.join(prompts_dir, "prompts", "logo_generation_styles.txt")
    
    style_guide = {}
    
    try:
        with open(prompts_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Parse the file content
        current_style = None
        current_prompt = None
        current_guide = None
        
        for line in content.split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
                
            # Check for style name patterns like "1. Bold Minimalism with a Twist"
            if line.startswith(('1.', '2.', '3.', '4.', '5.', '6.', '7.', '8.', '9.')):
                # Save previous style if exists
                if current_style and current_prompt and current_guide:
                    style_guide[current_style] = {
                        "prompt": current_prompt,
                        "style_guide": current_guide
                    }
                
                # Extract style name (remove number and period)
                current_style = line.split('.', 1)[1].strip()
                current_prompt = None
                current_guide = None
                
            elif line.startswith('prompt:'):
                current_prompt = line[7:].strip()  # Remove 'prompt:' prefix
                
            elif line.startswith('style_guide:'):
                current_guide = line[12:].strip()  # Remove 'style_guide:' prefix
        
        # Don't forget the last style
        if current_style and current_prompt and current_guide:
            style_guide[current_style] = {
                "prompt": current_prompt,
                "style_guide": current_guide
            }

        # If parsing produced no styles, fall back to defaults
        if not style_guide:
            logger.warning("Style guide parsed empty; using default styles fallback")
            style_guide = {
                "Minimalist": {
                    "prompt": "Clean, minimalist logo, simple icon, modern sans serif, balanced spacing, high legibility, professional branding, no background clutter.",
                    "style_guide": "Use 1–2 flat colors, high contrast, negative space, geometric shapes, avoid gradients and textures.",
                },
                "Elegant": {
                    "prompt": "Elegant, premium logo, refined serif or high-contrast sans, subtle icon, balanced composition, sophisticated brand feel.",
                    "style_guide": "Use neutral or metallic palette, generous whitespace, fine line iconography, avoid busy backgrounds.",
                },
                "Athletic": {
                    "prompt": "Bold athletic logo, dynamic icon, strong sans serif, high impact, performance-oriented, clean background.",
                    "style_guide": "Use energetic colors with high contrast, geometric or shield-like icons, emphasize legibility and movement cues.",
                },
            }
            
    except FileNotFoundError:
        logger.warning(f"Style guide file not found at {prompts_file}, using fallback")
        # Fallback to a minimal style guide
        style_guide = {
            "Minimalist": {
                "prompt": "Clean, minimalist logo, simple icon, modern sans serif, balanced spacing, high legibility, professional branding, no background clutter.",
                "style_guide": "Use 1–2 flat colors, high contrast, negative space, geometric shapes, avoid gradients and textures.",
            }
        }
    except Exception as e:
        logger.error(f"Error loading style guide: {e}, using fallback")
        style_guide = {
            "Minimalist": {
                "prompt": "Clean, minimalist logo, simple icon, modern sans serif, balanced spacing, high legibility, professional branding, no background clutter.",
                "style_guide": "Use 1–2 flat colors, high contrast, negative space, geometric shapes, avoid gradients and textures.",
            }
        }
    
    return style_guide

load_dotenv()  # Load environment variables


# Build a dynamic Enum of available logo styles from the style guide file
# Exposed for UI/agent to list valid options
def _build_logo_style_enum(style_guide: dict[str, dict[str, str]]):
    try:
        # Ensure valid member names by replacing spaces and special chars
        members = {}
        for style_name in style_guide.keys():
            key = (
                style_name.upper()
                .replace(" ", "_")
                .replace("-", "_")
                .replace("&", "AND")
            )
            if key in members:
                # Disambiguate if collision
                suffix = 2
                while f"{key}_{suffix}" in members:
                    suffix += 1
                key = f"{key}_{suffix}"
            members[key] = style_name
        return Enum("LogoStyle", members)  # type: ignore[arg-type]
    except Exception:
        # Fallback minimal enum
        return Enum("LogoStyle", {"MINIMALIST": "Minimalist"})


# Cache the style guide and enum at import time
STYLE_GUIDE = load_style_guide()
LogoStyle = _build_logo_style_enum(STYLE_GUIDE)


class LogoGenerator(BaseTool):
    """
    Generate THREE logos that follow design requirements (creation), or ONE edited logo (editing).

    Creation mode (editing=False):
    - User may provide up to 3 style names (from style enum). If none provided, AI auto-picks styles using social cache
    - Builds style-specific prompts using templates from style guide plus optional social-media guidelines and user requirements
    - Generates exactly THREE logo images across the chosen styles via fal nano-banana
    - Saves outputs under outputs/logos/<timestamp>/ and records history for future edits
    - Upserts BRAND_NAME only (LOGO_URL is saved later by DirectLabelOnRecipientTool after user picks)

    Editing mode (editing=True):
    - Requires `prompt` describing the requested change(s)
    - Edits an existing logo provided by `edit_logo_input` (URL or local path). If not provided, loads the last generated logo
    - Applies edits via fal nano-banana/edit with the previous image as reference
    - Returns ONE edited logo and updates history (incremental edit number)
    """

    brand_name: str | None = Field(
        None, description="Brand name to render in the logo (recommended)"
    )

    prompt: str = Field(
        "", description="Design requirements. REQUIRED when editing=True; optional otherwise."
    )

    editing: bool = Field(
        False, description="Set True to edit the last generated logo for this session"
    )

    output_format: str = Field(
        "png", description="Output format (png recommended for transparent logos)"
    )

    outdir: str | None = Field(
        None, description="Custom output directory. Defaults to outputs/logos/<timestamp>"
    )

    session_id: str | None = Field(
        None, description="Optional session id; auto-derived from headers if not provided"
    )

    social_media_username: str | None = Field(
        None, description="Social media username for cached design guidelines (e.g., 'choi3an')"
    )

    # New: preferred styles and generation controls
    styles: List[str] | None = Field(
        None,
        description="Optional list of 1-3 style names to use (see list_available_styles). When omitted, AI picks styles.",
    )

    num_logos: int = Field(
        3, description="Total logos to generate in creation mode (always coerced to 3)."
    )

    edit_logo_input: str | None = Field(
        None,
        description="When editing, an existing logo URL or local file path to edit. If omitted, uses last generated logo.",
    )

    def _extract_design_guidelines(self, analysis: dict) -> dict:
        """Extract design guidelines from social media analysis"""
        design_guidance = analysis.get("brand_design_guidance", {})
        
        guidelines = {
            "sentiment": design_guidance.get("sentiment", "modern and professional"),
            "tone_words": design_guidance.get("tone_words", ["modern", "clean"]),
            "color_palette": design_guidance.get("color_palette_hex", ["#000000", "#FFFFFF"]),
            "style_keywords": design_guidance.get("logo_guidelines", {}).get("style_keywords", ["modern", "clean"]),
            "iconography": design_guidance.get("logo_guidelines", {}).get("iconography_recommendations", "Simple, clean icons"),
            "typography": design_guidance.get("typography", "Modern sans-serif"),
            "archetype": analysis.get("inferred_archetype", {}).get("name", "Unknown"),
            "visual_style": analysis.get("visual_style", {}).get("styling_vibe_tags", ["modern"]),
            "influencer_alignment": analysis.get("brand_naming_guidelines", {}).get("influencer_alignment", "Professional and aspirational")
        }
        
        return guidelines

    def _palette_override_file(self, session_id: str) -> str:
        cache_dir = os.path.join(os.getcwd(), "tools", "cache", "social_media_analysis")
        os.makedirs(cache_dir, exist_ok=True)
        return os.path.join(cache_dir, f"PALETTE_{session_id}_latest.json")

    def _load_palette_override(self, session_id: str) -> dict | None:
        try:
            path = self._palette_override_file(session_id)
            if os.path.exists(path):
                with open(path, "r") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            return None
        return None

    async def _generate_logo_single(self, prompt: str):
        """Generate a single logo using nano-banana via HTTP (same style as DirectLabelOnRecipientTool)."""
        logger.info("Generating single logo using nano-banana via HTTP")
        try:
            fal_key = os.getenv("FAL_KEY")
            if not fal_key:
                raise RuntimeError("FAL_KEY environment variable is not set")

            headers = {"Authorization": f"Key {fal_key}", "Content-Type": "application/json"}
            payload = {"prompt": prompt, "num_images": 1, "output_format": self.output_format}
            resp = requests.post("https://fal.run/fal-ai/nano-banana", headers=headers, json=payload, timeout=300)
            resp.raise_for_status()
            data = resp.json()
            images = data.get("images") or []
            if not images or not images[0].get("url"):
                return ""
            return images[0]["url"]
        except Exception as e:
            logger.exception(f"Error generating logo: {str(e)}")
            return ""
    

    async def _generate_logo_multi(self, prompt: str, count: int) -> List[str]:
        """Generate up to `count` logos for one prompt using nano-banana via HTTP."""
        urls: List[str] = []
        try:
            fal_key = os.getenv("FAL_KEY")
            if not fal_key:
                return urls
            headers = {"Authorization": f"Key {fal_key}", "Content-Type": "application/json"}
            payload = {"prompt": prompt, "num_images": max(1, min(count, 3)), "output_format": self.output_format}
            resp = requests.post("https://fal.run/fal-ai/nano-banana", headers=headers, json=payload, timeout=300)
            if resp.status_code != 200:
                logger.error(f"fal nano-banana error: {resp.status_code} {resp.text}")
                return urls
            data = resp.json()
            images = data.get("images") or []
            for img in images:
                u = img.get("url")
                if isinstance(u, str) and u:
                    urls.append(u)
        except Exception:
            logger.exception("Error generating multiple logos")
        return urls


    def list_available_styles(self) -> List[str]:
        """Expose available styles for UI/agent."""
        try:
            return list(STYLE_GUIDE.keys())
        except Exception:
            return ["Minimalist"]


    def run(self):
        """
        Create 3 logos (creation) or 1 edited logo (editing) following design requirements.
        """
        logger.info(f"Starting logo {'edit' if self.editing else 'creation'} for brand: {self.brand_name}")
        try:
            # Check if FAL_KEY is set
            if not os.getenv("FAL_KEY"):
                logger.error("FAL_KEY environment variable is not set")
                return {
                    "status": "error",
                    "message": "FAL_KEY environment variable is not set. Please set it to use the LogoGenerator tool.",
                }

            # Resolve session id
            session_id = self.session_id or _get_session_id_from_headers()

            # Helper: ensure output dir
            import time as _time
            outdir = self.outdir or os.path.join(os.getcwd(), "outputs", "logos", str(int(_time.time())))
            os.makedirs(outdir, exist_ok=True)

            # Load design guidelines (creation mode only, for AI-picked styling)
            design_guidelines = None
            if not self.editing:
                if not self.social_media_username:
                    discovered = _discover_latest_username_for_session()
                    if discovered:
                        self.social_media_username = discovered
                        logger.info(f"Discovered username for session: {self.social_media_username}")
                if self.social_media_username:
                    logger.info(f"Loading design guidelines for username: {self.social_media_username}")
                    analysis = _load_social_media_analysis(self.social_media_username)
                    if analysis:
                        design_guidelines = self._extract_design_guidelines(analysis)
                        logger.info("Loaded design guidelines from cache")
                    else:
                        logger.warning("Failed to load design guidelines from analysis cache")

                # Apply palette override if present for this session
                try:
                    override = self._load_palette_override(session_id)
                    if isinstance(override, dict):
                        pal = override.get("palette") if isinstance(override.get("palette"), list) else None
                        if pal:
                            if not design_guidelines:
                                design_guidelines = {}
                            design_guidelines["color_palette"] = [str(x) for x in pal if isinstance(x, str)]
                            roles = override.get("roles") if isinstance(override.get("roles"), list) else []
                            if roles:
                                # Attach roles text to be used later in prompt
                                design_guidelines["color_roles_text"] = ", ".join([
                                    f"{(r.get('role') or 'unspecified')}={r.get('hex')}" for r in roles if isinstance(r, dict)
                                ])
                            logger.info("Applied palette override from session cache")
                except Exception:
                    pass

            # Build prompt(s)
            if self.editing:
                if not self.prompt or not self.prompt.strip():
                    return {"status": "error", "message": "Editing requires a non-empty prompt describing the changes"}
                # Build an editing-focused prompt (no cache guidelines)
                final_prompt = (
                    "You are an expert logo designer editing an existing logo. PRIORITIZE the user's change request. "
                    "Only modify what the user asks for; preserve other elements. Maintain vector-like clarity, high legibility, "
                    "proper spacing, and clean transparent background.\n\n"
                    f"USER REQUEST: {self.prompt}"
                )
            else:
                # If no design guidelines available, require a user/agent-provided prompt
                if not design_guidelines and not (self.prompt and self.prompt.strip()):
                    return {
                        "status": "error",
                        "message": "No social media analysis available. Please provide a 'prompt' describing the logo to generate.",
                    }

                # If user provided a prompt and no guidelines, generate all 3 from that prompt
                if (self.prompt and self.prompt.strip()) and not design_guidelines:
                    base = (
                        "Design a professional, brandable LOGO with: clean negative space, legible typography, cohesive icon-wordmark relation, "
                        "high contrast, minimal gradients or textures, and a transparent background. Vector-like clarity."
                    )
                    name_part = f" Brand name: {self.brand_name}." if self.brand_name else ""
                    final = f"{base}{name_part} USER REQUIREMENTS: {self.prompt.strip()}"
                    prompts_per_style: List[Tuple[str, int, str]] = [(final, 3, "UserPrompt")]
                else:
                    # Build style-specific prompts using guidelines; either user-selected styles or AI-picked styles
                    requested_total = 3
                    selected_styles: List[str] = []
                    if self.styles:
                        for s in self.styles[:3]:
                            if s in STYLE_GUIDE:
                                selected_styles.append(s)
                    if not selected_styles:
                        selected_styles = list(STYLE_GUIDE.keys())[:3]

                    n = max(1, len(selected_styles))
                    base_count = requested_total // n
                    remainder = requested_total % n
                    per_style_counts: List[int] = []
                    for i in range(n):
                        per_style_counts.append(base_count + (1 if i < remainder else 0))

                    prompts_per_style = []  # type: ignore[assignment]
                    for style_name, count in zip(selected_styles, per_style_counts):
                        style = STYLE_GUIDE.get(style_name, {})
                        style_prompt = style.get("prompt", "")
                        style_guide = style.get("style_guide", "")
                        base = (
                            "Design a professional, brandable LOGO with: clean negative space, legible typography, cohesive icon-wordmark relation, "
                            "high contrast, minimal gradients or textures, and a transparent background. Vector-like clarity."
                        )
                        name_part = f" Brand name: {self.brand_name}." if self.brand_name else ""
                        user_part = f" USER REQUIREMENTS (highest priority): {self.prompt}." if (self.prompt and self.prompt.strip()) else ""
                        # Ensure we output HEX codes directly
                        try:
                            palette_list = [
                                str(c).upper() if isinstance(c, str) else str(c)
                                for c in (design_guidelines.get('color_palette', []) or [])
                            ]
                        except Exception:
                            palette_list = []
                        dg = (
                            f" Archetype: {design_guidelines.get('archetype', 'N/A')}. "
                            f"Tone: {', '.join(design_guidelines.get('tone_words', []))}. "
                            f"Style Keywords: {', '.join(design_guidelines.get('style_keywords', []))}. "
                            f"HEX Palette: {', '.join(palette_list)}. "
                            f"Typography: {design_guidelines.get('typography', 'Modern sans-serif')}"
                        )
                        roles_text = design_guidelines.get("color_roles_text")
                        if roles_text:
                            dg += f" Roles: {roles_text}."
                        design_part = f" DESIGN CONTEXT: {dg}."
                        style_part = f" STYLE TEMPLATE: {style_name} — {style_prompt} | GUIDE: {style_guide}."
                        prompts_per_style.append((f"{base}{name_part}{user_part}{design_part}{style_part}", count, style_name))

            # Creation vs Editing execution
            if not self.editing:
                # Always generate exactly THREE outputs
                results: List[Dict[str, str]] = []
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    # prompts_per_style is defined in creation branch
                    for prompt, count, style_name in prompts_per_style:  # type: ignore[name-defined]
                        urls = loop.run_until_complete(self._generate_logo_multi(prompt, count))
                        for u in urls:
                            # Download
                            r = requests.get(u, timeout=300)
                            r.raise_for_status()
                            safe_name = (self.brand_name or "logo").replace(" ", "_")
                            # style slug for filename
                            style_slug = style_name.replace(" ", "_")
                            out_path = os.path.join(outdir, f"{safe_name}_{session_id}_{style_slug}.png" if self.output_format == "png" else f"{safe_name}_{session_id}_{style_slug}.jpg")
                            # Ensure unique filename if multiple for same style
                            idx = 1
                            base_out = out_path
                            while os.path.exists(out_path):
                                root, ext = os.path.splitext(base_out)
                                out_path = f"{root}_{idx}{ext}"
                                idx += 1
                            with open(out_path, "wb") as f:
                                f.write(r.content)

                            # Save history (creation)
                            self._save_logo_history(session_id=session_id, image_path=out_path, prompt=prompt, is_edit=False)
                            # Derive public URL for FastAPI static serving
                            outputs_root = os.path.realpath(os.path.join(os.getcwd(), "outputs"))
                            real = os.path.realpath(out_path)
                            try:
                                rel = os.path.relpath(real, outputs_root)
                            except ValueError:
                                rel = os.path.basename(real)
                            public_path = f"/outputs/{rel.replace(os.sep, '/')}"
                            # 'public_url' should be the fal URL for display; 'image_url' should be local path for edits
                            results.append({"style": style_name, "public_url": u, "image_url": out_path})
                finally:
                    loop.close()

                if len(results) < 3:
                    return {"status": "error", "message": "Failed to generate three logo images"}

                # Save BRAND_NAME only (defer LOGO_URL until user picks, saved by DirectLabelOnRecipientTool)
                try:
                    if self.brand_name:
                        if ensure_contact:
                            ec = ensure_contact(
                                email=f"{(self.social_media_username or 'brand')}_brand@example.com",
                                first_name=self.brand_name,
                                custom_fields_by_symbol={"BRAND_NAME": self.brand_name},
                                tags=["aaas", "logo-generation"],
                            )
                            contact_id = (ec.get("contact") or {}).get("id") or ec.get("contact_id")
                        else:
                            res = upsert_contact_with_fields(
                                email=f"{(self.social_media_username or 'brand')}_brand@example.com",
                                first_name=self.brand_name,
                                custom_fields_by_symbol={"BRAND_NAME": self.brand_name},
                                tags=["aaas", "logo-generation"],
                            )
                            contact_id = (res.get("contact") or {}).get("id") or res.get("id")
                except Exception as e:
                    logger.warning(f"HighLevel save (brand only) skipped: {e}")

                return {
                    "status": "success",
                    "mode": "creation",
                    "styles": results,
                    "design_guidelines_used": bool(design_guidelines),
                    "username_used": self.social_media_username,
                    "contact_id": locals().get("contact_id"),
                }
            else:
                # Edit last logo
                source_path: str | None = None
                if self.edit_logo_input:
                    if self.edit_logo_input.startswith("http://") or self.edit_logo_input.startswith("https://"):
                        # Download to temp file
                        r = requests.get(self.edit_logo_input, timeout=300)
                        r.raise_for_status()
                        tmp_dir = os.path.join(outdir, "_edit_src")
                        os.makedirs(tmp_dir, exist_ok=True)
                        source_path = os.path.join(tmp_dir, "source_logo.png")
                        with open(source_path, "wb") as f:
                            f.write(r.content)
                    elif os.path.exists(self.edit_logo_input):
                        source_path = self.edit_logo_input
                if not source_path:
                    source_path = self._get_last_logo_image(session_id)
                if not source_path or not os.path.exists(source_path):
                    return {"status": "error", "message": "No existing logo found to edit. Provide 'edit_logo_input' or generate a logo first."}

                # Convert to data URI preserving alpha
                img_data_uri = self._to_data_uri_from_file_preserve_alpha(source_path, max_height=1024)  # type: ignore[arg-type]
                headers = {"Authorization": f"Key {os.getenv('FAL_KEY')}", "Content-Type": "application/json"}
                payload = {"prompt": final_prompt, "image_urls": [img_data_uri], "num_images": 1, "output_format": "png"}
                resp = requests.post("https://fal.run/fal-ai/nano-banana/edit", headers=headers, json=payload, timeout=300)
                if resp.status_code != 200:
                    return {"status": "error", "message": f"fal edit error: {resp.status_code} {resp.text}"}
                data = resp.json()
                images = data.get("images") or []
                if not images or not images[0].get("url"):
                    return {"status": "error", "message": "fal edit returned no image url"}
                image_url = images[0]["url"]

                r = requests.get(image_url, timeout=300)
                r.raise_for_status()
                safe_name = (self.brand_name or "logo").replace(" ", "_")
                # Determine next edit number
                next_edit = self._next_edit_number(session_id)
                out_path = os.path.join(outdir, f"{safe_name}_{session_id}_logo_edit_{next_edit}.png")
                with open(out_path, "wb") as f:
                    f.write(r.content)

                # Save history (edit)
                self._save_logo_history(session_id=session_id, image_path=out_path, prompt=final_prompt, is_edit=True)

                # Upload edited logo to GHL and update LOGO_URL on contact (prefer cached contact id)
                try:
                    token = os.getenv("HIGHLEVEL_ACCESS_TOKEN") or os.getenv("HIGHLEVEL_TOKEN") or os.getenv("GHL_TOKEN")
                    location_id = os.getenv("HIGHLEVEL_LOCATION_ID") or os.getenv("GHL_LOCATION_ID")
                    if token and location_id:
                        from wizard_designer.utils.highlevel_client import _derive_session_key, _load_cached_contact  # type: ignore
                        skey = _derive_session_key()
                        cached = _load_cached_contact(skey)
                        contact_id = (cached or {}).get("id")
                        if contact_id:
                            ghl_url = None
                            try:
                                if upload_media:
                                    up_res = upload_media(out_path, filename=os.path.basename(out_path))
                                    if up_res.get("ok"):
                                        ghl_url = up_res.get("url") or ghl_url
                            except Exception:
                                pass
                            # Ensure field id
                            fid = None
                            try:
                                if get_or_create_custom_field_id:
                                    fid = get_or_create_custom_field_id("LOGO_URL")
                            except Exception:
                                pass
                            if not fid and _resolve_field_ids:
                                try:
                                    fid = _resolve_field_ids().get("LOGO_URL")
                                except Exception:
                                    pass
                            # Fallback value: fal URL if upload failed
                            value_to_save = ghl_url or image_url
                            if fid and value_to_save:
                                payload = {"tags": ["aaas", "logo-generation"], "customFields": [{"id": fid, "value": value_to_save}]}
                                headers = {
                                    "Authorization": f"Bearer {token}",
                                    "Accept": "application/json",
                                    "Content-Type": "application/json",
                                    "Version": "2021-07-28",
                                    "LocationId": location_id,
                                }
                                requests.put(f"{API_BASE}/contacts/{contact_id}", headers=headers, json=payload, timeout=30)
                except Exception as e:
                    logger.warning(f"HighLevel save (logo edit) skipped: {e}")

                # Derive public URL
                outputs_root = os.path.realpath(os.path.join(os.getcwd(), "outputs"))
                real = os.path.realpath(out_path)
                try:
                    rel = os.path.relpath(real, outputs_root)
                except ValueError:
                    rel = os.path.basename(real)
                public_path = f"/outputs/{rel.replace(os.sep, '/')}"

                # Expose fal URL for viewing; local path for editing
                return {
                    "status": "success",
                    "mode": "editing",
                    "public_url": image_url,
                    "image_url": out_path,
                    "edit_number": next_edit,
                    "prompt_used": final_prompt,
                }

        except Exception as e:
            logger.exception(f"Error in logo generation process: {str(e)}")
            return {
                "status": "error",
                "message": f"Error generating logo previews: {str(e)}",
            }

    def _logo_history_files(self, session_id: str) -> tuple[str, str]:
        metadata_dir = os.path.join(os.getcwd(), "cache", "generated_images")
        os.makedirs(metadata_dir, exist_ok=True)
        history_file = os.path.join(metadata_dir, f"LOGO_{session_id}_history.json")
        latest_file = os.path.join(metadata_dir, f"LOGO_{session_id}_latest.json")
        return history_file, latest_file

    def _save_logo_history(self, *, session_id: str, image_path: str, prompt: str, is_edit: bool) -> None:
        import time as _time
        history_file, latest_file = self._logo_history_files(session_id)
        history = []
        try:
            if os.path.exists(history_file):
                with open(history_file, "r") as f:
                    history = json.load(f)
        except Exception:
            history = []
        new_entry = {
            "image_path": image_path,
            "prompt": prompt,
            "timestamp": _time.time(),
            "created_at": _time.strftime("%Y-%m-%d %H:%M:%S"),
            "is_edit": is_edit,
            "edit_number": len([h for h in history if h.get("is_edit", False)]) + 1 if is_edit else 0,
            "session_id": session_id,
        }
        history.append(new_entry)
        if len(history) > 20:
            history = history[-20:]
        try:
            with open(history_file, "w") as f:
                json.dump(history, f, indent=2)
            with open(latest_file, "w") as f:
                json.dump(new_entry, f, indent=2)
        except Exception:
            pass

    def _get_last_logo_image(self, session_id: str) -> str | None:
        history_file, latest_file = self._logo_history_files(session_id)
        try:
            if os.path.exists(latest_file):
                with open(latest_file, "r") as f:
                    latest = json.load(f)
                    return latest.get("image_path")
            if os.path.exists(history_file):
                with open(history_file, "r") as f:
                    history = json.load(f)
                    if history:
                        return history[-1].get("image_path")
        except Exception:
            return None
        return None

    def _next_edit_number(self, session_id: str) -> int:
        history_file, _ = self._logo_history_files(session_id)
        try:
            if os.path.exists(history_file):
                with open(history_file, "r") as f:
                    history = json.load(f)
                    edits = [h for h in history if h.get("is_edit", False)]
                    if not edits:
                        return 1
                    nums = [int(e.get("edit_number", 0)) for e in edits if isinstance(e.get("edit_number", 0), int)]
                    return (max(nums) if nums else 0) + 1
        except Exception:
            return 1
        return 1

    def _to_data_uri_from_file_preserve_alpha(self, path: str, max_height: int = 1024) -> str:
        import PIL.Image as PILImage
        img = PILImage.open(path)
        has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
        if not has_alpha:
            img = img.convert("RGB")
        if img.height > max_height:
            new_w = int(img.width * (max_height / img.height))
            img = img.resize((max(1, new_w), max_height), PILImage.LANCZOS)
        buf = __import__("io").BytesIO()
        import base64
        if has_alpha or str(path).lower().endswith(".png"):
            img.save(buf, format="PNG")
            mime = "image/png"
        else:
            img.save(buf, format="JPEG", quality=92)
            mime = "image/jpeg"
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        return f"data:{mime};base64,{b64}"


if __name__ == "__main__":
    # Set logging level for the test
    print("Running LogoGenerator test")

    # Test the LogoGenerator
    generator = LogoGenerator(
        # brand_name="CoolTech",
        # Uncomment to test a specific style
        style_name=["Playful", "Urban Chic"],
        image_size="square",
        safety_tolerance=4,
        output_format="jpeg",
    )

    print("Executing LogoGenerator run method")
    result = generator.run()

    if result["status"] == "success":
        print(f"Successfully generated {len(result['styles'])} logo style previews")
        for style_name, style_data in result["styles"].items():
            print(f"  - {style_name}: {style_data['url']}")
    else:
        print(f"Error: {result['message']}")

    print("LogoGenerator test complete")
