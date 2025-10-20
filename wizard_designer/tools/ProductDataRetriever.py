import json
import os
import logging
from typing import Any, Dict, List, Optional, Union
from agency_swarm.tools import BaseTool  # type: ignore
from dotenv import load_dotenv
from pydantic import Field
from openai import OpenAI
import requests


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
load_dotenv()


class ProductDataRetriever(BaseTool):
    """
    Intelligently retrieves product data from NocoDB based on product desires and social media analysis cache.
    Uses GPT-4o-mini to analyze natural language product requirements and find matching products.
    Integrates with social media analysis cache to prioritize recommended product types for the influencer's audience.
    """

    product_desires: str = Field(
        ..., description="Natural language description of desired products (e.g., 'I want skincare products for sensitive skin, preferably organic moisturizers')"
    )
    user_category: str | None = Field(
        default=None,
        description="Optional explicit product category chosen by the user. If provided, results are limited to this category."
    )
    mode: str = Field(
        default="ask",
        description="Category selection mode: 'user' (use user_category), 'ai' (pick one from social cache), or 'ask' (agent asks user first)."
    )
    max_results: int = Field(
        default=10, description="Maximum number of products to return"
    )
    include_categories: bool = Field(
        default=True, description="Whether to include product categories in search analysis"
    )
    confidence_threshold: float = Field(
        default=0.0, description="Minimum confidence threshold for product matching (0.0-1.0) - set to 0.0 to always return results"
    )
    analysis_model: str = Field(
        default=os.getenv("OPENAI_MODEL", "gpt-4o-mini"), description="OpenAI model to use for product desire analysis"
    )
    debug: bool = Field(
        default=True, description="Enable debug logging"
    )
    social_media_username: str | None = Field(
        None, description="Social media username to load product recommendations from cached analysis (auto-discovered if not provided)"
    )

    def __init__(self, **data):
        super().__init__(**data)
        # Initialize OpenAI client
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.warning("OPENAI_API_KEY not set; GPT analysis will fail")
        self._openai_client = OpenAI(api_key=api_key) if api_key else None

    def _get_session_id_from_headers(self) -> str:
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
                    if self.debug:
                        logger.info(f"🔍 Found session ID from header {header}: {session_id}")
                    return session_id
            
            # Fallback: generate new UUID for local development
            import uuid
            new_session_id = str(uuid.uuid4())[:8]
            if self.debug:
                logger.info(f"🆔 Generated new session ID: {new_session_id}")
            return new_session_id
            
        except Exception as e:
            if self.debug:
                logger.warning(f"Error getting session ID: {e}")
            import uuid
            return str(uuid.uuid4())[:8]

    def _discover_latest_username_for_session(self) -> str | None:
        """Infer the latest username for the current session by scanning analysis cache files."""
        try:
            from pathlib import Path
            session_id = self._get_session_id_from_headers()
            current_dir = Path(__file__).parent
            analysis_dir = current_dir.parent / "cache" / "social_media_analysis"
            if not analysis_dir.exists():
                return None
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
            if self.debug:
                logger.warning(f"Failed to discover username for session: {e}")
            return None

    def _load_social_media_analysis(self, username: str) -> dict | None:
        """Load the latest social media analysis for the given username from current session."""
        try:
            import json
            from pathlib import Path
            session_id = self._get_session_id_from_headers()
            current_dir = Path(__file__).parent
            analysis_dir = current_dir.parent / "cache" / "social_media_analysis"
            if not analysis_dir.exists():
                if self.debug:
                    logger.warning(f"Analysis directory not found: {analysis_dir}")
                return None
            
            # Find the latest analysis file for this username and session
            pattern = f"{username}_{session_id}_*.json"
            files = list(analysis_dir.glob(pattern))
            
            if not files:
                if self.debug:
                    logger.warning(f"No analysis files found for username: {username} and session: {session_id}")
                return None
            
            # Get the most recent file
            latest_file = max(files, key=lambda f: f.stat().st_mtime)
            if self.debug:
                logger.info(f"Loading analysis from: {latest_file}")
            
            with open(latest_file, 'r') as f:
                data = json.load(f)
                return data.get("analysis", {})
                
        except Exception as e:
            if self.debug:
                logger.warning(f"Failed to load social media analysis for {username}: {e}")
            return None

    def _get_cache_recommendations(self) -> Dict[str, Any] | None:
        """Get product recommendations from social media analysis cache."""
        try:
            # Auto-discover username if not provided
            username = self.social_media_username or self._discover_latest_username_for_session()
            if not username:
                if self.debug:
                    logger.info("No username provided or discovered; proceeding without cache recommendations")
                return None
            
            # Load analysis
            analysis = self._load_social_media_analysis(username)
            if not analysis:
                if self.debug:
                    logger.warning("Failed to load social media analysis for cache recommendations")
                return None
            
            # Extract relevant information
            recommendations = {
                "recommended_product_types": analysis.get("recommended_product_types", []),
                "archetype": analysis.get("inferred_archetype", {}).get("name", "Unknown"),
                "audience": analysis.get("audience_alignment", {}).get("real_product_consumers", {}).get("who", "General audience"),
                "marketing_angle": analysis.get("marketing_angle", ""),
                "visual_style": analysis.get("visual_style", {}).get("styling_vibe_tags", []),
                "brand_design_guidance": analysis.get("brand_design_guidance", {})
            }
            
            if self.debug:
                logger.info(f"Loaded cache recommendations for {username}: {len(recommendations['recommended_product_types'])} product types")
            
            return recommendations
            
        except Exception as e:
            if self.debug:
                logger.warning(f"Failed to get cache recommendations: {e}")
            return None

    def _get_nocodb_credentials(self):
        """Get NocoDB credentials from environment variables."""
        return {
            'base_url': os.getenv("NC_BASE_URL"),
            'api_token': os.getenv("NC_API_TOKEN"),
            'table_id': os.getenv("NC_TABLE_ID")
        }

    def _validate_inputs(self, credentials: dict) -> None:
        """Validate NocoDB configuration inputs."""
        table_id = credentials.get('table_id')
        base_url = credentials.get('base_url')
        
        if table_id and (table_id.startswith("http://") or table_id.startswith("https://")):
            raise RuntimeError(
                "NC_TABLE_ID appears to be a URL. Did you accidentally set NC_TABLE_ID=base_url? "
                "Set NC_BASE_URL to your instance URL and NC_TABLE_ID to the table hash."
            )
        if "/" in (table_id or ""):
            raise RuntimeError("NC_TABLE_ID should not contain slashes. Provide only the table ID/hash.")
        if base_url and not (base_url.startswith("http://") or base_url.startswith("https://")):
            raise RuntimeError("NC_BASE_URL must start with http:// or https://")

    def _analyze_product_desires(self) -> Dict[str, Any]:
        """Use GPT-4o-mini to analyze product desires and extract key criteria, enhanced with cache recommendations."""
        if not self._openai_client:
            raise RuntimeError("OpenAI API key not configured")

        # Load social media analysis cache for recommended products
        cache_recommendations = self._get_cache_recommendations()
        
        # Build enhanced system prompt with cache recommendations
        cache_context = ""
        if cache_recommendations:
            recommended_products = cache_recommendations.get("recommended_product_types", [])
            archetype = cache_recommendations.get("archetype", "Unknown")
            audience = cache_recommendations.get("audience", "General audience")
            
            cache_context = f"""

CACHE-BASED RECOMMENDATIONS (from social media analysis):
- Influencer Archetype: {archetype}
- Target Audience: {audience}
- Recommended Product Types: {', '.join(recommended_products)}

PRIORITIZE these recommended product types when matching products. If the user's desires align with any of these recommendations, give them higher priority in the analysis."""

        system_prompt = f"""You are a product matching assistant specializing in influencer-driven consumer behavior. Given a natural language description of product desires, extract key information for database search.

Key insight: People want products to EMULATE and BE LIKE the influencers they admire, not just to attract them. Focus on lifestyle, aesthetic, and aspirational matching.{cache_context}

Your response must be a JSON object with the following structure:
{{
    "categories": ["category1", "category2", ...],  // Likely product categories mentioned or implied
    "keywords": ["keyword1", "keyword2", ...],     // Important keywords for product matching
    "attributes": {{                                // Specific product attributes mentioned
        "brand_preference": "specific brand or 'any'",
        "price_range": "budget/mid/premium or 'any'",
        "skin_type": "if skincare related, otherwise null",
        "material": "if relevant, otherwise null",
        "color": "if specific color mentioned, otherwise null",
        "size": "if size mentioned, otherwise null",
        "aesthetic": "style/vibe they want to achieve",
        "lifestyle": "lifestyle they're aspiring to"
    }},
    "must_have": ["essential requirement 1", ...], // Essential requirements
    "nice_to_have": ["optional feature 1", ...],   // Optional features
    "exclude": ["thing to avoid 1", ...],          // Things to exclude
    "emulation_factors": {{                         // What they want to emulate/achieve
        "look": "visual aesthetic they want",
        "feel": "how they want to feel using products",
        "persona": "identity/image they're building"
    }},
    "cache_aligned": ["recommended product type 1", ...], // Product types from cache that align with user desires
    "confidence": 0.8                              // Your confidence in the analysis (0.0-1.0)
}}

Consider aspirational language, lifestyle cues, and identity-building desires. Extract both explicit and implicit desires for self-transformation."""

        try:
            response = self._openai_client.chat.completions.create(
                model=self.analysis_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Analyze these product desires: {self.product_desires}"}
                ],
                temperature=0.1,  # Low temperature for consistent analysis
                response_format={"type": "json_object"}
            )

            analysis_result = json.loads(response.choices[0].message.content)
            
            if self.debug:
                logger.info(f"GPT Analysis Result: {json.dumps(analysis_result, indent=2)}")
                if cache_recommendations:
                    logger.info(f"Cache recommendations applied: {cache_recommendations.get('recommended_product_types', [])}")
            
            return analysis_result

        except Exception as e:
            logger.error(f"Error analyzing product desires with GPT: {e}")
            # Fallback: simple keyword extraction with cache recommendations
            keywords = self.product_desires.lower().split()
            cache_aligned = []
            if cache_recommendations:
                recommended_products = cache_recommendations.get("recommended_product_types", [])
                # Simple matching of keywords to recommended products
                for product_type in recommended_products:
                    if any(keyword in product_type.lower() for keyword in keywords):
                        cache_aligned.append(product_type)
            
            return {
                "categories": [],
                "keywords": keywords,
                "attributes": {},
                "must_have": [],
                "nice_to_have": [],
                "exclude": [],
                "emulation_factors": {},
                "cache_aligned": cache_aligned,
                "confidence": 0.3
            }

    def _fetch_all_products(self, limit: int = 1000) -> List[Dict[str, Any]]:
        """Fetch all products from NocoDB to perform intelligent matching."""
        credentials = self._get_nocodb_credentials()
        self._validate_inputs(credentials)
        
        if not all(credentials.values()):
            raise RuntimeError("Missing NocoDB credentials. Please set NC_BASE_URL, NC_API_TOKEN, and NC_TABLE_ID")
        
        base_url = credentials['base_url'].rstrip("/")
        url = f"{base_url}/api/v2/tables/{credentials['table_id']}/records"

        headers = {
            "xc-token": credentials['api_token'],
            "accept": "application/json",
        }

        params = {
            "limit": limit,
            "offset": 0,
        }

        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            response.raise_for_status()
            
            result = response.json()
            products = result.get("list", [])
            
            if self.debug:
                logger.info(f"Fetched {len(products)} products from NocoDB")
            
            return products

        except requests.HTTPError as exc:
            try:
                err_payload = response.json()
            except Exception:
                err_payload = {"message": response.text}
            raise RuntimeError(f"NocoDB fetch failed: {response.status_code} {err_payload}") from exc

    def _list_categories(self, products: List[Dict[str, Any]]) -> List[str]:
        """Extract distinct categories from product list."""
        cats = set()
        for p in products:
            for key in ["Category", "category", "Product Category", "product_category"]:
                val = p.get(key)
                if isinstance(val, str) and val.strip():
                    cats.add(val.strip())
        return sorted(cats)

    def _filter_by_category(self, products: List[Dict[str, Any]], category: str) -> List[Dict[str, Any]]:
        """Return only products in the specified category (case-insensitive contains)."""
        if not category:
            return products
        
        # Normalize apostrophes to handle different apostrophe types (straight vs curly)
        def normalize_apostrophes(text: str) -> str:
            # Replace various apostrophe types with standard straight apostrophe
            return text.replace("'", "'").replace("'", "'").replace("'", "'").replace("'", "'").replace("'", "'").replace(chr(8217), "'")
        
        cat_lower = normalize_apostrophes(category.lower())
        if self.debug:
            logger.info(f"🔍 Filtering by category: '{category}' -> normalized: '{cat_lower}'")
        
        filtered: List[Dict[str, Any]] = []
        for p in products:
            val = (p.get("Category") or p.get("category") or p.get("Product Category") or p.get("product_category") or "")
            if isinstance(val, str):
                val_normalized = normalize_apostrophes(val.lower())
                if self.debug and "Men" in val:
                    logger.info(f"  Checking product category: '{val}' -> normalized: '{val_normalized}' -> match: {cat_lower in val_normalized}")
                if cat_lower in val_normalized:
                    filtered.append(p)
        
        if self.debug:
            logger.info(f"  -> Found {len(filtered)} products in category '{category}'")
        return filtered

    def _choose_category_via_gpt(self, categories: List[str]) -> str | None:
        """Use GPT to pick ONE best category given social cache recommendations and available categories."""
        try:
            if not self._openai_client:
                return None

            cache_recs = self._get_cache_recommendations() or {}
            rec_types = cache_recs.get("recommended_product_types", []) or []
            archetype = cache_recs.get("archetype", "Unknown")
            audience = cache_recs.get("audience", "General audience")
            marketing = cache_recs.get("marketing_angle", "")

            system = (
                "You select exactly ONE product category from a provided list that best matches the influencer's profile. "
                "Prioritize alignment with recommended product types, archetype, audience, and marketing angle. "
                "If multiple categories fit, choose the one that will most likely convert for this audience. "
                "Return strict JSON with a single field: {\"category\": \"<one from list>\"}."
            )
            user = {
                "available_categories": categories,
                "recommended_product_types": rec_types,
                "archetype": archetype,
                "audience": audience,
                "marketing_angle": marketing,
            }

            resp = self._openai_client.chat.completions.create(
                model=self.analysis_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(user)},
                ],
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            data = json.loads(resp.choices[0].message.content)
            picked = data.get("category")
            if picked and picked in categories:
                return picked

            # Best-effort loose match if model returns near-match text
            if picked:
                picked_lower = picked.lower()
                for c in categories:
                    if picked_lower in c.lower() or c.lower() in picked_lower:
                        return c
            return None
        except Exception as e:
            if self.debug:
                logger.warning(f"GPT category selection failed: {e}")
            return None

    def _calculate_product_match_score(self, product: Dict[str, Any], analysis: Dict[str, Any]) -> float:
        """Calculate how well a product matches the analyzed desires."""
        score = 0.0
        max_score = 0.0

        # Get product fields - handle different possible field names
        name_candidates = [
            "Name", "name", "product_name", "Product Name", "productName",
            "Title", "title", "Title EN", "title_en", "Display Name",
            "display_name", "DisplayName", "Short Name", "short_name",
            "Product", "product", "Label", "label"
        ]
        product_name = ""
        for key in name_candidates:
            if key in product and product.get(key):
                product_name = str(product.get(key))
                break
        # Fallback: use SKU as product name if no title-like field found
        if not product_name:
            for sku_key in ["SKU", "sku", "Sku", "code", "Code", "CODE", "product_sku", "productSKU"]:
                if product.get(sku_key):
                    product_name = str(product.get(sku_key))
                    break
        product_category = product.get("Category") or product.get("category") or ""
        product_description = product.get("Description") or product.get("description") or ""
        product_brand = product.get("Brand") or product.get("brand") or ""
        
        # Combine searchable text
        searchable_text = f"{product_name} {product_category} {product_description} {product_brand}".lower()

        # Category matching (high weight)
        max_score += 30
        for category in analysis.get("categories", []):
            if category.lower() in searchable_text:
                score += 30
                break
        
        # Keyword matching
        keywords = analysis.get("keywords", [])
        if keywords:
            max_score += 25
            matched_keywords = sum(1 for keyword in keywords if keyword.lower() in searchable_text)
            score += (matched_keywords / len(keywords)) * 25

        # Must-have requirements (critical)
        must_have = analysis.get("must_have", [])
        if must_have:
            max_score += 35
            matched_requirements = sum(1 for req in must_have if req.lower() in searchable_text)
            if matched_requirements == len(must_have):
                score += 35
            else:
                # Penalty for missing must-have requirements
                score -= 10

        # Nice-to-have features
        nice_to_have = analysis.get("nice_to_have", [])
        if nice_to_have:
            max_score += 10
            matched_nice = sum(1 for feature in nice_to_have if feature.lower() in searchable_text)
            score += (matched_nice / len(nice_to_have)) * 10

        # Exclusion penalties
        exclude = analysis.get("exclude", [])
        for excluded_item in exclude:
            if excluded_item.lower() in searchable_text:
                score -= 20  # Penalty for excluded items

        # Attributes matching
        attributes = analysis.get("attributes", {})
        max_score += 15
        attribute_score = 0
        for attr_key, attr_value in attributes.items():
            if attr_value and attr_value.lower() != "any":
                if attr_value.lower() in searchable_text:
                    # Give higher weight to aesthetic and lifestyle attributes
                    weight = 3 if attr_key in ["aesthetic", "lifestyle"] else 2
                    attribute_score += weight

        score += min(attribute_score, 15)

        # Emulation factors (new scoring category)
        emulation_factors = analysis.get("emulation_factors", {})
        if emulation_factors:
            max_score += 20
            emulation_score = 0
            for factor_key, factor_value in emulation_factors.items():
                if factor_value:
                    # Check if emulation desires match product descriptions
                    factor_words = factor_value.lower().split()
                    matched_words = sum(1 for word in factor_words if word in searchable_text)
                    if matched_words > 0:
                        emulation_score += (matched_words / len(factor_words)) * 7

            score += min(emulation_score, 20)

        # Cache-aligned products (highest priority)
        cache_aligned = analysis.get("cache_aligned", [])
        if cache_aligned:
            max_score += 40  # High weight for cache recommendations
            cache_score = 0
            for recommended_type in cache_aligned:
                if recommended_type.lower() in searchable_text:
                    cache_score += 20  # High score for exact matches
                else:
                    # Partial matching for related terms
                    recommended_words = recommended_type.lower().split()
                    matched_words = sum(1 for word in recommended_words if word in searchable_text)
                    if matched_words > 0:
                        cache_score += (matched_words / len(recommended_words)) * 10
            
            score += min(cache_score, 40)

        # Normalize score (0.0 to 1.0)
        if max_score > 0:
            normalized_score = max(0, score) / max_score
        else:
            normalized_score = 0

        return min(1.0, normalized_score)  # Cap at 1.0

    def _rank_products(self, products: List[Dict[str, Any]], analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Rank products based on how well they match the analyzed desires. Always returns the best matches."""
        scored_products = []
        
        for product in products:
            match_score = self._calculate_product_match_score(product, analysis)
            
            product_with_score = product.copy()
            product_with_score["_match_score"] = round(match_score, 3)
            product_with_score["_match_confidence"] = "high" if match_score >= 0.8 else "medium" if match_score >= 0.5 else "low"
            scored_products.append(product_with_score)

        # Sort by match score (highest first) and always return the top matches
        scored_products.sort(key=lambda x: x["_match_score"], reverse=True)
        
        # Always return the top matches, regardless of absolute score
        return scored_products[:self.max_results]

    def run(self) -> str:
        """Execute the product retrieval based on natural language desires."""
        try:
            logger.info(f"Analyzing product desires: '{self.product_desires}'")
            
            # Step 1: Analyze the product desires using GPT
            analysis = self._analyze_product_desires()
            
            # Log analysis confidence for debugging
            analysis_confidence = analysis.get("confidence", 0)
            if self.debug:
                logger.info(f"Analysis confidence: {analysis_confidence}")

            # Step 2: Fetch all products from database
            all_products = self._fetch_all_products()

            # Step 2.1: Determine category per mode and filter to a single category
            selected_category: str | None = None
            if self.mode == "user" and self.user_category:
                selected_category = self.user_category
            elif self.mode == "ai":
                categories = self._list_categories(all_products)
                # Try GPT to pick one; fallback to first cache recommendation; else first category
                selected_category = self._choose_category_via_gpt(categories)
                if not selected_category:
                    cache_recs = self._get_cache_recommendations() or {}
                    rec_types = cache_recs.get("recommended_product_types", [])
                    if isinstance(rec_types, list) and rec_types:
                        # choose the rec type that best matches any category
                        rec_lower = [r.lower() for r in rec_types]
                        best = None
                        for c in categories:
                            cl = c.lower()
                            if any(r in cl or cl in r for r in rec_lower):
                                best = c
                                break
                        selected_category = best or (categories[0] if categories else None)
            # 'ask' mode: return a category list for the agent to ask the user
            if self.mode == "ask" and not self.user_category:
                categories = self._list_categories(all_products)
                return json.dumps({
                    "success": True,
                    "message": "Please ask the user to select a category or choose AI mode.",
                    "available_categories": categories
                }, ensure_ascii=False, indent=2)

            # If we have a selected category, limit products strictly to that category
            if selected_category:
                all_products = self._filter_by_category(all_products, selected_category)
            
            if not all_products:
                return json.dumps({
                    "success": True,
                    "message": "No products found in database",
                    "products": [],
                    "analysis": analysis,
                    "total_products_searched": 0
                })

            # Step 3: Rank products based on analysis
            matching_products = self._rank_products(all_products, analysis)

            # Step 4: Return only minimal product info: title, SKU, category, price
            def _extract_title(product: Dict[str, Any]) -> str:
                # Try a wide range of likely title/name fields
                title_keys = [
                    "Name", "name", "product_name", "Product Name", "productName",
                    "Title", "title", "Title EN", "title_en", "Display Name",
                    "display_name", "DisplayName", "Short Name", "short_name",
                    "Product", "product", "Label", "label"
                ]
                for key in title_keys:
                    value = product.get(key)
                    if value:
                        return str(value)
                # Fallback to SKU as title to avoid "Unknown Product"
                for sku_key in ["SKU", "sku", "Sku", "code", "Code", "CODE", "product_sku", "productSKU"]:
                    value = product.get(sku_key)
                    if value:
                        return str(value)
                return "Unknown Product"

            def _extract_sku(product: Dict[str, Any]) -> Optional[str]:
                # Try common SKU field variants
                for key in [
                    "SKU",
                    "sku",
                    "Sku",
                    "product_sku",
                    "Product SKU",
                    "productSKU",
                    "code",
                    "Code",
                    "CODE",
                ]:
                    value = product.get(key)
                    if value:
                        return str(value)
                return None

            def _extract_category(product: Dict[str, Any]) -> Optional[str]:
                for key in [
                    "Category",
                    "category",
                    "Product Category",
                    "product_category",
                ]:
                    value = product.get(key)
                    if value:
                        return str(value)
                return None

            def _extract_price(product: Dict[str, Any]) -> Optional[float]:
                # Look for common price keys; return a float if possible
                possible_keys = [
                    "Price",
                    "price",
                    "PRICE",
                    "Price USD",
                    "price_usd",
                    "unit_price",
                    "Unit Price",
                    "cost",
                    "Cost",
                ]
                for key in possible_keys:
                    if key in product:
                        value = product.get(key)
                        if value is None:
                            continue
                        # Numeric already
                        if isinstance(value, (int, float)):
                            try:
                                return round(float(value), 2)
                            except Exception:
                                continue
                        # String: extract first number
                        if isinstance(value, str):
                            import re
                            m = re.search(r"[-+]?[0-9]*\.?[0-9]+", value.replace(",", ""))
                            if m:
                                try:
                                    return round(float(m.group(0)), 2)
                                except Exception:
                                    pass
                return None

            minimal_products: List[Dict[str, Any]] = []
            for p in matching_products:
                sku_value = _extract_sku(p)
                if not sku_value:
                    continue  # Skip items without a resolvable SKU
                minimal_entry: Dict[str, Any] = {
                    "title": _extract_title(p),
                    "sku": sku_value,
                }
                cat = _extract_category(p)
                if cat:
                    minimal_entry["category"] = cat
                price = _extract_price(p)
                if price is not None:
                    minimal_entry["price"] = price
                minimal_products.append(minimal_entry)

            result = {
                "success": True,
                "message": f"Found {len(minimal_products)} best matching products for your desires",
                "products": minimal_products,
                "total_products_searched": len(all_products),
                "selected_category": selected_category,
            }

            if self.debug:
                logger.info(f"Successfully retrieved {len(matching_products)} matching products")

            return json.dumps(result, ensure_ascii=False, indent=2)

        except Exception as e:
            error_result = {
                "success": False,
                "error": str(e),
                "message": "Failed to retrieve product data",
                "products": []
            }
            logger.error(f"Error in ProductDataRetriever: {e}")
            return json.dumps(error_result, ensure_ascii=False, indent=2)


# Test function for development
def test_product_data_retriever():
    """Test the ProductDataRetriever with sample data."""
    load_dotenv()
    
    # Test cases
    test_cases = [
        "I want organic skincare products for sensitive skin",
        "Looking for premium moisturizers and serums",
        "Need affordable makeup products, especially lipsticks",
        "Want natural hair care products for dry hair"
    ]
    
    for test_case in test_cases:
        print(f"\n--- Testing: {test_case} ---")
        tool = ProductDataRetriever(product_desires=test_case, max_results=5, confidence_threshold=0.0, debug=True)
        result = tool.run()
        print(result)


if __name__ == "__main__":
    test_product_data_retriever()
