from agency_swarm.tools import BaseTool
from pydantic import Field
from dotenv import load_dotenv
import os
import requests  # type: ignore
import json
import logging
from functools import lru_cache, wraps

# Lightweight local logger to avoid external utils dependency
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

# Load env before reading any API keys
load_dotenv()

# Optional dependencies
try:
    import textrazor  # type: ignore
except Exception:
    textrazor = None  # type: ignore

try:
    import whois  # type: ignore
except Exception:
    whois = None  # type: ignore

try:
    from agencii.tools.CEOAgent.CustomerMemory import CustomerMemory  # type: ignore
except Exception:
    CustomerMemory = None  # type: ignore

COMPETITOR_KEYWORDS = [
    "vape",
    "smoke",
    "cigarette",
    "tobacco",
    "nicotine",
    "alcohol",
    "beer",
    "wine",
    "liquor",
    "pharmaceutical",
    "prescription",
    "drugs",
]
# Initialize TextRazor client only if available and configured
_TR_API_KEY = os.getenv("TEXTRAZOR_API_KEY")
if textrazor and _TR_API_KEY:
    textrazor.api_key = _TR_API_KEY
    client = textrazor.TextRazor(extractors=["topics"])  # type: ignore
else:
    client = None


def log_cache_calls(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        before = func.cache_info().hits
        result = func(*args, **kwargs)
        after = func.cache_info().hits

        if after > before:
            logger.info(f"✅ Cache hit for {func.__name__} with args: {args}")
        else:
            logger.info(f"❌ API call made for {func.__name__} with args: {args}")

        return result

    return wrapper


@log_cache_calls
@lru_cache(maxsize=100)
def cached_get_google_search_results(brand_name):
    url = "https://www.googleapis.com/customsearch/v1"

    try:
        api_key = os.getenv("GOOGLE_API_KEY")
        search_engine_id = os.getenv("SEARCH_ENGINE_ID")
        if not api_key or not search_engine_id:
            logger.warning("GOOGLE_API_KEY or SEARCH_ENGINE_ID not set; skipping search")
            return 0, [], []

        query = f'"{brand_name}"'

        params = {
            "key": api_key,
            "cx": search_engine_id,
            "q": query,
            "num": 10,
        }

        response = requests.get(url, params=params, timeout=15)
        data = response.json()

        total_results = int(data.get("searchInformation", {}).get("totalResults", 0))
        items = data.get("items", [])
        snippets = [item.get("htmlSnippet", "") for item in items]
        return total_results, items, snippets

    except Exception as e:
        logger.error(f"Error in _get_google_search_results: {str(e)}")
        return 0, [], []


@log_cache_calls
@lru_cache(maxsize=100)
def cached_extract_high_confidence_topics(snippets_tuple, min_score=0.7):
    try:
        if not client:
            logger.warning("TextRazor unavailable; skipping topic extraction")
            return [], []
        snippets = list(snippets_tuple)
        combined_text = " ".join(snippets)
        logger.info(f"Combined text: {combined_text}")
        response = client.analyze(text=combined_text)  # type: ignore
        response_json = dict(response.json)

        topics = set()
        competitor_topics = set()  # New set to track competitor-related topics

        for item in response_json.get("response", {}).get("coarseTopics", []):
            if item.get("score", 0) > min_score:
                label = item.get("label", "").lower()

                # Check if topic contains competitor keywords
                if any(keyword in label for keyword in COMPETITOR_KEYWORDS):
                    competitor_topics.add(label)
                else:
                    topics.add(label)

        for item in response_json.get("response", {}).get("topics", []):
            if item.get("score", 0) > min_score:
                label = item.get("label", "").lower()

                # Check if topic contains competitor keywords
                if any(keyword in label for keyword in COMPETITOR_KEYWORDS):
                    competitor_topics.add(label)
                else:
                    topics.add(label)

        return list(topics), list(competitor_topics)

    except Exception as e:
        logger.error(f"Error in _extract_high_confidence_topics: {str(e)}")
        return [], []


@log_cache_calls
@lru_cache(maxsize=100)
def cached_whois_lookup(domain, tlds=("com", "net", "org")):
    results = {}
    # Clean domain name: remove any existing TLD and spaces
    base_domain = domain.lower().strip()
    for tld in ["com", "net", "org"]:  # Remove any existing TLD
        base_domain = base_domain.replace(f".{tld}", "")

    logger.info(f"Base domain for checking: {base_domain}")

    for tld in tlds:
        try:
            full_domain = f"{base_domain}.{tld}"
            logger.info(f"Checking domain: {full_domain}")
            if not whois:
                raise RuntimeError("whois library not available")
            w = whois.whois(full_domain)  # type: ignore
            details = {
                "creation_date": w.creation_date,
                "expiration_date": w.expiration_date,
                "updated_date": w.updated_date,
            }
            result = {"available": w.creation_date is None, "info": details}
            status = "AVAILABLE" if result["available"] else "TAKEN"
            results[full_domain] = {
                "status": status,
            }
        except Exception as e:
            logger.error(f"Error in _whois_lookup: {str(e)}")
            results[full_domain] = {
                "status": "LIKELY_AVAILABLE",
            }
    return results


class DomainValidationTool(BaseTool):
    """
    A tool for validating domain availability and analyzing potential brand name conflicts.
    """

    domain: str = Field(
        None, description="The domain name to validate (e.g., 'luxenova')"
    )

    def _get_google_search_results(self, brand_name):
        """Get Google search results with caching"""
        return cached_get_google_search_results(brand_name)

    def _extract_high_confidence_topics(self, snippets, min_score=0.7):
        return cached_extract_high_confidence_topics(tuple(snippets), min_score)

    def _compute_competition_score(
        self, result_count, competitor_topics, total_results=0
    ):
        """
        Compute competition score based on result count, competitor topics, and total results
        Returns "Low", "Medium", or "High"
        """
        score = 0

        # Base score from result count and total results
        if (
            total_results > 10000000
        ):  # If more than 10M results, consider it high competition
            score += 4  # This will force "High" competition level
        elif total_results > 1000000:  # If more than 1M results
            score += 2
        elif result_count <= 3:
            score += 0
        elif result_count <= 6:
            score += 1
        else:
            score += 2

        # Additional score for competitor topics
        if len(competitor_topics) >= 2:
            score += 2
        elif len(competitor_topics) == 1:
            score += 1

        logger.info("Competition score breakdown:")
        logger.info(f"Result count score: {score}")
        logger.info(f"Total results: {total_results}")
        logger.info(f"Competitor topics found: {competitor_topics}")

        if score <= 1:
            return "Low"
        elif score <= 2:  # Adjusted threshold to make it harder to get "Medium"
            return "Medium"
        else:
            return "High"

    def _whois_lookup(self, tlds=("com", "net", "org")):
        return cached_whois_lookup(self.domain, tlds)

    def _analyze_brand_name_competition(self, brand_name):
        """Analyze brand name competition using Google search and topic analysis"""
        total_results, items, snippets = self._get_google_search_results(brand_name)
        result_count = len(items)
        topics, competitor_topics = self._extract_high_confidence_topics(snippets)
        logger.info(f"Topics: {topics}")
        logger.info(f"Competitor topics: {competitor_topics}")
        score = self._compute_competition_score(
            result_count, competitor_topics, total_results
        )
        logger.info(f"Score: {score}")
        return {
            "brand": brand_name,
            "competition_level": score,
            "google_total_results": total_results,
            "real_result_count": result_count,
            "brand_relevant_topics": topics,
            "competitor_topics": competitor_topics,
        }

    def _calculate_viability_score(self, domain_results, competition_results):
        """Calculate overall viability score (0-10) based on domain and competition analysis"""

        # Domain availability score (0-5)
        available_domains = sum(
            1
            for d in domain_results.values()
            if d["status"] in ["AVAILABLE", "LIKELY_AVAILABLE"]
        )
        domain_score = min(
            available_domains * 1.67, 5
        )  # 1.67 points per available domain, max 5

        # Competition score (0-5)
        competition_scores = {"Low": 5, "Medium": 3, "High": 1}
        competition_score = competition_scores.get(
            competition_results["competition_level"], 0
        )

        # Calculate final score (simple sum of both scores)
        final_score = min(domain_score + competition_score, 10)

        return round(final_score, 1)

    def run(self):
        """
        Unified brand validation that combines domain availability and competition analysis.
        Returns comprehensive results with viability score and recommendation.
        """
        try:
            # Input validation
            if not self.domain or len(self.domain.strip()) == 0:
                return {
                    "status": "error",
                    "error": "Domain name cannot be empty",
                    "brand": self.domain,
                }

            # Check domain availability
            domain_results = self._whois_lookup()
            logger.info(f"Domain Results: {domain_results}")

            # Analyze brand name competition
            competition_results = self._analyze_brand_name_competition(self.domain)
            logger.info(
                f"Competition Results: {json.dumps(competition_results, indent=4)}"
            )

            # Calculate viability score
            viability_score = self._calculate_viability_score(
                domain_results, competition_results
            )
            logger.info(f"Viability Score: {viability_score}/10")

            # Determine recommendation
            if viability_score >= 7:
                recommendation = "PROCEED"
                reason = (
                    "High viability score indicates good potential for brand success"
                )
            elif viability_score >= 4:
                recommendation = "CAUTION"
                reason = "Moderate concerns with either domain availability or market competition"
            else:
                recommendation = "RECONSIDER"
                reason = "Significant challenges with domain availability and/or market competition"

            # Combine results
            response = {
                "status": "success",
                "brand_name": self.domain,
                "domain_availability": domain_results,
                "competition_analysis": competition_results,
                "viability_metrics": {
                    "score": viability_score,
                    "available_domains": sum(
                        1
                        for d in domain_results.values()
                        if d["status"] in ["AVAILABLE", "LIKELY_AVAILABLE"]
                    ),
                    "competitor_topics_found": len(
                        competition_results["competitor_topics"]
                    ),
                    "competition_level": competition_results["competition_level"],
                },
                "recommendation": {"decision": recommendation, "reason": reason},
            }

            # Store results in CustomerMemory (optional)
            if CustomerMemory:
                try:
                    memory_tool = CustomerMemory(
                        operation="store",
                        user_id=self._shared_state.get("user_id"),
                        conversation=[
                            {
                                "role": "system",
                                "content": f"Brand Name Validation Results for: {self.domain}",
                            },
                            {
                                "role": "assistant",
                                "content": json.dumps(response, indent=2),
                            },
                        ],
                        metadata={
                            "type": "brand_validation",
                            "brand_name": self.domain,
                            "viability_score": viability_score,
                            "recommendation": recommendation,
                        },
                    )
                    memory_tool.run()
                    logger.info("Stored validation results in CustomerMemory")
                except Exception as e:
                    logger.error(f"Failed to store in CustomerMemory: {str(e)}")
                    # Continue execution even if storage fails
            else:
                logger.info("CustomerMemory not available; skipping storage")

            return response

        except Exception as e:
            logger.error(f"Error in brand validation: {str(e)}")
            return {"status": "error", "brand": self.domain, "error": str(e)}


if __name__ == "__main__":
    try:
        # Test cases
        test_domains = [
            # "verdenexus",  # Test a likely available domain
            "google",  # Test a definitely taken domain
            # "storm-syndicate"  # Test with hyphen
        ]

        logger.info("Starting DomainValidationTool tests...")

        for test_domain in test_domains:
            logger.info(f"\nTesting domain: {test_domain}")

            # Initialize tool
            validator = DomainValidationTool(domain=test_domain)

        # Run validation
        logger.info("Running domain validation 1... (should be uncached)")
        result = validator.run()

        logger.info("Running domain validation 2... (should be cached)")
        result = validator.run()

        # Pretty print results
        print(f"\nResults for {test_domain}:")
        print(json.dumps(result, indent=2))

        # Add a separator for readability
        print("\n" + "=" * 50)

    except Exception as e:
        logger.error(f"Test execution failed: {str(e)}", exc_info=True)
        print(f"Error: {str(e)}")
