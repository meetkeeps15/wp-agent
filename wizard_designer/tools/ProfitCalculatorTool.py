import os
import json
import logging
from typing import List, Dict, Any, Optional
from pathlib import Path

import requests
from agency_swarm.tools import BaseTool
from pydantic import Field
from dotenv import load_dotenv


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


class ProfitCalculatorTool(BaseTool):
    """
    MVP Profit Calculator: simple follower → buyer benchmark.

    - Pulls non-membership base cost from NocoDB (e.g., "Non Member Pricing (T1)" or similar fields)
    - Requires a single user-provided retail price
    - Uses conversion_rate (default 2%) and follower_count (from social cache if available; otherwise must be provided)
    - Computes: profit_per_unit = retail_price - base_cost; estimated_buyers = followers × conversion_rate; earnings = buyers × profit_per_unit
    """

    skus: List[str] = Field(..., description="List of product SKUs to calculate profit for")
    retail_price: Optional[float] = Field(None, description="Suggested Retail Price to test margins and earnings")
    conversion_rate: float = Field(default=0.02, description="Benchmark conversion rate from followers to buyers (0-1). Default 0.02 (2%)")
    followers: Optional[int] = Field(default=None, description="Follower count to use. If omitted, attempts to read from social analysis cache.")
    check_price_only: bool = Field(default=False, description="If true, return only base (non-member) cost per SKU; other inputs are ignored.")
    debug: bool = Field(default=True, description="Enable debug logging")

    def __init__(self, **data):
        super().__init__(**data)

    def _validate_tool_inputs(self) -> bool:
        """Validate the tool input parameters."""
        if not self.skus:
            logger.error("No SKUs provided")
            return False
        if not self.check_price_only:
            if self.retail_price is None or self.retail_price <= 0:
                logger.error("Retail price must be provided and positive")
                return False
            if not (0 <= self.conversion_rate <= 1):
                logger.error("Conversion rate must be between 0 and 1")
                return False
        if self.followers is not None and self.followers < 0:
            logger.error("Followers cannot be negative")
            return False
            
        return True

    def _get_session_id_from_headers(self) -> str:
        """Extract session ID from agency headers or generate a new one."""
        try:
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

    def _get_follower_count_from_cache(self) -> int:
        """Get follower count from cached social media analysis."""
        try:
            session_id = self._get_session_id_from_headers()
            current_dir = Path(__file__).parent
            analysis_dir = current_dir.parent / "cache" / "social_media_analysis"
            
            if not analysis_dir.exists():
                if self.debug:
                    logger.warning(f"Analysis directory not found: {analysis_dir}")
                return 0
            
            # Find the latest analysis file for this session
            pattern = f"*_{session_id}_*.json"
            files = list(analysis_dir.glob(pattern))
            
            if not files:
                if self.debug:
                    logger.warning(f"No analysis files found for session: {session_id}")
                return 0
            
            # Get the most recent file
            latest_file = max(files, key=lambda f: f.stat().st_mtime)
            if self.debug:
                logger.info(f"Loading follower data from: {latest_file}")
            
            with open(latest_file, 'r') as f:
                data = json.load(f)
                
            # Try to get follower count from different possible locations
            analysis = data.get("analysis", {})
            profile = analysis.get("profile", {}) if "analysis" in data else data.get("profile", {})
            
            follower_count = (
                profile.get("followersCount") or 
                profile.get("followers_count") or 
                profile.get("followers") or 0
            )
            
            if self.debug:
                logger.info(f"Found follower count: {follower_count}")
            
            return int(follower_count)
                
        except Exception as e:
            if self.debug:
                logger.warning(f"Failed to get follower count from cache: {e}")
            return 0

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

    def _fetch_products_by_skus(self, skus: List[str]) -> List[Dict[str, Any]]:
        """Fetch specific products from NocoDB by SKU list."""
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

        # Fetch all products and filter by SKUs (NocoDB filtering can be tricky with OR conditions)
        params = {
            "limit": 1000,
            "offset": 0,
        }

        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            response.raise_for_status()
            
            result = response.json()
            all_products = result.get("list", [])
            
            # Filter products by SKU
            matching_products = []
            for product in all_products:
                # Check various possible SKU field names
                sku_candidates = [
                    "SKU", "sku", "Sku", "product_sku", "Product SKU", "productSKU",
                    "code", "Code", "CODE"
                ]
                product_sku = None
                for sku_key in sku_candidates:
                    if sku_key in product and product.get(sku_key):
                        product_sku = str(product.get(sku_key))
                        break
                
                if product_sku and product_sku in skus:
                    matching_products.append(product)
            
            if self.debug:
                logger.info(f"Found {len(matching_products)} products matching SKUs: {skus}")
            
            return matching_products

        except requests.HTTPError as exc:
            try:
                err_payload = response.json()
            except Exception:
                err_payload = {"message": response.text}
            raise RuntimeError(f"NocoDB fetch failed: {response.status_code} {err_payload}") from exc

    def _extract_price_from_field(self, product: Dict[str, Any], field_candidates: List[str]) -> Optional[float]:
        """Extract price from product data using field candidates."""
        for key in field_candidates:
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
                
                # String: handle special cases and extract price
                if isinstance(value, str):
                    # Skip Excel error values
                    if value.strip() in ["#REF!", "#N/A", "#VALUE!", "#ERROR!", ""]:
                        continue
                    
                    import re
                    # Remove dollar signs and other currency symbols, then extract number
                    cleaned_value = value.replace("$", "").replace(",", "").strip()
                    m = re.search(r"[-+]?[0-9]*\.?[0-9]+", cleaned_value)
                    if m:
                        try:
                            return round(float(m.group(0)), 2)
                        except Exception:
                            pass
        return None

    def _extract_base_cost(self, product: Dict[str, Any]) -> Optional[float]:
        """Extract the non-membership/base product cost from likely fields."""
        # Prefer explicit non-member or base cost fields
        base_cost_candidates = [
            # Explicit non-member pricing (from previous schema)
            "Non Member Pricing\n(T1)", "Non Member Pricing (T1)", "T1",
            "non_member_price", "Non_Member_Price",
            # Generic cost fields
            "Base Cost", "Cost", "Cost Price", "Unit Cost", "Wholesale Cost", "COGS",
            "base_cost", "cost", "cost_price", "unit_cost", "wholesale_cost", "cogs",
            # Other common labels
            "standard_price", "Standard Price"
        ]
        price = self._extract_price_from_field(product, base_cost_candidates)
        return price

    def _extract_membership_prices(self, product: Dict[str, Any]) -> Dict[str, Optional[float]]:
        """Backward-compat: not used in MVP; returns only a 'Base (Non-Member)' mapping if available."""
        base_cost = self._extract_base_cost(product)
        return {"Base (Non-Member)": base_cost}

    def _extract_product_info(self, product: Dict[str, Any]) -> Dict[str, Any]:
        """Extract key product information."""
        # Extract title/name
        name_candidates = [
            "Name", "name", "product_name", "Product Name", "productName",
            "Title", "title", "Title EN", "title_en", "Display Name",
            "display_name", "DisplayName", "Short Name", "short_name",
            "Product", "product", "Label", "label"
        ]
        
        product_name = "Unknown Product"
        for key in name_candidates:
            if key in product and product.get(key):
                product_name = str(product.get(key))
                break
        
        # Extract SKU
        sku_candidates = [
            "SKU", "sku", "Sku", "product_sku", "Product SKU", "productSKU",
            "code", "Code", "CODE"
        ]
        
        product_sku = None
        for key in sku_candidates:
            if key in product and product.get(key):
                product_sku = str(product.get(key))
                break
        
        # Extract category
        category = product.get("Category") or product.get("category") or "Unknown"
        
        # Extract base cost from NocoDB (non-membership)
        membership_prices = self._extract_membership_prices(product)
        
        return {
            "name": product_name,
            "sku": product_sku,
            "category": str(category),
            "membership_prices": membership_prices
        }

    def _calculate_profit_simple(self, base_cost: float, retail_price: float, followers: int, conversion_rate: float) -> Dict[str, Any]:
        """Simple MVP profit calc: unit profit and earnings estimate."""
        profit_per_unit = max(0.0, retail_price - base_cost)
        estimated_buyers = int(max(0, followers) * conversion_rate)
        estimated_earnings = round(profit_per_unit * estimated_buyers, 2)
        margin_pct = f"{(profit_per_unit / retail_price * 100):.1f}%" if retail_price > 0 else "0%"
        return {
            "base_cost": round(base_cost, 2),
            "retail_price": round(retail_price, 2),
            "profit_per_unit": round(profit_per_unit, 2),
            "margin": margin_pct,
            "followers": followers,
            "conversion_rate": f"{conversion_rate*100:.1f}%",
            "estimated_buyers": estimated_buyers,
            "estimated_earnings": estimated_earnings,
        }

    def run(self) -> str:
        """Execute the MVP profit calculation for the given SKUs."""
        try:
            # Validate tool inputs first
            if not self._validate_tool_inputs():
                return json.dumps({
                    "success": False,
                    "message": "Invalid input parameters"
                })
            
            if self.debug:
                mode = "check_price_only" if self.check_price_only else "mvp_simple"
                logger.info(f"Calculating ({mode}) for SKUs: {self.skus}")

            # If only checking prices, skip follower/retail validations in runtime and return prices
            if self.check_price_only:
                products = self._fetch_products_by_skus(self.skus)
                if not products:
                    return json.dumps({
                        "success": False,
                        "message": f"No products found for SKUs: {self.skus}",
                        "products": []
                    })

                product_prices = []
                for product in products:
                    info = self._extract_product_info(product)
                    base_cost = info["membership_prices"].get("Base (Non-Member)")
                    product_prices.append({
                        "product": {
                            "name": info["name"],
                            "sku": info["sku"],
                            "category": info["category"]
                        },
                        "price_check": {
                            "base_cost": round(base_cost, 2) if base_cost is not None else None
                        }
                    })

                return json.dumps({
                    "success": True,
                    "message": f"Base costs fetched for {len(product_prices)} products",
                    "calculation_parameters": {"mode": "check_price_only"},
                    "products": product_prices,
                    "summary": {
                        "products_analyzed": len(product_prices),
                        "skus_requested": len(self.skus)
                    }
                }, ensure_ascii=False, indent=2)
            
            # Resolve followers: use provided, else try cache, else error
            followers = self.followers if self.followers is not None else self._get_follower_count_from_cache()
            if followers in (None, 0):
                return json.dumps({
                    "success": False,
                    "message": "Follower count not found in social cache. Please provide 'followers' input.",
                    "hint": "Provide followers, or run SocialMediaAnalyzer first to populate cache.",
                })
            
            # Fetch products from NocoDB
            products = self._fetch_products_by_skus(self.skus)
            
            if not products:
                return json.dumps({
                    "success": False,
                    "message": f"No products found for SKUs: {self.skus}",
                    "products": []
                })
            
            # Calculate MVP profit for each product
            product_calculations = []
            total_estimated_earnings = 0.0
            
            for product in products:
                product_info = self._extract_product_info(product)
                base_cost = product_info["membership_prices"].get("Base (Non-Member)")
                if base_cost is None:
                    if self.debug:
                        logger.warning(f"No base (non-member) cost found for product {product_info.get('sku')}, skipping")
                    continue
                    
                simple_calc = self._calculate_profit_simple(
                    base_cost=base_cost,
                    retail_price=self.retail_price,
                    followers=followers,
                    conversion_rate=self.conversion_rate,
                )
                total_estimated_earnings += simple_calc["estimated_earnings"]
                
                product_calculation = {
                    "product": {
                        "name": product_info["name"],
                        "sku": product_info["sku"],
                        "category": product_info["category"],
                        "base_cost": round(base_cost, 2)
                    },
                    "mvp_profit": simple_calc
                }
                
                product_calculations.append(product_calculation)
                
            calculation_parameters = {
                "mode": "mvp_simple",
                "retail_price": self.retail_price,
                "conversion_rate": f"{self.conversion_rate*100:.1f}%",
                "followers": followers
            }
            
            result = {
                "success": True,
                "message": f"MVP profit calculated for {len(product_calculations)} products",
                "calculation_parameters": calculation_parameters,
                "products": product_calculations,
                "summary": {
                    "products_analyzed": len(product_calculations),
                    "skus_requested": len(self.skus),
                    "total_estimated_earnings": round(total_estimated_earnings, 2)
                }
            }
            
            if self.debug:
                logger.info(f"Successfully calculated MVP profit for {len(product_calculations)} products")
            
            return json.dumps(result, ensure_ascii=False, indent=2)

        except Exception as e:
            error_result = {
                "success": False,
                "error": str(e),
                "message": "Failed to calculate profit",
                "products": []
            }
            logger.error(f"Error in ProfitCalculatorTool: {e}")
            return json.dumps(error_result, ensure_ascii=False, indent=2)


# Test function for development
def test_profit_calculator():
    """Test the MVP ProfitCalculatorTool with simple follower→buyer benchmark."""
    load_dotenv()
    
    # Test with sample SKUs
    test_skus = ["ROC502", "ROC816", "TEST_SKU"]
    
    tool = ProfitCalculatorTool(
        skus=test_skus, 
        retail_price=49.99,
        conversion_rate=0.02,
        followers=100000,
        debug=True,
    )
    result = tool.run()
    print(result)


if __name__ == "__main__":
    test_profit_calculator()
