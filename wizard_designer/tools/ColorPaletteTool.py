from agency_swarm.tools import BaseTool
from pydantic import Field
import os
import json
import time
import logging
from typing import List, Dict, Optional


def setup_logger(name: str) -> logging.Logger:
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
    try:
        headers_to_check = [
            'X-Chat-Id',
            'X-User-Id',
            'X-Agent-Id',
            'X-Chat-ID', 'X-ChatId',
            'X-User-ID', 'X-UserId',
            'X-Session-Id', 'X-Session-ID', 'X-SessionId',
            'X-Conversation-Id', 'X-Conversation-ID', 'X-ConversationId',
            'CURSOR_TRACE_ID', 'CURSOR_SESSION_ID', 'CURSOR_CHAT_ID',
            'AGENCY_SESSION_ID', 'AGENCY_CHAT_ID', 'AGENCY_USER_ID',
        ]
        for header in headers_to_check:
            env_var = header.replace('-', '_').upper()
            value = os.getenv(env_var)
            if value:
                return str(value)[:8]
        import uuid
        return str(uuid.uuid4())[:8]
    except Exception:
        import uuid
        return str(uuid.uuid4())[:8]


def _discover_latest_username_for_session() -> Optional[str]:
    try:
        from pathlib import Path
        session_id = _get_session_id_from_headers()
        analysis_dir = Path(__file__).parent.parent / "cache" / "social_media_analysis"
        if not analysis_dir.exists():
            return None
        candidates = sorted(
            analysis_dir.glob(f"*_{session_id}_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            return None
        fname = candidates[0].name
        marker = f"_{session_id}_"
        idx = fname.rfind(marker)
        if idx > 0:
            return fname[:idx]
        return None
    except Exception:
        return None


def _load_social_media_analysis(username: str) -> Optional[dict]:
    try:
        from pathlib import Path
        session_id = _get_session_id_from_headers()
        analysis_dir = Path(__file__).parent.parent / "cache" / "social_media_analysis"
        if not analysis_dir.exists():
            return None
        files = list(analysis_dir.glob(f"{username}_{session_id}_*.json"))
        if not files:
            return None
        latest = max(files, key=lambda f: f.stat().st_mtime)
        with open(latest, "r") as f:
            data = json.load(f)
        return data.get("analysis")
    except Exception:
        return None


def _palette_cache_paths(session_id: str) -> Dict[str, str]:
    cache_dir = os.path.join(os.path.dirname(__file__), os.pardir, "cache", "social_media_analysis")
    cache_dir = os.path.abspath(cache_dir)
    os.makedirs(cache_dir, exist_ok=True)
    latest = os.path.join(cache_dir, f"PALETTE_{session_id}_latest.json")
    history = os.path.join(cache_dir, f"PALETTE_{session_id}_history.json")
    return {"dir": cache_dir, "latest": latest, "history": history}


def _normalize_hex(hex_str: str) -> Optional[str]:
    if not isinstance(hex_str, str):
        return None
    s = hex_str.strip().lstrip('#')
    if len(s) not in (3, 6):
        return None
    if any(c not in "0123456789abcdefABCDEF" for c in s):
        return None
    if len(s) == 3:
        s = ''.join([c*2 for c in s])
    return f"#{s.upper()}"


def _auto_palette_from_primary(primary_hex: str, size: int = 5) -> List[str]:
    try:
        import colorsys
        h = primary_hex
        h = _normalize_hex(h) or "#3366FF"
        r = int(h[1:3], 16) / 255.0
        g = int(h[3:5], 16) / 255.0
        b = int(h[5:7], 16) / 255.0
        hls = colorsys.rgb_to_hls(r, g, b)
        base_h, base_l, base_s = hls
        colors: List[str] = []
        shifts = [0.0, 0.08, -0.08, 0.16, -0.16]
        for i in range(min(size, len(shifts))):
            nh = (base_h + shifts[i]) % 1.0
            nl = min(1.0, max(0.0, base_l + (0.10 if i % 2 == 0 else -0.10)))
            ns = min(1.0, max(0.0, base_s + (0.10 if i < 2 else -0.05)))
            rr, gg, bb = colorsys.hls_to_rgb(nh, nl, ns)
            colors.append(f"#{int(rr*255):02X}{int(gg*255):02X}{int(bb*255):02X}")
        # Ensure primary is first
        if colors and colors[0] != _normalize_hex(primary_hex):
            colors[0] = _normalize_hex(primary_hex) or colors[0]
        return colors
    except Exception:
        return [(_normalize_hex(primary_hex) or "#3366FF"), "#FF6B6B", "#FFD93D", "#6BCB77", "#4D96FF"]


class ColorPaletteTool(BaseTool):
    """
    Generate a color palette preview as individual swatches and a combined strip image.
    - If colors/roles are not provided, loads suggestions from SocialMediaAnalyzer cache (if available).
    - Can auto-generate a palette from a single primary color.
    - Saves outputs under outputs/palettes/<timestamp>/ and returns public-servable paths.
    - Optionally persists the finalized palette to a session-scoped cache override for downstream tools.
    """

    primary: Optional[str] = Field(None, description="Primary color hex (e.g., #3366FF). Used when auto-generating.")
    colors: Optional[List[str]] = Field(None, description="Explicit list of hex colors to render.")
    roles: Optional[List[Dict[str, str]]] = Field(
        None,
        description="Optional roles per color, e.g., [{hex, role}] where role in {primary, secondary, accent, neutral}",
    )
    swatch_size: int = Field(512, description="Size in pixels of square swatches.")
    session_id: Optional[str] = Field(None, description="Override session id; auto-derived otherwise.")
    save_override: bool = Field(True, description="If true, save the palette as an override for this session.")
    social_media_username: Optional[str] = Field(None, description="Optional username to load social cache from.")

    def _resolve_session(self) -> str:
        return self.session_id or _get_session_id_from_headers()

    def _resolve_palette(self) -> Dict[str, List[Dict[str, str]] | List[str]]:
        # Try explicit inputs first
        normalized: List[str] = []
        roles: List[Dict[str, str]] = []

        if isinstance(self.colors, list) and self.colors:
            for c in self.colors:
                h = _normalize_hex(c)
                if h:
                    normalized.append(h)

        if isinstance(self.roles, list) and self.roles:
            for r in self.roles:
                h = _normalize_hex(r.get("hex")) if isinstance(r, dict) else None
                role = (r.get("role") or "").strip().lower() if isinstance(r, dict) else ""
                if h:
                    roles.append({"hex": h, "role": role or "unspecified"})
                    if h not in normalized:
                        normalized.append(h)

        # If nothing provided, try social media analysis cache
        if not normalized:
            username = self.social_media_username or _discover_latest_username_for_session()
            if username:
                analysis = _load_social_media_analysis(username) or {}
                bdg = analysis.get("brand_design_guidance") or {}
                palette = bdg.get("color_palette_hex") or []
                if isinstance(palette, list):
                    for c in palette:
                        h = _normalize_hex(c)
                        if h and h not in normalized:
                            normalized.append(h)
                # roles
                roles_list = bdg.get("color_roles") or []
                if isinstance(roles_list, list):
                    for r in roles_list:
                        if not isinstance(r, dict):
                            continue
                        h = _normalize_hex(r.get("hex"))
                        role = (r.get("role") or "").strip().lower()
                        if h:
                            roles.append({"hex": h, "role": role or "unspecified"})

        # If still empty, auto-generate from primary or default
        if not normalized:
            if self.primary:
                normalized = _auto_palette_from_primary(self.primary, 5)
            else:
                normalized = ["#0B0F19", "#1F3B73", "#3366FF", "#6BCB77", "#FFD93D"]

        # Deduplicate preserving order
        seen = set()
        unique = []
        for c in normalized:
            if c not in seen:
                unique.append(c)
                seen.add(c)

        return {"palette": unique, "roles": roles}

    def _render_images(self, palette: List[str]) -> Dict[str, List[Dict[str, str]] | str]:
        from PIL import Image, ImageDraw

        outdir = os.path.join(os.getcwd(), "outputs", "palettes", str(int(time.time())))
        os.makedirs(outdir, exist_ok=True)

        outputs: List[Dict[str, str]] = []

        # Individual swatches
        for hex_color in palette:
            sw = Image.new("RGB", (self.swatch_size, self.swatch_size), color=hex_color)
            safe = hex_color.replace('#', '')
            sw_path = os.path.join(outdir, f"swatch_{safe}.png")
            sw.save(sw_path)
            # Derive public path under /outputs for FastAPI static serving
            outputs_root = os.path.realpath(os.path.join(os.getcwd(), "outputs"))
            real = os.path.realpath(sw_path)
            try:
                rel = os.path.relpath(real, outputs_root)
            except ValueError:
                rel = os.path.basename(real)
            public_path = f"/outputs/{rel.replace(os.sep, '/')}"
            # For palettes, just keep local path; no external fal URL involved
            outputs.append({"kind": "swatch", "hex": hex_color, "output_path": sw_path})

        return {"outdir": outdir, "outputs": outputs}

    def _save_override(self, session_id: str, palette: List[str], roles: List[Dict[str, str]]) -> None:
        try:
            paths = _palette_cache_paths(session_id)
            record = {
                "session_id": session_id,
                "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "palette": palette,
                "roles": roles,
            }
            # Write latest
            with open(paths["latest"], "w") as f:
                json.dump(record, f, indent=2)
            # Append to history
            hist: List[dict] = []
            if os.path.exists(paths["history"]):
                try:
                    with open(paths["history"], "r") as f:
                        hist = json.load(f)
                        if not isinstance(hist, list):
                            hist = []
                except Exception:
                    hist = []
            hist.append(record)
            if len(hist) > 20:
                hist = hist[-20:]
            with open(paths["history"], "w") as f:
                json.dump(hist, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed saving palette override: {e}")

    def run(self):
        try:
            session_id = self._resolve_session()
            resolved = self._resolve_palette()
            palette = [c for c in resolved.get("palette", []) if isinstance(c, str)]  # type: ignore[arg-type]
            roles = [r for r in resolved.get("roles", []) if isinstance(r, dict)]     # type: ignore[arg-type]

            if not palette:
                return {"status": "error", "message": "No colors could be resolved to render"}

            render = self._render_images(palette)

            if self.save_override:
                self._save_override(session_id, palette, roles)

            # Return as a list so API can attach public URLs for each image
            result: List[Dict] = []
            # Meta first (no output URL expected)
            result.append({"kind": "meta", "session_id": session_id, "palette": palette, "roles": roles})
            # Images
            result.extend(render.get("outputs", []))
            return result
        except Exception as e:
            logger.exception("ColorPaletteTool failed")
            return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    print("Running ColorPaletteTool test...")
    tool = ColorPaletteTool(
        primary="#3366FF",
        colors=None,
        roles=None,
        swatch_size=64,
        save_override=True,
    )
    out = tool.run()
    print(json.dumps(out, indent=2) if isinstance(out, dict) else out)


