import json
import os
import uuid
import logging
import base64
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from agency_swarm.tools import BaseTool  # type: ignore
from apify_client import ApifyClient
from dotenv import load_dotenv
from pydantic import Field
from openai import OpenAI
import requests

from wizard_designer.utils.highlevel_client import upsert_contact_with_fields

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


def get_execution_time_in_readable_format(start_time: datetime) -> str:
    """Return human-readable elapsed time like 'Xm Ys'."""
    delta = datetime.now(timezone.utc) - start_time
    total_seconds = int(delta.total_seconds())
    minutes, seconds = divmod(total_seconds, 60)
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


logger = setup_logger(__name__)

load_dotenv()


class SocialMediaAnalyzer(BaseTool):
    """
    Analyzes social media profiles and posts for brand development.
    Calls both profile and post APIs to get comprehensive data.
    """

    profile_url: str = Field(
        ..., description="URL of the social media profile to analyze"
    )
    max_results: int = Field(
        default=10, description="Maximum number of posts to analyze"
    )
    debug: bool | None = Field(default=True, description="Enable debug logging")
    get_related_profiles: bool | None = Field(
        default=False, description="Enable related profiles scraping"
    )
    include_comments: bool | None = Field(
        default=True, description="If true, fetch up to comments_per_post comments for each post"
    )
    comments_per_post: int | None = Field(
        default=10, description="Max number of comments to fetch per post when include_comments is true"
    )
    analysis_posts_limit: int | None = Field(
        default=5, description="Max number of posts to include in GPT analysis"
    )
    analysis_model: str | None = Field(
        default=os.getenv("OPENAI_MODEL", "gpt-4o-mini"), description="OpenAI model to use for analysis"
    )
    analysis_language: str | None = Field(
        default="en", description="Language of the analysis output"
    )
    use_cache: bool | None = Field(
        default=True, description="Use and update local cache to avoid repeated API calls"
    )
    cache_max_age_minutes: int | None = Field(
        default=720, description="Max cache age in minutes before refetching (default 12h)"
    )

    def __init__(self, **data):
        super().__init__(**data)
        logger.info("Initializing SocialMediaAnalyzer with Apify API token")
        api_token = os.getenv("APIFY_API_TOKEN")
        if not api_token:
            # Defer hard failure to run() so schema generation still works
            logger.warning("APIFY_API_TOKEN not set; requests will fail until provided")
        self._client = ApifyClient(api_token) if api_token else None
        
        # Get session ID from headers for cache isolation
        self._session_id = self._get_session_id_from_headers()

    def _apify_proxy_config(self) -> dict:
        """Build Apify proxy configuration from environment variables if provided."""
        try:
            use_proxy_env = os.getenv("APIFY_USE_PROXY", "").strip().lower()
            groups_env = os.getenv("APIFY_PROXY_GROUPS", "").strip()
            country_code = os.getenv("APIFY_PROXY_COUNTRY_CODE", "").strip()

            use_proxy = use_proxy_env in ("1", "true", "yes", "on") or bool(groups_env)
            if not use_proxy:
                return {}

            config: dict = {
                "proxyConfiguration": {
                    "useApifyProxy": True,
                }
            }
            groups: list[str] = []
            if groups_env:
                groups = [g.strip() for g in groups_env.split(",") if g.strip()]
            if groups:
                config["proxyConfiguration"]["apifyProxyGroups"] = groups
            if country_code:
                config["proxyConfiguration"]["countryCode"] = country_code

            # Some actors still support legacy 'proxy' key; include defensively
            config["proxy"] = config["proxyConfiguration"].copy()
            return config
        except Exception:
            return {}

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

    def _get_profile_data(self, request_id: str, username: str) -> list:
        """Get profile data using instagram-profile-scraper"""
        logger.info(f"[{request_id}] Fetching profile data...")
        logger.debug(f"Calling profile API for username: {username}")
        # Derive timeouts/memory from environment (no user inputs)
        timeout_secs = int(os.getenv("APIFY_TIMEOUT_SECS", "600"))
        profile_run = self._client.actor("apify/instagram-profile-scraper").call(
            run_input={
                "usernames": [username],
            },
            memory_mbytes=int(os.getenv("APIFY_MEMORY_MBYTES", "4096")),
            timeout_secs=timeout_secs,
        )

        logger.info("Profile API call completed")
        return (
            self._client.dataset(profile_run["defaultDatasetId"])
            .list_items(clean=True, limit=1)
            .items
        )

    def _get_posts_data(self, request_id: str, username: str) -> list:
        """Get posts data using instagram-post-scraper"""
        logger.info(f"[{request_id}] Fetching posts data...")
        logger.debug(f"Calling posts API for username: {username}")

        # Reduce timeframe from 3 years to 1 year for faster retrieval
        one_year_ago = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

        timeout_secs = int(os.getenv("APIFY_TIMEOUT_SECS", "600"))
        posts_run = self._client.actor("apify/instagram-post-scraper").call(
            run_input={
                # Use username input as expected by the actor
                "username": [username],
                "resultsLimit": min(int(self.max_results or 20), 50),
                "onlyPostsNewerThan": one_year_ago,
                "skipPinnedPosts": True,
                "expandHashtags": False,
                "expandMentions": False,
                "scrapeComments": False,
            },
            memory_mbytes=int(os.getenv("APIFY_MEMORY_MBYTES", "4096")),
            timeout_secs=timeout_secs,
        )

        logger.info(f"Posts API call completed. Fetching posts since {one_year_ago}")
        return (
            self._client.dataset(posts_run["defaultDatasetId"])
            .list_items(
                clean=True,
                limit=20,  # Match resultsLimit
            )
            .items
        )

    def _get_tiktok_profile_and_posts(self, request_id: str, username_or_url: str) -> dict:
        """Get TikTok profile and posts using Apify TikTok actors and normalize to IG-like shape"""
        logger.info(f"[{request_id}] Fetching TikTok data...")

        # Build a profile URL if we only received a handle
        url = username_or_url
        if "tiktok.com" not in url:
            handle = username_or_url.lstrip("@")
            url = f"https://www.tiktok.com/@{handle}/"
        # Strip query/fragment for stability
        if "?" in url:
            url = url.split("?", 1)[0]
        if "#" in url:
            url = url.split("#", 1)[0]

        timeout_secs = int(os.getenv("APIFY_TIMEOUT_SECS", "600"))
        items: list = []

        def _fetch_items(actor_name: str, run_input: dict) -> list:
            # Merge proxy config and ensure both resultsLimit/maxItems are set
            proxy_cfg = self._apify_proxy_config()
            merged_input = {
                **run_input,
                **proxy_cfg,
            }
            if "resultsLimit" not in merged_input:
                merged_input["resultsLimit"] = min(int(self.max_results or 20), 50)
            if "maxItems" not in merged_input:
                merged_input["maxItems"] = merged_input["resultsLimit"]
            # For clockworks/tiktok-profile-scraper, request covers and control posts per profile
            if "clockworks/tiktok-profile-scraper" in actor_name:
                merged_input.setdefault("shouldDownloadCovers", True)
                merged_input.setdefault("resultsPerPage", merged_input["resultsLimit"]) 
                merged_input.setdefault("excludePinnedPosts", True)
 
            run_loc = self._client.actor(actor_name).call(
                run_input=merged_input,
                memory_mbytes=int(os.getenv("APIFY_MEMORY_MBYTES", "4096")),
                timeout_secs=timeout_secs,
            )
            return (
                self._client.dataset(run_loc["defaultDatasetId"])  # type: ignore[index]
                .list_items(clean=True, limit=min(int(self.max_results or 20), 50))
                .items
            )

        handle = username_or_url.lstrip("@")
        if "tiktok.com" in handle and "/@" in handle:
            handle = handle.split("/@", 1)[1].split("/", 1)[0]
        # Remove query/fragment from extracted handle
        if "?" in handle:
            handle = handle.split("?", 1)[0]
        if "#" in handle:
            handle = handle.split("#", 1)[0]

        # Try multiple strategies across actor candidates
        # Priority: env override -> common actors
        env_actor = (os.getenv("TIKTOK_ACTOR", "").strip() or None)
        actor_candidates = [
            env_actor,
            "clockworks/tiktok-scraper",            # common, general-purpose
            "clockworks/tiktok-profile-scraper",    # profile-focused
            "scraptik/tiktok-api",                  # API-like actor
            "industrious_overlap/tiktok-scraper",
            "xtdata/tiktok-scraper",
        ]
        actor_candidates = [a for a in actor_candidates if a]

        for actor_name in actor_candidates:
            if items:
                break
            # 1) actor-specific 'profiles' path
            if handle and ("profile" in actor_name or "scraptik/tiktok-api" in actor_name):
                try:
                    items = _fetch_items(
                        actor_name,
                        {
                            "profiles": [handle],
                            "resultsPerPage": min(int(self.max_results or 20), 100),
                            "profileScrapeSections": ["videos"],
                            "profileSorting": "latest",
                            "excludePinnedPosts": False,
                            "shouldDownloadVideos": False,
                            "shouldDownloadCovers": True,
                            "shouldDownloadSubtitles": False,
                            "shouldDownloadSlideshowImages": False,
                            "shouldDownloadAvatars": False,
                        },
                    )
                except Exception as e:
                    logger.warning(f"[{request_id}] {actor_name} (profiles) failed: {e}")
                    items = []
            # 2) directUrls
            if not items:
                try:
                    items = _fetch_items(
                        actor_name,
                        {"directUrls": [url], "resultsLimit": min(int(self.max_results or 20), 50)},
                    )
                except Exception as e:
                    logger.warning(f"[{request_id}] {actor_name} (directUrls) failed: {e}")
                    items = []
            # 3) startUrls
            if not items:
                try:
                    items = _fetch_items(
                        actor_name,
                        {"startUrls": [{"url": url}], "resultsLimit": min(int(self.max_results or 20), 50)},
                    )
                except Exception as e:
                    logger.warning(f"[{request_id}] {actor_name} (startUrls) failed: {e}")
                    items = []
            # 4) usernames
            if not items and handle:
                try:
                    items = _fetch_items(
                        actor_name,
                        {"usernames": [handle], "resultsLimit": min(int(self.max_results or 20), 50)},
                    )
                except Exception as e:
                    logger.warning(f"[{request_id}] {actor_name} (usernames) failed: {e}")
                    items = []
            # 5) username (singular)
            if not items and handle:
                try:
                    items = _fetch_items(
                        actor_name,
                        {"username": handle, "resultsLimit": min(int(self.max_results or 20), 50)},
                    )
                except Exception as e:
                    logger.warning(f"[{request_id}] {actor_name} (username) failed: {e}")
                    items = []
            # 6) handles
            if not items and handle:
                try:
                    items = _fetch_items(
                        actor_name,
                        {"handles": [handle], "resultsLimit": min(int(self.max_results or 20), 50)},
                    )
                except Exception as e:
                    logger.warning(f"[{request_id}] {actor_name} (handles) failed: {e}")
                    items = []

        if not items:
            return {"profile": {}, "latestPosts": []}

        # Try to derive profile fields from the first item
        first = items[0]
        author_meta = first.get("authorMeta") or first.get("author") or first.get("authorDetails") or {}

        username = (
            author_meta.get("name")
            or author_meta.get("uniqueId")
            or self._extract_username(url)
        )
        full_name = author_meta.get("nickName") or author_meta.get("nickname") or ""
        followers = (
            author_meta.get("fans")
            or (author_meta.get("authorStats") or {}).get("followerCount")
            or author_meta.get("followerCount")
            or 0
        )
        signature = author_meta.get("signature") or author_meta.get("bio") or ""
        verified = bool(author_meta.get("verified"))
        posts_count = (
            author_meta.get("video")
            or (author_meta.get("authorStats") or {}).get("videoCount")
            or author_meta.get("videoCount")
            or 0
        )

        # Normalize posts to match IG-like subset used by analyzer
        latest_posts: list[dict] = []
        for it in items:
            caption = it.get("text") or it.get("desc") or it.get("title") or ""
            # Extract hashtags from list or from caption
            hashtags_list = []
            if isinstance(it.get("hashtags"), list):
                for h in it.get("hashtags", []):
                    name = h.get("name") if isinstance(h, dict) else str(h)
                    if not name:
                        continue
                    if name.startswith("#"):
                        hashtags_list.append(name)
                    else:
                        hashtags_list.append(f"#{name}")
            elif isinstance(caption, str):
                # Fallback simple parse
                for token in caption.split():
                    if token.startswith("#"):
                        hashtags_list.append(token)

            likes = (
                it.get("diggCount")
                or (it.get("stats") or {}).get("diggCount")
                or 0
            )
            comments = (
                it.get("commentCount")
                or (it.get("stats") or {}).get("commentCount")
                or 0
            )
            timestamp = it.get("createTime") or it.get("timestamp") or ""
            post_url = (
                it.get("webVideoUrl")
                or it.get("url")
                or it.get("shareUrl")
                or ""
            )
            # Try to pick a representative image (cover)
            covers = it.get("covers") or {}
            cover_url = (
                # Common cover fields nested under 'covers'
                covers.get("dynamic")
                or covers.get("default")
                or covers.get("origin")
                or covers.get("dynamicCover")
                or covers.get("defaultCover")
                or covers.get("originCover")
                # Top-level fallbacks seen across actors
                or it.get("dynamicCover")
                or it.get("defaultCover")
                or it.get("originCover")
                or it.get("thumbnailUrl")
                or it.get("coverUrl")
                or (it.get("videoMeta") or {}).get("cover")
                or (it.get("videoMeta") or {}).get("coverSmall")
                or (it.get("video") or {}).get("cover")
            )
            images = [cover_url] if isinstance(cover_url, str) and cover_url else []

            latest_posts.append(
                {
                    "caption": caption,
                    "hashtags": hashtags_list,
                    "likesCount": likes,
                    "commentsCount": comments,
                    "timestamp": timestamp,
                    "url": post_url,
                    "images": images,
                }
            )

        profile = {
            "username": username or "",
            "fullName": full_name,
            "followersCount": int(followers or 0),
            "biography": signature,
            "verified": verified,
            "businessCategoryName": "",
            "postsCount": int(posts_count or 0),
            # Align with IG normalized structure (downstream expects this key)
            "latestPosts": latest_posts[: min(int(self.max_results or 10), 50)],
        }

        return {"profile": profile, "latestPosts": profile.get("latestPosts", [])}

    def _get_post_comments(self, request_id: str, post_url: str, max_comments: int) -> list:
        """Fetch comments for a single post using instagram-comment-scraper"""
        logger.info(f"[{request_id}] Fetching comments for post: {post_url}")
        # Dedicated timeout for comments (env-controlled)
        per_post_timeout = int(os.getenv("COMMENTS_TIMEOUT_SECS", "120"))
        try:
            run = self._client.actor("apify/instagram-comment-scraper").call(
                run_input={
                    # Actor expects directUrls for post/reel links
                    "directUrls": [post_url],
                    # Attempt to limit scrape size server-side (actor may ignore unknown keys)
                    "resultsLimit": max_comments,
                    "maxItems": max_comments,
                    # Some actors support maxItems/maxComments; we also limit via dataset read
                },
                memory_mbytes=1024,
                timeout_secs=per_post_timeout,
            )
            items = (
                self._client.dataset(run["defaultDatasetId"])  # type: ignore[index]
                .list_items(clean=True, limit=max_comments)
                .items
            )
        except Exception as e:
            logger.warning(f"[{request_id}] Failed to fetch comments for {post_url}: {e}")
            items = []

        # Normalize a compact subset of each comment
        comments: list[dict] = []
        for it in items or []:
            comments.append(
                {
                    "text": it.get("text", ""),
                    "username": it.get("ownerUsername") or it.get("username", ""),
                    "timestamp": it.get("timestamp") or it.get("createdAt") or "",
                    "likesCount": it.get("likesCount", 0),
                    "url": it.get("url", post_url),
                }
            )
        return comments

    def _first_image_url(self, post: Dict[str, Any]) -> Optional[str]:
        images = post.get("images") or []
        if isinstance(images, list) and images:
            url = images[0]
            return url if isinstance(url, str) else None
        return None

    def _url_to_data_url(self, url: str) -> Optional[str]:
        try:
            headers = {"User-Agent": "Mozilla/5.0 (compatible; SMA/1.0)"}
            resp = requests.get(url, headers=headers, timeout=20)
            if resp.status_code >= 400:
                return None
            content_type = resp.headers.get("Content-Type", "image/jpeg")
            b64 = base64.b64encode(resp.content).decode("utf-8")
            return f"data:{content_type};base64,{b64}"
        except Exception:
            return None

    def _run_gpt_analysis(self, posts: List[Dict[str, Any]], comments_map: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return {"status": "error", "error": "OPENAI_API_KEY not set", "analysis": None}

        client = OpenAI(api_key=api_key)

        # Prepare content blocks: take first image per post and a compact comments sample
        items: List[Dict[str, Any]] = []
        for post in posts:
            first_image = self._first_image_url(post)
            if first_image:
                # Convert to data URL to avoid remote fetch failures/expirations
                data_url = self._url_to_data_url(first_image)
                if data_url:
                    items.append({"type": "image_url", "image_url": {"url": data_url}})
            # Add up to 10 comments text concatenated
            post_url = post.get("url", "")
            comments = comments_map.get(post_url, [])
            if comments:
                text = "\n".join([f"@{(c.get('username') or '').strip()}: {(c.get('text') or '').strip()}" for c in comments[:10]])
                if text:
                    items.append({"type": "text", "text": f"COMMENTS for {post_url}:\n{text}"})

        if not items:
            return {"status": "error", "error": "No images or comments to analyze", "analysis": None}

        sys_prompt = (
            "You are a brand strategist and stylist. Analyze the influencer's first-image visuals and audience comments. "
            "Classify the influencer into one of these archetypes (pick 1 primary, up to 2 secondary):\n"
            "1 Gym Bros & Bodybuilders | Followers: 70% men 30% women 18-35 | Best: Protein, creatine, preworkout, BCAAs | Comments: 'split?', 'protein grams?', 'stack pls' | Angle: performance & gains.\n"
            "2 Wellness & Yoga Gurus | Followers: 80% women 20-40 | Best: Collagen, adaptogens, detox, greens | Comments: 'morning ritual', 'matcha?', 'energy' | Angle: balance & natural health.\n"
            "3 Biohackers & Productivity | Followers: 60% men 40% women 20-45 | Best: Nootropics, omega-3, vitamin D | Comments: 'focus stack?', 'does it work?', 'sleep improved' | Angle: brain power & longevity.\n"
            "4 Beauty & Lifestyle | Followers: 85% women 18-35 | Best: Collagen, biotin, hair gummies, anti-aging | Comments: 'skin goals', 'routine?', 'serum + supplement?' | Angle: beauty & confidence.\n"
            "5 Nutrition & Healthy Cooking | Followers: 70% women 30% men | Best: Protein, probiotics, greens | Comments: 'recipe pls', 'smoothie color', 'vegan?' | Angle: healthy lifestyle via food.\n"
            "6 Plant-Based & Sustainable | Followers: 75% women 18-40 | Best: Vegan protein, B12, iron, algae omega-3 | Comments: 'vegan?', 'cruelty-free?', 'protein source?' | Angle: eco-friendly wellness.\n"
            "7 Functional/CrossFit | Followers: 60% men 40% women 20-40 | Best: Electrolytes, BCAAs, recovery | Comments: 'WOD beast', 'recover fast?', 'stack?' | Angle: strength & peak performance.\n"
            "8 Science-Based Educators | Followers: mixed 22-45 | Best: Creatine, omega-3, multivitamins | Comments: 'any studies?', 'evidence?', 'sources?' | Angle: trust & evidence.\n"
            "9 Weight-Loss Coaches | Followers: 80% women 25-45 | Best: MRPs, fat burners, appetite blends | Comments: 'down 2kg', 'belly fat?', 'before/after' | Angle: fast results & motivation.\n"
            "10 Micro/Niche Influencers | Followers: 1k-50k mixed | Best: niche supplements | Comments: 'I trust you', 'ordered', 'thanks' | Angle: authenticity & relatability.\n"
            "11 Aesthetic Lifestyle Males | Followers: 80% women 18-30 | Best: Collagen, protein, beauty gummies, multivitamins | Comments: 'marry me', 'skin goals king', 'morning routine?' | Angle: looks, status & lifestyle.\n"
            "Return ONLY JSON with these keys: \n"
            "inferred_archetype: {name, confidence_0_1, rationale}, archetype_candidates: [{name, confidence_0_1}], "
            "influencer_persona: {name_unknown_ok, role, traits}, visual_style: {dress_description, common_color_palettes, styling_vibe_tags}, "
            "synthesis_sentences: [10 strings mixing: who the influencer is; how followers aspire/think; supplement products they might buy], "
            "audience_alignment: {referent_audience: {who, demographics, why_they_want_to_be_like_them}, admirer_audience: {who, demographics, why_they_follow}, real_product_consumers: {who, demographics, rationale}}, "
            "recommended_product_types: [5-10 items from ONLY these categories: Men's Health, General Health, Premium Sports Nutrition, Weight Loss & Detox, Nootropics, Women's Health/Hair/Skin/Beauty, In-House Custom Formulas, Premium Green & Red Superfoods - aligned to REAL consumers], marketing_angle: short string aligned to REAL consumers, "
            "brand_design_guidance: {sentiment, tone_words, typography, color_palette_hex, color_roles: [{hex, role_primary_secondary_accent_neutral}], color_usage: [{hex, usage_contexts, ratio_approx_percent}], logo_guidelines: {style_keywords, iconography_recommendations, typography_pairing, safe_area_clearspace_rules, lockup_recommendations, background_contrast_rules, do: [..], dont: [..]}, imagery_guidelines, packaging_notes}, "
            "brand_naming_guidelines: {naming_philosophy: string, style_keywords: [strings], word_categories: {latin_derived: [strings], modern_tech: [strings], wellness_nature: [strings], luxury_premium: [strings], performance_energy: [strings]}, naming_patterns: [strings], syllable_preference: string, cultural_considerations: [strings], avoid_words: [strings], brand_voice_alignment: string, influencer_alignment: string}. "
            "Keep it concise and in the requested language."
        )

        user_prompt = (
            f"Language: {self.analysis_language or 'en'}. "
            "Classify to an archetype using visuals + comments (primary + optional secondaries). "
            "Focus on: who the influencer is, how they dress, colors, vibe (luxury, gym, etc). "
            "Then craft 10 short sentences covering: influencer identity; follower aspiration; supplements to buy. "
            "Identify the group that sees the influencer as a referent (aspirational) vs admirer audience. "
            "IMPORTANT: Real product consumers are the group wanting to be LIKE the influencer (referent audience). If a male influencer attracts women, women may admire but not want to be like him; align products to those who aspire. "
            "Align recommended products + marketing angle specifically to REAL product consumers. "
            "In brand_design_guidance, include explicit logo generation guidelines (style, icon, type pairing, clearspace, lockups, background contrast, dos/donts). "
            "For color palettes, specify roles (primary/secondary/accent/neutral) and when to use each with approximate percentage ratios and contexts (packaging front, headers, CTAs, backgrounds), ensuring accessible contrast. "
            "CRITICAL: Generate comprehensive brand_naming_guidelines including naming philosophy, style keywords, naming patterns, syllable preferences, cultural considerations, words to avoid, brand voice alignment, and influencer_alignment (how names should specifically align with this influencer's persona, aesthetic, and audience)."
        )

        try:
            resp = client.chat.completions.create(
                model=self.analysis_model or os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": [{"type": "text", "text": user_prompt}] + items},
                ],
                temperature=0.5,
            )
            content = resp.choices[0].message.content or "{}"
            # Try to parse JSON; if not, wrap
            try:
                parsed = json.loads(content)
            except Exception as e:
                logger.warning(f"Failed to parse GPT response as JSON: {e}")
                # Try to extract JSON from markdown code blocks
                if "```json" in content:
                    try:
                        json_start = content.find("```json") + 7
                        json_end = content.find("```", json_start)
                        if json_end > json_start:
                            json_content = content[json_start:json_end].strip()
                            parsed = json.loads(json_content)
                        else:
                            parsed = {"raw": content}
                    except Exception:
                        parsed = {"raw": content}
                else:
                    parsed = {"raw": content}
            return {"status": "success", "analysis": parsed}
        except Exception as e:
            return {"status": "error", "error": str(e), "analysis": None}


    # ---------------- Cache helpers ----------------
    def _cache_dir(self) -> str:
        # Anchor cache to project root (parent of this file's directory)
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        path = os.path.join(project_root, "cache", "social_media_analyzer")
        os.makedirs(path, exist_ok=True)
        return path

    def _cache_file(self, username: str) -> str:
        # Sanitize filename for cache safety
        safe = username.replace("/", "_").replace("?", "_").replace("#", "_").replace(":", "_")
        return os.path.join(self._cache_dir(), f"{safe}_{self._session_id}.json")

    def _load_cache(self, username: str) -> Optional[Dict[str, Any]]:
        try:
            path = self._cache_file(username)
            if not os.path.exists(path):
                logger.info(f"[cache] No cache file: {path}")
                return None
            with open(path, "r") as f:
                data = json.load(f)
            # Allow both wrapped {saved_at, data} and raw payloads
            if isinstance(data, dict) and "data" in data and isinstance(data["data"], dict):
                payload = data["data"]
            else:
                payload = data
            # Do not keep any cached analysis; force re-analysis each run
            if isinstance(payload, dict):
                payload.pop("analysis", None)
            # No TTL logic: always accept cache as valid
            logger.info(f"[cache] Loaded cache for {username} from {path}")
            return {"data": payload}
        except Exception:
            return None

    def _save_cache(self, username: str, payload: Dict[str, Any]) -> None:
        try:
            path = self._cache_file(username)
            # Strip analysis before saving
            to_store = payload.copy() if isinstance(payload, dict) else payload
            if isinstance(to_store, dict):
                to_store.pop("analysis", None)
            with open(path, "w") as f:
                json.dump({"saved_at": datetime.now().isoformat(), "data": to_store}, f)
        except Exception:
            pass

    def _save_analysis(self, username: str, analysis: Dict[str, Any]) -> str:
        """Save analysis results to a file for reuse by other tools"""
        try:
            # Create analysis directory
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            analysis_dir = os.path.join(project_root, "cache", "social_media_analysis")
            os.makedirs(analysis_dir, exist_ok=True)
            
            # Save analysis with session ID and timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{username}_{self._session_id}_{timestamp}.json"
            filepath = os.path.join(analysis_dir, filename)
            
            with open(filepath, "w") as f:
                json.dump({
                    "username": username,
                    "session_id": self._session_id,
                    "saved_at": datetime.now().isoformat(),
                    "analysis": analysis
                }, f, indent=2)
            
            logger.info(f"Analysis saved to: {filepath}")
            return filepath
        except Exception as e:
            logger.warning(f"Failed to save analysis: {e}")
            return ""

    def _minimize_analysis(self, analysis: Dict[str, Any]) -> Dict[str, Any]:
        """Return a compact subset of analysis for lightweight responses."""
        try:
            inferred = analysis.get("inferred_archetype") or {}
            persona = analysis.get("influencer_persona") or {}
            visual = analysis.get("visual_style") or {}
            products = analysis.get("recommended_product_types") or []
            angle = analysis.get("marketing_angle") or ""
            brand_design = analysis.get("brand_design_guidance") or {}
            tone_words = brand_design.get("tone_words") or []
            palette = brand_design.get("color_palette_hex") or []

            return {
                "inferred_archetype": {
                    "name": inferred.get("name"),
                    "confidence_0_1": inferred.get("confidence_0_1"),
                },
                "influencer_persona": {
                    "role": persona.get("role"),
                },
                "visual_style": {
                    "styling_vibe_tags": (visual.get("styling_vibe_tags") or [])[:5],
                },
                "recommended_product_types": list(products)[:5],
                "marketing_angle": angle,
                "brand_design": {
                    "tone_words": list(tone_words)[:3],
                    "color_palette_hex": list(palette)[:4],
                },
            }
        except Exception:
            return {}

    def _filter_owner_content(self, data: Dict) -> Dict:
        """Filter data to only include essential content for GPT analysis"""
        filtered = {
            "status": "success",
            "platform": data.get("platform", "unknown"),
            "profile": {
                # Keep only essential profile fields
                "username": data.get("profile", {}).get("username", ""),
                "fullName": data.get("profile", {}).get("fullName", ""),
                "followersCount": data.get("profile", {}).get("followersCount", 0),
                "bio": data.get("profile", {}).get("biography", ""),
                "verified": data.get("profile", {}).get("verified", False),
                "businessCategoryName": data.get("profile", {}).get(
                    "businessCategoryName", ""
                ),
                "postsCount": data.get("profile", {}).get("postsCount", 0),
            },
            # Keep only essential post data for GPT analysis (not returned in final response)
            "posts": [
                {
                    "caption": post.get("caption", ""),
                    "hashtags": post.get("hashtags", []),
                    "likesCount": post.get("likesCount", 0),
                    "commentsCount": post.get("commentsCount", 0),
                    "timestamp": post.get("timestamp", ""),
                    "url": post.get("url", ""),
                    "images": post.get("images", []),
                }
                for post in data.get("profile", {}).get("latestPosts", [])[:int(self.analysis_posts_limit or 5)]
            ],
        }

        if self.get_related_profiles:
            filtered["relatedProfiles"] = data.get("relatedProfiles", [])

        return filtered

    def run(self) -> Dict[str, Any]:
        """Execute both API calls and combine results"""
        start_time = datetime.now(timezone.utc)
        request_id = str(uuid.uuid4())[
            :8
        ]  # Generate a short request ID for correlation

        # Log request start with request ID
        logger.info(
            f"[{request_id}] "
            f"SocialMediaAnalyzer request received for URL: {self.profile_url}"
        )

        try:
            if not self._client:
                raise RuntimeError("APIFY_API_TOKEN is missing. Set it in environment variables.")

            platform = self._detect_platform(self.profile_url)
            username = self._extract_username(self.profile_url)

            logger.info(
                f"[{request_id}] Starting analysis for {platform} profile: {username}"
            )

            # Try cache first if enabled
            cached: Optional[Dict[str, Any]] = None
            if self.use_cache:
                cached = self._load_cache(username)
            if cached and isinstance(cached.get("data"), dict):
                cached_result = cached["data"]
                # Top-up comments if requested and missing
                if self.include_comments:
                    needs_comments = any("comments" not in (p or {}) for p in cached_result.get("posts", []))
                    if needs_comments:
                        for post in cached_result.get("posts", []):
                            url = post.get("url")
                            if url and "comments" not in post:
                                post["comments"] = self._get_post_comments(request_id, url, int(self.comments_per_post or 10))
                        if self.use_cache:
                            self._save_cache(username, cached_result)
                # Always run GPT analysis (no conditional check)
                if "analysis" not in cached_result:
                    comments_map: Dict[str, List[Dict[str, Any]]] = {}
                    for p in cached_result.get("posts", []):
                        url = p.get("url", "")
                        if url and isinstance(p.get("comments"), list):
                            comments_map[url] = p["comments"]
                    posts_for_analysis = cached_result.get("posts", [])[: int(self.analysis_posts_limit or 5)]
                    analysis = self._run_gpt_analysis(posts_for_analysis, comments_map)
                    # Save analysis for reuse by other tools and build minimal response
                    analysis_file = ""
                    if analysis.get("status") == "success":
                        analysis_file = self._save_analysis(username, analysis.get("analysis", {}))
                    minimal = {
                        "status": analysis.get("status"),
                        **self._minimize_analysis(analysis.get("analysis", {}) or {}),
                    }
                    if analysis_file:
                        minimal["file"] = analysis_file
                    cached_result["analysis"] = minimal
                    
                    # Remove posts from cached result since they've been analyzed
                    if "posts" in cached_result:
                        del cached_result["posts"]
                    
                    logger.info(f"[{request_id}] Returning cached result for {username}")
                    return cached_result

            # TikTok branch: use TikTok scraper and normalized mapping
            if platform == "tiktok":
                tiktok_data = self._get_tiktok_profile_and_posts(request_id, self.profile_url)
                profile = tiktok_data.get("profile", {})

                result = {
                    "status": "success",
                    "platform": platform,
                    "profile": profile,
                    "metadata": {
                        "analyzed_at": datetime.now().isoformat(),
                        "profile_url": self.profile_url,
                        "request_id": request_id,
                    },
                }

                filtered_result = self._filter_owner_content(result)

                # No TikTok comments fetching (not supported here); proceed to GPT analysis if posts exist
                if filtered_result.get("posts"):
                    comments_map: Dict[str, List[Dict[str, Any]]] = {}
                    posts_for_analysis = filtered_result["posts"][: int(self.analysis_posts_limit or 5)]
                    analysis = self._run_gpt_analysis(posts_for_analysis, comments_map)
                    analysis_file = ""
                    if analysis.get("status") == "success":
                        analysis_file = self._save_analysis(username, analysis.get("analysis", {}))
                    minimal = {
                        "status": analysis.get("status"),
                        **self._minimize_analysis(analysis.get("analysis", {}) or {}),
                    }
                    if analysis_file:
                        minimal["file"] = analysis_file
                    filtered_result["analysis"] = minimal

                    # Remove posts from final response since they've been analyzed
                    del filtered_result["posts"]
                else:
                    # Ensure posts key is not present in final TikTok response
                    if "posts" in filtered_result:
                        del filtered_result["posts"]

                # Upsert to HighLevel and cache
                try:
                    prof = filtered_result.get("profile", {})
                    username_val = prof.get("username") or self._extract_username(self.profile_url)
                    followers = int(prof.get("followersCount") or 0)
                    email = f"{username_val}@example.com"
                    cf = {
                        "TIKTOK_HANDLE": f"@{username_val}",
                        "TIKTOK_FOLLOWERS": followers,
                    }
                    upsert_contact_with_fields(
                        email=email,
                        first_name=prof.get("fullName") or username_val,
                        custom_fields_by_symbol=cf,
                        tags=["aaas", "social-media-analysis"],
                    )
                except Exception as e:
                    logger.warning(f"HighLevel upsert skipped: {e}")

                logger.info(
                    f"[{request_id}] TikTok analysis completed successfully, execution time: {get_execution_time_in_readable_format(start_time)}."
                )
                if self.use_cache:
                    self._save_cache(username, filtered_result)
                return filtered_result

            # Instagram branch (default)
            profile_data = self._get_profile_data(request_id, username)

            if not profile_data:
                error_msg = f"No profile data retrieved for: {self.profile_url}"
                logger.error(f"[{request_id}] {error_msg}")
                raise ValueError(error_msg)

            # Optionally fetch posts with the posts actor (fallback if profile lacks latestPosts)
            latest_posts: List[Dict] = []
            try:
                posts_data = self._get_posts_data(request_id, username)
                if isinstance(posts_data, list):
                    latest_posts = posts_data[: int(self.max_results or 10)]
            except Exception as e:
                logger.warning(f"[{request_id}] Posts fetch failed, continuing with profile data only: {e}")

            profile = profile_data[0] if isinstance(profile_data, list) else {}
            # If profile doesn't include latestPosts, insert the posts we fetched
            if latest_posts and not profile.get("latestPosts"):
                profile["latestPosts"] = latest_posts

            # Filter for owner's content only
            result = {
                "status": "success",
                "platform": platform,
                "profile": profile,
                "metadata": {
                    "analyzed_at": datetime.now().isoformat(),
                    "profile_url": self.profile_url,
                    "request_id": request_id,
                },
            }

            filtered_result = self._filter_owner_content(result)

            # Always enrich comments for top posts (by likes) - Instagram only
            if (
                self.include_comments
                and filtered_result.get("posts")
                and (filtered_result.get("platform") == "instagram")
            ):
                posts_sorted = sorted(
                    filtered_result["posts"], key=lambda p: int(p.get("likesCount", 0)), reverse=True
                )
                top_post = posts_sorted[0] if posts_sorted else None
                max_comments = max(0, int(self.comments_per_post or 10))
                if top_post and max_comments > 0:
                    url = top_post.get("url")
                    if url:
                        top_post["comments"] = self._get_post_comments(request_id, url, max_comments)

            # Always run GPT analysis using first images and comments
            if filtered_result.get("posts"):
                # Build map url->comments for quick lookup
                comments_map: Dict[str, List[Dict[str, Any]]] = {}
                for p in filtered_result["posts"]:
                    url = p.get("url", "")
                    if url and isinstance(p.get("comments"), list):
                        comments_map[url] = p["comments"]

                posts_for_analysis = filtered_result["posts"][: int(self.analysis_posts_limit or 5)]
                analysis = self._run_gpt_analysis(posts_for_analysis, comments_map)
                # Save analysis for reuse by other tools and build minimal response
                analysis_file = ""
                if analysis.get("status") == "success":
                    analysis_file = self._save_analysis(username, analysis.get("analysis", {}))
                minimal = {
                    "status": analysis.get("status"),
                    **self._minimize_analysis(analysis.get("analysis", {}) or {}),
                }
                if analysis_file:
                    minimal["file"] = analysis_file
                filtered_result["analysis"] = minimal
                
                # Remove posts from final response since they've been analyzed
                del filtered_result["posts"]

            # --- NEW: Upsert HighLevel contact with social media info ---
            try:
                prof = filtered_result.get("profile", {})
                username_val = prof.get("username") or self._extract_username(self.profile_url)
                followers = int(prof.get("followersCount") or 0)
                # Use a synthetic email as identifier unless provided later
                email = f"{username_val}@example.com"
                cf: Dict[str, Any] = {}
                # Map based on detected platform
                platform = filtered_result.get("platform", "")
                if platform == "instagram":
                    cf = {
                        "IG_HANDLE": f"@{username_val}",
                        "IG_FOLLOWERS": followers,
                    }
                elif platform == "tiktok":
                    cf = {
                        "TIKTOK_HANDLE": f"@{username_val}",
                        "TIKTOK_FOLLOWERS": followers,
                    }

                if cf:
                    upsert_contact_with_fields(
                        email=email,
                        first_name=prof.get("fullName") or username_val,
                        custom_fields_by_symbol=cf,
                        tags=["aaas", "social-media-analysis"],
                    )
            except Exception as e:
                logger.warning(f"HighLevel upsert skipped: {e}")
            logger.info(
                f"[{request_id}] Analysis completed successfully with "
                f"GPT analysis and brand naming insights, "
                f"execution time: "
                f"{get_execution_time_in_readable_format(start_time)}."
            )
            # Save to cache
            if self.use_cache:
                self._save_cache(username, filtered_result)
            return filtered_result

        except Exception as e:
            error_msg = str(e)
            logger.error(
                f"[{request_id}] Error during analysis: {error_msg}", exc_info=True
            )
            return {
                "status": "error",
                "error": error_msg,
                "timestamp": datetime.now().isoformat(),
                "request_id": request_id,
                "profile_url": self.profile_url,
            }

    @staticmethod
    def _detect_platform(url: str) -> str:
        """Detect social media platform from URL"""
        if "instagram.com" in url:
            return "instagram"
        elif "twitter.com" in url or "x.com" in url:
            return "twitter"
        elif "tiktok.com" in url:
            return "tiktok"
        elif "facebook.com" in url:
            return "facebook"
        # Allow environment-based default for bare @handles
        try:
            if url.strip().startswith("@"):
                default_handle_platform = os.getenv("DEFAULT_HANDLE_PLATFORM", "instagram").strip().lower()
                if default_handle_platform in ("tiktok", "instagram", "twitter"):
                    return default_handle_platform
        except Exception:
            pass
        return "unknown"

    @staticmethod
    def _extract_username(url: str) -> str:
        """Extract username from social media URL"""

        try:
            # Normalize
            if url[-1] == "/":
                url = url[:-1]

            # TikTok: prefer the segment after '/@'
            if "tiktok.com" in url and "/@" in url:
                after_at = url.split("/@", 1)[1]
                # The username ends at the next '/'
                username = after_at.split("/", 1)[0]
            else:
                username = url.strip("/").split("/")[-1]

            if username.startswith("@"):
                username = username[1:]
            return username
        except Exception:
            return url


