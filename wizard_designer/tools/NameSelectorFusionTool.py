from agency_swarm.tools import BaseTool
from pydantic import Field
from typing import Optional, Dict, List, Any
import logging
import json
import time
import os
import openai
import requests
import whois

# Local lightweight logger
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


class NameSelectorFusionTool(BaseTool):
    """
    A fusion tool that combines brand name generation and domain validation to iteratively
    find the best brand name based on social media analysis and validation results.
    
    This tool works in a loop:
    1. Generates a large list of name ideas using GPT directly
    2. Validates each name using direct domain validation
    3. Ranks names by viability score
    4. Returns the best name with style information
    """

    social_media_data: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Social media analysis data containing style preferences, aesthetic themes, etc. If not provided, it will be auto-loaded from cache using session headers."
    )
    
    social_media_username: Optional[str] = Field(
        default=None, description="Social media username to load analysis from (e.g., 'choi3an')"
    )
    
    max_names_to_generate: int = Field(
        default=20,
        description="Maximum number of names to generate in the initial batch"
    )
    
    max_iterations: int = Field(
        default=3,
        description="Maximum number of iterations to find a good name"
    )
    
    min_viability_score: float = Field(
        default=7.0,
        description="Minimum viability score required to accept a name"
    )
    
    # Always using GPT for name generation now
    
    celebrity_reference: Optional[str] = Field(
        default=None,
        description="Optional celebrity name to inspire the naming style"
    )
    
    # Optional guidance from user/agent to bias naming choices
    user_naming_insights: Optional[str] = Field(
        default=None,
        description="Optional short guidance to bias naming (tone, motifs, do/don't). Agent-controlled and only used when explicitly provided."
    )
    
    def __init__(self, **data: Any):
        """Initialize and auto-load minimal social media context from cache when missing.
        This reduces prompt size and reuses prior analysis bound to X-Chat-Id.
        """
        super().__init__(**data)
        try:
            if not self.social_media_data:
                minimal_context = self._auto_load_social_context()
                if minimal_context:
                    self.social_media_data = minimal_context
                    logger.info("Auto-loaded minimal social media context from cache for naming tool")
        except Exception as e:
            logger.warning(f"Failed to auto-load social context: {e}")
    
    def _get_session_id_from_headers(self) -> str:
        """Extract session ID from agency headers or generate a new one."""
        try:
            import os
            
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

    def _auto_load_social_context(self) -> Optional[Dict[str, Any]]:
        """Load minimal social media context from cache using session-aware filenames.
        Only pulls the fields needed for naming: platform and compact profile.
        Also discovers the latest username for this session when not provided.
        """
        try:
            from pathlib import Path
            import json as _json
            import re as _re

            session_id = self._get_session_id_from_headers()
            base_dir = Path(__file__).parent.parent

            # Prefer provided username; otherwise infer the latest one for this session
            username: Optional[str] = self.social_media_username
            if not username:
                analysis_dir = base_dir / "cache" / "social_media_analysis"
                if analysis_dir.exists():
                    # Match files like: {username}_{session}_{timestamp}.json (username may contain underscores)
                    candidates = sorted(analysis_dir.glob(f"*_{session_id}_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
                    if candidates:
                        fname = candidates[0].name
                        marker = f"_{session_id}_"
                        idx = fname.rfind(marker)
                        if idx > 0:
                            username = fname[:idx]
                            # Persist discovered username for downstream analysis loading
                            try:
                                self.social_media_username = username
                            except Exception:
                                pass

            if not username:
                logger.info("No username found to load social context for this session")
                return None

            # Read compact profile/platform from SocialMediaAnalyzer cache
            analyzer_cache = base_dir / "cache" / "social_media_analyzer" / f"{username}_{session_id}.json"
            platform = "unknown"
            profile_compact: Dict[str, Any] = {}
            if analyzer_cache.exists():
                try:
                    with open(analyzer_cache, "r", encoding="utf-8") as f:
                        stored = _json.load(f)
                    payload = stored.get("data", stored) if isinstance(stored, dict) else {}
                    platform = payload.get("platform", platform)
                    prof = payload.get("profile", {}) or {}
                    profile_compact = {
                        "fullName": prof.get("fullName", ""),
                        "bio": prof.get("bio", ""),
                        "businessCategoryName": prof.get("businessCategoryName", ""),
                        "verified": prof.get("verified", False),
                        "followersCount": prof.get("followersCount", 0),
                    }
                except Exception as e:
                    logger.warning(f"Failed reading analyzer cache: {e}")

            # Construct minimal social context; do NOT include heavy analysis blob
            minimal_context = {
                "platform": platform,
                "profile": profile_compact,
            }
            return minimal_context
        except Exception as e:
            logger.warning(f"Error loading minimal social context: {e}")
            return None

    def _load_social_media_analysis(self, username: str) -> Optional[Dict]:
        """Load the latest social media analysis for the given username from current session"""
        try:
            import json
            import glob
            from pathlib import Path
            
            # Get current session ID
            session_id = self._get_session_id_from_headers()
            
            # Find the analysis directory
            current_dir = Path(__file__).parent
            # Go up one level to tools directory, then to cache
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

    def _load_name_generation_prompt(self) -> str:
        """Load the name generation prompt from the prompts folder"""
        try:
            prompt_path = os.path.join(os.path.dirname(__file__), "prompts", "name_generation_prompt.txt")
            with open(prompt_path, 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            logger.warning("Prompt file not found, using default prompt")
            return self._get_default_prompt()
    
    def _get_default_prompt(self) -> str:
        """Fallback default prompt if file loading fails"""
        return """You are a creative brand naming expert specializing in supplement industry naming conventions.

Generate {num_names} unique brand names for a supplement company, that are unlikely to already exist online, to make it SEO friendly.

Style Context: {style_context}

Previous Names Generated: {previous_names}

Social Media Profile Analysis:
- Influencer/Profile Name: {influencer_name}
- Bio: {bio_text}
- Business Category: {business_category}
- Verified Status: {verified_status}
- Follower Count: {follower_count}
- Platform: {platform}

Requirements:
- Blend two components (e.g., 'VitaNova Formula', 'Truva Nutra')
- Always give some names playing with latin words. (Not all names)
- Make sure names aren't super long, they must be short conscise, easy to pronounce.
- If the style context is celebrity, make sure the names are inspired by the celebrity and not just the style
- Match creativity of these examples: 'Enigma Mage', 'Odyssey Uncharted', 'Nova Cloud'— but avoid similar sounding patterns or repeated themes
- Generate unique names that are different from previously generated names
- Suitable for supplement industry (vitamins, protein, wellness, fitness, health, nutrition, supplements)
- Ensure each name feels brandable, and search engine friendly with **low search competition** potential (unique phrasing that won't surface many results on Google)
- Names should reflect the aesthetic theme and brand voice from the style context
- Consider the interests when crafting names
- Take inspiration from the influencer's profile, bio, and social media presence
- Consider the business category and target audience based on follower count and verification status

Aesthetic Theme: {aesthetic_theme}
Brand Voice: {brand_voice}
Interests: {interests}

Format: Return only the names, one per line, with no additional text or formatting."""

    def _extract_personalization_data(self, social_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract detailed personalization data for name generation.
        """
        logger.info("Extracting personalization data for name generation")
        
        personalization_data = {
            "name_elements": [],
            "interest_keywords": [],
            "personality_traits": [],
            "lifestyle_themes": [],
            "bio_keywords": [],
            "cultural_elements": []
        }
        
        # Extract name elements
        influencer_name = social_data.get("profile", {}).get("fullName", "")
        username = self.social_media_username or ""
        
        if influencer_name:
            # Split name into parts for potential use
            name_parts = influencer_name.lower().split()
            personalization_data["name_elements"] = name_parts
            
        if username:
            # Extract potential syllables or meaningful parts from username
            username_clean = username.lower().replace("_", "").replace(".", "")
            personalization_data["name_elements"].append(username_clean)
        
        # Extract bio keywords
        bio = social_data.get("profile", {}).get("bio", "").lower()
        if bio:
            # Extract meaningful words from bio
            bio_words = bio.split()
            meaningful_words = [word for word in bio_words if len(word) > 3 and word.isalpha()]
            personalization_data["bio_keywords"] = meaningful_words[:10]  # Top 10 words
        
        # Extract interests from hashtags and analysis
        all_hashtags = []
        for post in social_data.get("posts", []):
            hashtags = post.get("hashtags", [])
            if isinstance(hashtags, list):
                all_hashtags.extend([tag.lower().replace("#", "") for tag in hashtags])
        
        # Categorize interests
        interest_categories = {
            "fitness": ["fitness", "gym", "workout", "health", "wellness", "strength", "cardio"],
            "nature": ["nature", "organic", "natural", "eco", "green", "sustainable", "plant"],
            "lifestyle": ["lifestyle", "travel", "adventure", "explore", "life", "daily"],
            "beauty": ["beauty", "skincare", "glow", "radiant", "beautiful", "aesthetic"],
            "food": ["food", "nutrition", "healthy", "cooking", "recipe", "meal", "diet"],
            "tech": ["tech", "innovation", "ai", "digital", "future", "smart"],
            "sea": ["ocean", "sea", "beach", "surf", "marine", "wave", "blue", "aqua"],
            "mountain": ["mountain", "peak", "summit", "hike", "trail", "altitude"],
            "spiritual": ["spiritual", "mindful", "zen", "meditation", "peace", "balance"]
        }
        
        for category, keywords in interest_categories.items():
            if any(keyword in " ".join(all_hashtags) for keyword in keywords):
                personalization_data["interest_keywords"].append(category)
        
        # Extract personality traits from analysis
        analysis = social_data.get("analysis", {})
        if isinstance(analysis, dict):
            archetype = analysis.get("inferred_archetype", {})
            if archetype and isinstance(archetype, dict):
                archetype_name = archetype.get("name", "").lower()
                personalization_data["personality_traits"].append(archetype_name)
        
        logger.info(f"Extracted personalization data: {personalization_data}")
        return personalization_data

    def _extract_style_from_social_data(self, social_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract style information from SocialMediaAnalyzer output data.
        """
        logger.info("Extracting style information from SocialMediaAnalyzer data")
        
        # First, try to load from saved analysis if username is provided
        if self.social_media_username:
            logger.info(f"Loading style metadata from saved analysis for: {self.social_media_username}")
            saved_analysis = self._load_social_media_analysis(self.social_media_username)
            if saved_analysis:
                # Parse the analysis if it's in raw format
                if isinstance(saved_analysis, dict) and "raw" in saved_analysis:
                    try:
                        import json
                        raw_content = saved_analysis["raw"]
                        # Remove markdown code blocks if present
                        if raw_content.startswith("```json"):
                            raw_content = raw_content[7:]  # Remove ```json
                        if raw_content.endswith("```"):
                            raw_content = raw_content[:-3]  # Remove ```
                        parsed_analysis = json.loads(raw_content.strip())
                        saved_analysis = parsed_analysis
                        logger.info("Successfully parsed raw analysis data")
                    except Exception as e:
                        logger.warning(f"Failed to parse saved analysis: {e}")
                        saved_analysis = {}
                else:
                    logger.info("Using already parsed analysis data")
                
                if saved_analysis:
                    # Extract brand naming guidelines from saved analysis
                    naming_guidelines = saved_analysis.get("brand_naming_guidelines", {})
                    visual_style = saved_analysis.get("visual_style", {})
                    archetype = saved_analysis.get("inferred_archetype", {})
                    
                    # Build style context from analysis
                    style_elements = []
                    if visual_style.get("styling_vibe_tags"):
                        style_elements.extend(visual_style["styling_vibe_tags"])
                    if visual_style.get("common_color_palettes"):
                        style_elements.extend(visual_style["common_color_palettes"])
                    
                    style_context = f"{archetype.get('name', 'influencer')} with {', '.join(style_elements[:5])}" if style_elements else f"{archetype.get('name', 'influencer')}"
                    
                    # Build aesthetic theme from visual analysis
                    aesthetic_theme = "modern wellness"  # default
                    if "luxury" in str(style_elements).lower():
                        aesthetic_theme = "elegant"
                    elif "gym" in str(style_elements).lower() or "fitness" in str(style_elements).lower():
                        aesthetic_theme = "mascot"
                    elif "wellness" in str(style_elements).lower() or "yoga" in str(style_elements).lower():
                        aesthetic_theme = "nature"
                    elif "tech" in str(style_elements).lower() or "modern" in str(style_elements).lower():
                        aesthetic_theme = "futuristic"
                    
                    # Build brand voice from analysis
                    brand_voice = naming_guidelines.get("brand_voice_alignment", "modern, aspirational, trustworthy")
                    
                    # Build interests from archetype
                    interests = ["health", "wellness", "supplements"]
                    archetype_name = archetype.get("name", "").lower()
                    if "gym" in archetype_name or "fitness" in archetype_name:
                        interests = ["fitness", "performance", "strength", "supplements"]
                    elif "beauty" in archetype_name or "lifestyle" in archetype_name:
                        interests = ["beauty", "lifestyle", "wellness", "supplements"]
                    elif "biohacker" in archetype_name:
                        interests = ["biohacking", "productivity", "optimization", "supplements"]
                    
                    # Extract influencer name from the saved analysis or use username as fallback
                    influencer_name = self.social_media_username or "Unknown"
                    
                    return {
                        "aesthetic_theme": aesthetic_theme,
                        "brand_voice": brand_voice,
                        "interests": interests,
                        "style_context": style_context,
                        "naming_guidelines": naming_guidelines,
                        "archetype": archetype,
                        "visual_style": visual_style,
                        "source": "saved_social_media_analysis",
                        "platform": social_data.get("platform", "unknown"),
                        "business_category": social_data.get("profile", {}).get("businessCategoryName", ""),
                        "verified": social_data.get("profile", {}).get("verified", False),
                        "follower_count": social_data.get("profile", {}).get("followersCount", 0),
                        "influencer_name": influencer_name,
                        "bio_text": social_data.get("profile", {}).get("bio", ""),
                        "verified_status": social_data.get("profile", {}).get("verified", False)
                    }
        
        # Initialize default style metadata
        style_metadata = {
            "aesthetic_theme": "minimalist",
            "brand_voice": "professional",
            "interests": [],
            "source": "social_media_analysis",
            "platform": social_data.get("platform", "unknown"),
            "business_category": social_data.get("profile", {}).get("businessCategoryName", ""),
            "verified": social_data.get("profile", {}).get("verified", False),
            "follower_count": social_data.get("profile", {}).get("followersCount", 0),
            "influencer_name": social_data.get("profile", {}).get("fullName", ""),
            "bio_text": social_data.get("profile", {}).get("bio", ""),
            "verified_status": social_data.get("profile", {}).get("verified", False)
        }
        
        # Extract from profile bio
        profile = social_data.get("profile", {})
        bio = profile.get("bio", "").lower()
        
        # Extract from hashtags across posts
        all_hashtags = []
        for post in social_data.get("posts", []):
            hashtags = post.get("hashtags", [])
            if isinstance(hashtags, list):
                all_hashtags.extend([tag.lower().replace("#", "") for tag in hashtags])
        
        # Extract from GPT analysis if available
        gpt_analysis = social_data.get("analysis", {})
        if gpt_analysis and isinstance(gpt_analysis, dict):
            # Extract archetype from GPT analysis
            inferred_archetype = gpt_analysis.get("inferred_archetype", {})
            if inferred_archetype and isinstance(inferred_archetype, dict):
                archetype_name = inferred_archetype.get("name", "").lower()
                if archetype_name:
                    style_metadata["aesthetic_theme"] = self._map_gpt_archetype_to_theme(archetype_name)
            
            # Extract visual style information
            visual_style = gpt_analysis.get("visual_style", {})
            if visual_style and isinstance(visual_style, dict):
                style_metadata["brand_voice"] = self._extract_brand_voice_from_visual_style(visual_style)
                style_metadata["interests"] = self._extract_interests_from_visual_style(visual_style)
        
        # If no GPT analysis, try to infer from hashtags and bio
        if not gpt_analysis:
            style_metadata["aesthetic_theme"] = self._infer_theme_from_hashtags_and_bio(all_hashtags, bio)
            style_metadata["brand_voice"] = self._infer_brand_voice_from_bio(bio)
            style_metadata["interests"] = self._extract_interests_from_hashtags(all_hashtags)
        
        logger.info(f"Extracted style from SocialMediaAnalyzer: {style_metadata}")
        return style_metadata

    def _map_gpt_archetype_to_theme(self, archetype_name: str) -> str:
        """
        Map GPT archetype names to our aesthetic themes.
        """
        archetype_mapping = {
            # Gym Bros & Bodybuilders
            "gym bros": "mascot",
            "bodybuilders": "mascot",
            "gym": "mascot",
            "fitness": "mascot",
            
            # Wellness & Yoga Gurus
            "wellness": "nature",
            "yoga": "nature",
            "yoga gurus": "nature",
            "meditation": "nature",
            
            # Biohackers & Productivity
            "biohackers": "futuristic",
            "productivity": "futuristic",
            "nootropics": "futuristic",
            "optimization": "futuristic",
            
            # Beauty & Lifestyle
            "beauty": "elegant",
            "lifestyle": "modern",
            "fashion": "elegant",
            "glamour": "elegant",
            
            # Nutrition & Healthy Cooking
            "nutrition": "nature",
            "cooking": "nature",
            "healthy": "nature",
            "food": "nature",
            
            # Plant-Based & Sustainable
            "plant-based": "nature",
            "sustainable": "nature",
            "vegan": "nature",
            "eco": "nature",
            
            # Functional/CrossFit
            "functional": "mascot",
            "crossfit": "mascot",
            "strength": "mascot",
            "performance": "mascot",
            
            # Science-Based Educators
            "science": "corporate",
            "educators": "corporate",
            "evidence": "corporate",
            "research": "corporate",
            
            # Weight-Loss Coaches
            "weight-loss": "modern",
            "coaches": "modern",
            "transformation": "modern",
            "motivation": "modern",
            
            # Micro/Niche Influencers
            "niche": "minimalist",
            "authentic": "minimalist",
            "relatable": "minimalist",
            
            # Aesthetic Lifestyle Males
            "aesthetic": "elegant",
            "lifestyle males": "modern",
            "status": "elegant"
        }
        
        # Find the best match
        for key, theme in archetype_mapping.items():
            if key in archetype_name.lower():
                return theme
        
        return "minimalist"  # Default fallback

    def _extract_brand_voice_from_visual_style(self, visual_style: Dict[str, Any]) -> str:
        """
        Extract brand voice from GPT visual style analysis.
        """
        styling_vibe = visual_style.get("styling_vibe_tags", [])
        if isinstance(styling_vibe, list):
            vibe_text = " ".join(styling_vibe).lower()
            
            if any(word in vibe_text for word in ["luxury", "premium", "exclusive", "high-end"]):
                return "luxury"
            elif any(word in vibe_text for word in ["casual", "relaxed", "comfortable", "everyday"]):
                return "casual"
            elif any(word in vibe_text for word in ["sophisticated", "elegant", "refined", "classy"]):
                return "sophisticated"
            elif any(word in vibe_text for word in ["edgy", "bold", "daring", "rebellious"]):
                return "edgy"
            elif any(word in vibe_text for word in ["professional", "business", "corporate", "formal"]):
                return "professional"
            elif any(word in vibe_text for word in ["trendy", "fashion-forward", "stylish", "modern"]):
                return "trendy"
        
        return "professional"  # Default

    def _extract_interests_from_visual_style(self, visual_style: Dict[str, Any]) -> List[str]:
        """
        Extract interests from GPT visual style analysis.
        """
        interests = []
        dress_description = visual_style.get("dress_description", "").lower()
        color_palettes = visual_style.get("common_color_palettes", [])
        
        # Extract from dress description
        if "athletic" in dress_description or "sporty" in dress_description:
            interests.extend(["fitness", "sports", "health"])
        if "formal" in dress_description or "business" in dress_description:
            interests.extend(["business", "professional", "career"])
        if "casual" in dress_description or "streetwear" in dress_description:
            interests.extend(["lifestyle", "fashion", "streetwear"])
        if "luxury" in dress_description or "designer" in dress_description:
            interests.extend(["luxury", "fashion", "design"])
        
        # Extract from color palettes
        if isinstance(color_palettes, list):
            for palette in color_palettes:
                if isinstance(palette, str):
                    if "earth" in palette.lower() or "green" in palette.lower():
                        interests.append("nature")
                    if "neon" in palette.lower() or "bright" in palette.lower():
                        interests.append("bold")
                    if "neutral" in palette.lower() or "beige" in palette.lower():
                        interests.append("minimalist")
        
        return list(set(interests))  # Remove duplicates

    def _infer_theme_from_hashtags_and_bio(self, hashtags: List[str], bio: str) -> str:
        """
        Infer aesthetic theme from hashtags and bio text.
        """
        combined_text = f"{bio} {' '.join(hashtags)}".lower()
        
        # Theme detection patterns
        if any(word in combined_text for word in ["minimal", "simple", "clean", "basic"]):
            return "minimalist"
        elif any(word in combined_text for word in ["futuristic", "tech", "digital", "cyber"]):
            return "futuristic"
        elif any(word in combined_text for word in ["elegant", "luxury", "premium", "sophisticated"]):
            return "elegant"
        elif any(word in combined_text for word in ["corporate", "business", "professional", "enterprise"]):
            return "corporate"
        elif any(word in combined_text for word in ["vintage", "retro", "classic", "nostalgic"]):
            return "vintage"
        elif any(word in combined_text for word in ["nature", "organic", "natural", "eco"]):
            return "nature"
        elif any(word in combined_text for word in ["art", "creative", "design", "artistic"]):
            return "abstract"
        elif any(word in combined_text for word in ["fitness", "gym", "health", "wellness"]):
            return "mascot"
        
        return "minimalist"  # Default

    def _infer_brand_voice_from_bio(self, bio: str) -> str:
        """
        Infer brand voice from bio text.
        """
        if any(word in bio for word in ["luxury", "premium", "exclusive", "elite"]):
            return "luxury"
        elif any(word in bio for word in ["casual", "relaxed", "chill", "easy"]):
            return "casual"
        elif any(word in bio for word in ["professional", "business", "corporate", "enterprise"]):
            return "professional"
        elif any(word in bio for word in ["edgy", "bold", "daring", "rebellious"]):
            return "edgy"
        elif any(word in bio for word in ["sophisticated", "elegant", "refined", "classy"]):
            return "sophisticated"
        elif any(word in bio for word in ["trendy", "fashion-forward", "stylish", "modern"]):
            return "trendy"
        
        return "professional"  # Default

    def _extract_interests_from_hashtags(self, hashtags: List[str]) -> List[str]:
        """
        Extract interests from hashtags.
        """
        interests = []
        
        for hashtag in hashtags:
            hashtag_lower = hashtag.lower()
            
            # Fitness & Health
            if any(word in hashtag_lower for word in ["fitness", "gym", "workout", "health", "wellness"]):
                interests.append("fitness")
            
            # Fashion & Beauty
            if any(word in hashtag_lower for word in ["fashion", "style", "beauty", "outfit", "look"]):
                interests.append("fashion")
            
            # Lifestyle & Travel
            if any(word in hashtag_lower for word in ["lifestyle", "travel", "adventure", "explore", "life"]):
                interests.append("lifestyle")
            
            # Business & Career
            if any(word in hashtag_lower for word in ["business", "entrepreneur", "career", "success", "motivation"]):
                interests.append("business")
            
            # Food & Nutrition
            if any(word in hashtag_lower for word in ["food", "nutrition", "healthy", "cooking", "recipe"]):
                interests.append("food")
            
            # Technology & Innovation
            if any(word in hashtag_lower for word in ["tech", "innovation", "ai", "digital", "future"]):
                interests.append("technology")
            
            # Supplements & Wellness
            if any(word in hashtag_lower for word in ["supplements", "vitamins", "protein", "wellness", "natural", "health"]):
                interests.append("supplements")
        
        return list(set(interests))  # Remove duplicates



    def _generate_names_batch(self, style_metadata: Dict[str, Any], existing_names: List[str] = None) -> List[str]:
        """
        Generate a batch of names using GPT directly with enhanced reasoning and personalization.
        """
        logger.info(f"Generating batch of names with style: {style_metadata['aesthetic_theme']}")
        
        try:
            # Check if OpenAI API key is available
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                logger.error("OpenAI API key not found")
                return []
            
            # Check if we have brand naming guidelines from saved analysis
            if style_metadata.get("source") == "saved_social_media_analysis" and style_metadata.get("naming_guidelines"):
                # Use enhanced prompt with brand naming guidelines
                naming_guidelines = style_metadata.get("naming_guidelines", {})
                archetype = style_metadata.get("archetype", {})
                visual_style = style_metadata.get("visual_style", {})
                
                style_context = f"Style: {style_metadata['aesthetic_theme']}"
                if self.celebrity_reference:
                    style_context = f"Celebrity: {self.celebrity_reference}"
                
                previous_names = ", ".join(existing_names or [])
                if not previous_names:
                    previous_names = "None"
                
                # Extract influencer details for personalization
                influencer_name = style_metadata.get('influencer_name', 'N/A')
                bio_text = style_metadata.get('bio_text', 'N/A')
                username = self.social_media_username or 'N/A'
                
                # Get detailed personalization data
                personalization_data = self._extract_personalization_data(self.social_media_data or {})
                
                formatted_prompt = f"""You are a creative brand naming expert who creates personalized supplement brand names that perfectly match the influencer's personality, interests, and characteristics.

Generate {self.max_names_to_generate} CATCHY, PERSONALIZED brand names that feel like they were created specifically for this influencer. Each name should have a clear connection to the influencer's identity.

INFLUENCER PERSONALIZATION CONTEXT:
- Real Name: {influencer_name}
- Username: {username}
- Bio: {bio_text}
- Archetype: {archetype.get('name', 'N/A')} (Confidence: {archetype.get('confidence_0_1', 0):.1%})
- Visual Style: {', '.join(visual_style.get('styling_vibe_tags', [])[:5])}
- Brand Voice: {naming_guidelines.get('brand_voice_alignment', 'N/A')}
- Interests: {', '.join(style_metadata.get('interests', []))}

DETAILED PERSONALIZATION DATA:
- Name Elements (for name play): {', '.join(personalization_data.get('name_elements', [])[:5])}
- Interest Keywords: {', '.join(personalization_data.get('interest_keywords', [])[:5])}
- Personality Traits: {', '.join(personalization_data.get('personality_traits', [])[:3])}
- Bio Keywords: {', '.join(personalization_data.get('bio_keywords', [])[:5])}

PERSONALIZATION STRATEGIES (use at least 2-3 of these):
1. **Name Play**: Extract syllables, sounds, or meanings from their real name or username
2. **Interest Integration**: Incorporate their passions (sea, fitness, nature, tech, etc.)
3. **Personality Traits**: Reflect their archetype and visual style
4. **Bio Elements**: Use keywords, themes, or concepts from their bio
5. **Cultural References**: If they reference specific cultures, locations, or concepts
6. **Lifestyle Alignment**: Match their lifestyle choices and values

REASONING REQUIREMENT:
For each name, think about WHY it's perfect for this specific influencer:
- How does it connect to their name/username?
- What aspect of their personality does it capture?
- How does it reflect their interests or lifestyle?
- Why would their audience connect with this name?

NAMING EXAMPLES WITH REASONING:
- "MOLO" (Rebecca Zamolo) - Plays with "Zamolo" surname, sounds like "mellow" reflecting her calm personality
- "Truvani" (Vani Hari "Food Babe") - Combines "Tru" (truth) with "Vani" (her name), reflecting her clean-food activism
- "Moon Juice" (Amanda Chantal Bacon) - Reflects her mystical, wellness-focused aesthetic and lunar themes
- "Bloom Nutrition" (Mari Llewellyn) - "Bloom" suggests growth/transformation, perfect for her fitness journey content

CRITICAL REQUIREMENTS:
- Names must be CATCHY and MEMORABLE
- Each name should have a clear personal connection to the influencer
- Avoid generic supplement names - make them feel custom-made
- Names should be short, pronounceable, and brandable
- Suitable for supplement industry but with personal flair
- Generate unique names that are different from previously generated names
- Consider SEO potential but prioritize personal connection

Format: Return only the names, one per line."""
            else:
                # Use enhanced default prompt with personalization
                style_context = f"Style: {style_metadata['aesthetic_theme']}"
                if self.celebrity_reference:
                    style_context = f"Celebrity: {self.celebrity_reference}"
                
                previous_names = ", ".join(existing_names or [])
                if not previous_names:
                    previous_names = "None"
                
                # Extract influencer details for personalization
                influencer_name = style_metadata.get('influencer_name', 'Unknown')
                bio_text = style_metadata.get('bio_text', 'No bio available')
                username = self.social_media_username or 'Unknown'
                interests = ", ".join(style_metadata.get('interests', []))
                
                # Get detailed personalization data
                personalization_data = self._extract_personalization_data(self.social_media_data or {})
                
                formatted_prompt = f"""You are a creative brand naming expert who creates personalized supplement brand names that perfectly match the influencer's personality, interests, and characteristics.

Generate {self.max_names_to_generate} CATCHY, PERSONALIZED brand names that feel like they were created specifically for this influencer. Each name should have a clear connection to the influencer's identity.

INFLUENCER PERSONALIZATION CONTEXT:
- Real Name: {influencer_name}
- Username: {username}
- Bio: {bio_text}
- Business Category: {style_metadata.get('business_category', 'Unknown')}
- Verified Status: {style_metadata.get('verified_status', False)}
- Follower Count: {style_metadata.get('follower_count', 0)}
- Platform: {style_metadata.get('platform', 'Unknown')}
- Aesthetic Theme: {style_metadata.get('aesthetic_theme', 'minimalist')}
- Brand Voice: {style_metadata.get('brand_voice', 'professional')}
- Interests: {interests}

DETAILED PERSONALIZATION DATA:
- Name Elements (for name play): {', '.join(personalization_data.get('name_elements', [])[:5])}
- Interest Keywords: {', '.join(personalization_data.get('interest_keywords', [])[:5])}
- Personality Traits: {', '.join(personalization_data.get('personality_traits', [])[:3])}
- Bio Keywords: {', '.join(personalization_data.get('bio_keywords', [])[:5])}

PERSONALIZATION STRATEGIES (use at least 2-3 of these):
1. **Name Play**: Extract syllables, sounds, or meanings from their real name or username
2. **Interest Integration**: Incorporate their passions (sea, fitness, nature, tech, etc.)
3. **Personality Traits**: Reflect their archetype and visual style
4. **Bio Elements**: Use keywords, themes, or concepts from their bio
5. **Cultural References**: If they reference specific cultures, locations, or concepts
6. **Lifestyle Alignment**: Match their lifestyle choices and values

REASONING REQUIREMENT:
For each name, think about WHY it's perfect for this specific influencer:
- How does it connect to their name/username?
- What aspect of their personality does it capture?
- How does it reflect their interests or lifestyle?
- Why would their audience connect with this name?

NAMING EXAMPLES WITH REASONING:
- "MOLO" (Rebecca Zamolo) - Plays with "Zamolo" surname, sounds like "mellow" reflecting her calm personality
- "Truvani" (Vani Hari "Food Babe") - Combines "Tru" (truth) with "Vani" (her name), reflecting her clean-food activism
- "Moon Juice" (Amanda Chantal Bacon) - Reflects her mystical, wellness-focused aesthetic and lunar themes
- "Bloom Nutrition" (Mari Llewellyn) - "Bloom" suggests growth/transformation, perfect for her fitness journey content

CRITICAL REQUIREMENTS:
- Names must be CATCHY and MEMORABLE
- Each name should have a clear personal connection to the influencer
- Avoid generic supplement names - make them feel custom-made
- Names should be short, pronounceable, and brandable
- Suitable for supplement industry but with personal flair
- Generate unique names that are different from previously generated names
- Consider SEO potential but prioritize personal connection
- Make sure names aren't super long, they must be short, concise, easy to pronounce

Format: Return only the names, one per line."""
            
            # Optionally append user insights to guide the model lightly
            if self.user_naming_insights:
                formatted_prompt = (
                    f"{formatted_prompt}\n\nADDITIONAL USER INSIGHTS (optional):\n"
                    f"{self.user_naming_insights}\n"
                    "Incorporate these preferences when they improve brand fit; otherwise prioritize analysis context."
                )

            # Append benchmark influencer-brand exemplars with detailed reasoning
            exemplars = (
                "Rebecca Zamolo — MOLO: Plays with 'Zamolo' surname, sounds like 'mellow' reflecting her calm personality\n"
                "Vani Hari (\"Food Babe\") — Truvani: Combines 'Tru' (truth) with 'Vani' (her name), reflecting clean-food activism\n"
                "Pamela Reif — Naturally Pam: Uses her first name 'Pam' with 'Naturally' reflecting her fitness/nutrition focus\n"
                "Amanda Chantal Bacon — Moon Juice: Reflects her mystical, wellness-focused aesthetic and lunar themes\n"
                "MaryRuth Ghiyam — MaryRuth Organics: Uses her full first name, 'Organics' reflects her clean-living values\n"
                "Liver King (Brian Johnson) — Heart & Soil: Reflects his ancestral/primal lifestyle philosophy\n"
                "Mari Llewellyn — Bloom Nutrition: 'Bloom' suggests growth/transformation, perfect for her fitness journey content\n"
                "Hannah & Daniel Neeleman — Be Well Protein: 'Be Well' reflects their holistic wellness approach\n"
                "Kelly LeVeque — Be Well Protein: Same 'Be Well' philosophy, emphasizing clean, grass-fed approach"
            )
            formatted_prompt = (
                f"{formatted_prompt}\n\nBENCHMARK INFLUENCER BRAND EXEMPLARS WITH REASONING (for inspiration; do not copy names):\n"
                f"{exemplars}\n\n"
                "PERSONALIZATION REASONING RUBRIC (think about each name's connection to the influencer):\n"
                "1) **Name Connection**: How does the name relate to their real name, username, or personal brand?\n"
                "2) **Personality Match**: What aspect of their personality, interests, or lifestyle does it capture?\n"
                "3) **Audience Appeal**: Why would their specific audience connect with this name?\n"
                "4) **Brand Story**: What story does the name tell about the influencer and their values?\n"
                "5) **Memorability**: Is it catchy, unique, and easy to remember?\n"
                "6) **Industry Fit**: Does it work well in the supplement/wellness space while maintaining personal connection?\n\n"
                "Generate names that follow this reasoning pattern - each name should have a clear, thoughtful connection to the influencer.\n"
                "Do NOT reveal reasoning; return only the names, one per line."
            )

            # Call OpenAI API (prefer gpt-4.1; fallback to responses API if needed)
            client = openai.OpenAI(api_key=api_key)
            content = None
            try:
                response = client.chat.completions.create(
                    model=os.getenv("OPENAI_MODEL", "gpt-4o"),
                    messages=[
                        {"role": "system", "content": "You are a creative brand naming expert."},
                        {"role": "user", "content": formatted_prompt}
                    ],
                    max_tokens=500,
                    temperature=0.5
                )
                content = response.choices[0].message.content.strip()
            except Exception as api_err:
                logger.warning(f"chat.completions failed on configured model, falling back to responses API: {api_err}")
                try:
                    resp = client.responses.create(
                        model=os.getenv("OPENAI_MODEL", "gpt-4o"),
                        input=[
                            {"role": "system", "content": "You are a creative brand naming expert."},
                            {"role": "user", "content": formatted_prompt}
                        ],
                        max_output_tokens=500,
                        temperature=0.5,
                    )
                    # Best-effort extraction across SDK versions
                    content = getattr(resp, "output_text", None)
                    if not content and hasattr(resp, "choices") and resp.choices:
                        try:
                            content = resp.choices[0].message.get("content", "")
                        except Exception:
                            content = str(resp)
                    if not content:
                        raise RuntimeError("Empty response content from responses API")
                    content = str(content).strip()
                except Exception as fallback_err:
                    logger.error(f"Both chat and responses API calls failed: {fallback_err}")
                    return []

            # Extract names from response
            
            names = []
            for line in content.split('\n'):
                line = line.strip()
                if line:
                    # Remove numbering if present (e.g., "1. Name" -> "Name")
                    if line[0].isdigit() and '. ' in line:
                        line = line.split('. ', 1)[1]
                    names.append(line)
            
            logger.info(f"Generated {len(names)} names successfully")
            return names
                
        except Exception as e:
            logger.error(f"Error in name generation: {str(e)}")
            return []

    def _validate_single_name(self, name: str) -> Dict[str, Any]:
        """
        Validate a single name for domain availability and market competition.
        """
        try:
            # Clean the name for domain checking
            clean_name = name.lower().replace(" ", "").replace("-", "")
            domain = f"{clean_name}.com"
            
            # Check domain availability
            domain_available = self._check_domain_availability(domain)
            
            # Basic competition analysis
            competition_level = self._analyze_competition(name)
            
            # Calculate viability score
            score = self._calculate_viability_score(domain_available, competition_level, name)
            
            # Determine recommendation
            recommendation = "PROCEED" if score >= 7.0 else "REVIEW" if score >= 5.0 else "AVOID"
            
            return {
                "status": "success",
                "domain_availability": {
                    "available": domain_available,
                    "domain": domain
                },
                "competition_analysis": {
                    "competition_level": competition_level,
                    "market_saturation": "low" if competition_level < 3 else "medium" if competition_level < 6 else "high"
                },
                "viability_metrics": {
                    "score": score,
                    "grade": self._get_grade(score)
                },
                "recommendation": {
                    "decision": recommendation,
                    "reasoning": f"Score: {score}/10 - {competition_level}/10 competition level"
                }
            }
            
        except Exception as e:
            logger.error(f"Error validating name '{name}': {str(e)}")
            return {
                "status": "error",
                "error": str(e)
            }

    def _check_domain_availability(self, domain: str) -> bool:
        """
        Check if a domain is available using WHOIS.
        """
        try:
            # Try to get WHOIS information
            domain_info = whois.whois(domain)
            
            # If we get info and it has expiration date, domain is taken
            if domain_info and hasattr(domain_info, 'expiration_date') and domain_info.expiration_date:
                return False
            
            # If no expiration date, likely available
            return True
            
        except Exception:
            # If WHOIS fails, assume available for now
            return True

    def _analyze_competition(self, name: str) -> int:
        """
        Analyze market competition for a name (simplified version).
        Returns a score from 1-10 (1 = low competition, 10 = high competition).
        """
        # Simple keyword-based competition analysis
        common_words = ["health", "wellness", "fitness", "nutrition", "vita", "pro", "max", "plus"]
        
        name_lower = name.lower()
        competition_score = 1
        
        # Check for common words
        for word in common_words:
            if word in name_lower:
                competition_score += 1
        
        # Check name length (shorter names are more competitive)
        if len(name) <= 6:
            competition_score += 2
        elif len(name) <= 10:
            competition_score += 1
        
        # Cap at 10
        return min(competition_score, 10)

    def _calculate_viability_score(self, domain_available: bool, competition_level: int, name: str) -> float:
        """
        Calculate overall viability score for a name.
        """
        score = 5.0  # Base score
        
        # Domain availability bonus
        if domain_available:
            score += 2.0
        
        # Competition penalty (reduced influence)
        score -= (competition_level - 5) * 0.1
        
        # Name quality bonus
        if len(name) >= 4 and len(name) <= 12:
            score += 1.0
        
        # Ensure score is between 0 and 10
        return max(0.0, min(10.0, score))

    def _get_grade(self, score: float) -> str:
        """
        Convert numeric score to letter grade.
        """
        if score >= 9.0:
            return "A+"
        elif score >= 8.0:
            return "A"
        elif score >= 7.0:
            return "B+"
        elif score >= 6.0:
            return "B"
        elif score >= 5.0:
            return "C+"
        elif score >= 4.0:
            return "C"
        elif score >= 3.0:
            return "D"
        else:
            return "F"

    def _validate_names_batch(self, names: List[str]) -> List[Dict[str, Any]]:
        """
        Validate a batch of names using direct domain validation.
        """
        logger.info(f"Validating batch of {len(names)} names")
        
        validated_names = []
        
        for i, name in enumerate(names):
            try:
                logger.info(f"Validating name {i+1}/{len(names)}: {name}")
                
                # Validate the name directly
                validation_result = self._validate_single_name(name)
                
                if validation_result["status"] == "success":
                    # Add the name to the validated list
                    validated_names.append({
                        "name": name,
                        "validation_result": validation_result,
                        "viability_score": validation_result["viability_metrics"]["score"],
                        "domain_availability": validation_result["domain_availability"],
                        "competition_level": validation_result["competition_analysis"]["competition_level"],
                        "recommendation": validation_result["recommendation"]["decision"]
                    })
                    
                    logger.info(f"Name '{name}' validated with score: {validation_result['viability_metrics']['score']}")
                else:
                    logger.warning(f"Failed to validate name '{name}': {validation_result.get('error', 'Unknown error')}")
                
                # Small delay to avoid overwhelming APIs
                time.sleep(0.5)
                
            except Exception as e:
                logger.error(f"Error validating name '{name}': {str(e)}")
                continue
        
        logger.info(f"Successfully validated {len(validated_names)} out of {len(names)} names")
        return validated_names

    def _rank_names_by_viability(self, validated_names: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Rank names by viability score and other factors.
        """
        logger.info("Ranking names by viability")
        
        # Sort by viability score (descending)
        ranked_names = sorted(validated_names, key=lambda x: x["viability_score"], reverse=True)
        
        # Add ranking information
        for i, name_data in enumerate(ranked_names):
            name_data["rank"] = i + 1
            name_data["grade"] = self._get_grade(name_data["viability_score"])
        
        logger.info(f"Top 3 names: {[n['name'] for n in ranked_names[:3]]}")
        return ranked_names

    def _get_grade(self, score: float) -> str:
        """
        Convert numerical score to letter grade.
        """
        if score >= 9.0:
            return "A+"
        elif score >= 8.0:
            return "A"
        elif score >= 7.0:
            return "B+"
        elif score >= 6.0:
            return "B"
        elif score >= 5.0:
            return "C+"
        elif score >= 4.0:
            return "C"
        else:
            return "D"

    def _find_best_name(self, ranked_names: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """
        Find the best name that meets our criteria.
        """
        logger.info("Finding the best name from ranked list")
        
        # Look for names with high viability scores
        high_viability_names = [n for n in ranked_names if n["viability_score"] >= self.min_viability_score]
        
        if high_viability_names:
            best_name = high_viability_names[0]
            logger.info(f"Found high-viability name: {best_name['name']} (Score: {best_name['viability_score']})")
            return best_name
        
        # If no high-viability names, take the top ranked name
        if ranked_names:
            best_name = ranked_names[0]
            logger.info(f"Using top-ranked name: {best_name['name']} (Score: {best_name['viability_score']})")
            return best_name
        
        logger.warning("No suitable names found")
        return None

    def run(self) -> Dict[str, Any]:
        """
        Execute the simplified name generation process - just return GPT results.
        """
        logger.info("Starting simplified name generation process")
        
        try:
            # Ensure social media context is available (auto-load if missing)
            if not self.social_media_data:
                self.social_media_data = self._auto_load_social_context() or {}
            
            # Extract style information from social media data
            style_metadata = self._extract_style_from_social_data(self.social_media_data)
            
            # Generate names using GPT
            generated_names = self._generate_names_batch(style_metadata, existing_names=[])
            
            if not generated_names:
                return {
                    "status": "error",
                    "message": "No names generated by GPT",
                    "total_names_generated": 0
                }
            
            # Take first 5 names
            top_5_names = generated_names[:5]
            selected_name = top_5_names[0] if top_5_names else "No names generated"
            
            # Prepare the response
            response = {
                "status": "success",
                "selected_name": {
                    "name": selected_name,
                    "viability_score": 0.0,
                    "grade": "N/A",
                    "rank": 1,
                    "recommendation": "GPT Generated"
                },
                "style_information": {
                    "aesthetic_theme": style_metadata.get("aesthetic_theme", "N/A"),
                    "brand_voice": style_metadata.get("brand_voice", "N/A"),
                    "interests": style_metadata.get("interests", []),
                    "source": style_metadata.get("source", "N/A")
                },
                "validation_details": {
                    "domain_availability": {"available": "Not checked", "domain": "Not checked"},
                    "competition_level": "Not checked",
                    "full_validation_result": "Validation disabled"
                },
                "process_summary": {
                    "iterations_completed": 1,
                    "total_names_generated": len(generated_names),
                    "total_names_validated": 0,
                    "top_5_names": [
                        {
                            "name": name,
                            "score": 0.0,
                            "grade": "N/A"
                        }
                        for name in top_5_names
                    ]
                }
            }
            
            logger.info(f"Name generation complete. Generated {len(generated_names)} names, returning top 5")
            return response
            
        except Exception as e:
            logger.error(f"Error in name generation process: {str(e)}", exc_info=True)
            return {
                "status": "error",
                "message": f"Error in name generation process: {str(e)}",
                "step": "name_generation"
            }


if __name__ == "__main__":
    # Test the fusion tool
    test_social_data = {
        "source": "instagram_analysis",
        "aesthetic_theme": "minimalist",
        "brand_voice": "sophisticated",
        "region": "West Coast",
        "interests": ["design", "luxury", "supplements"]
    }
    
    # Initialize the fusion tool
    fusion_tool = NameSelectorFusionTool(
        social_media_data=test_social_data,
        max_names_to_generate=15,
        max_iterations=2,
        min_viability_score=6.0,
        use_gpt=True
    )
    
    # Run the tool
    result = fusion_tool.run()
    
    # Print results
    print("\n" + "="*60)
    print("NAME SELECTION FUSION TOOL - TEST RESULTS")
    print("="*60)
    print(json.dumps(result, indent=2))