if __name__ == "__main__":
    try:
        # Instagram test profile
        test_instagram_profile = os.getenv("TEST_INSTAGRAM_PROFILE")
        if not test_instagram_profile:
            test_instagram_profile = "https://www.instagram.com/choi3an/"
            logger.info(
                f"TEST_INSTAGRAM_PROFILE not set, using fallback: {test_instagram_profile}"
            )

        # TikTok test handle or URL
        test_tiktok_profile = os.getenv("TEST_TIKTOK_PROFILE", "https://www.tiktok.com/@charlidamelio/")

        # Test IG with @ symbol (platform fallback via DEFAULT_HANDLE_PLATFORM)
        logger.info("Testing IG with @ symbol...")
        os.environ["DEFAULT_HANDLE_PLATFORM"] = os.getenv("DEFAULT_HANDLE_PLATFORM", "instagram")
        test_profile_with_at = f"@{test_instagram_profile.split('/')[-1]}"
        analyzer_with_at = SocialMediaAnalyzer(
            profile_url=test_profile_with_at, max_results=5, debug=True
        )
        logger.info(f"Running analysis for: {test_profile_with_at}")
        result_with_at = analyzer_with_at.run()
        print(f"IG @handle test status: {result_with_at.get('status', 'unknown')}")

        # Test Instagram URL
        logger.info("Testing Instagram URL...")
        analyzer_ig = SocialMediaAnalyzer(
            profile_url=test_instagram_profile,
            max_results=3,
            include_comments=True,
            comments_per_post=10,
            analysis_posts_limit=3,
            analysis_language="en",
            use_cache=True,
            cache_max_age_minutes=720
        )
        logger.info(f"Running analysis for: {test_instagram_profile}")
        result_ig = analyzer_ig.run()
        print(json.dumps({"ig_status": result_ig.get('status', 'unknown')}, indent=2))

        # Test TikTok URL (uses TikTok actor fallbacks)
        logger.info("Testing TikTok URL...")
        analyzer_tt = SocialMediaAnalyzer(
            profile_url=test_tiktok_profile,
            max_results=5,
            include_comments=False,
            analysis_posts_limit=3,
            analysis_language="en",
            use_cache=True
        )
        logger.info(f"Running analysis for: {test_tiktok_profile}")
        result_tt = analyzer_tt.run()
        print(json.dumps({"tt_status": result_tt.get('status', 'unknown')}, indent=2))

    except Exception as e:
        logger.error(f"Test execution failed: {str(e)}", exc_info=True)
        print(f"Error: {str(e)}")
