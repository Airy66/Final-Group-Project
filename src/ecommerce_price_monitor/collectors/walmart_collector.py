"""Walmart product data collector."""

import json
import os
import re
import time
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from bs4 import BeautifulSoup

from .base_collector import BaseCollector, ProductData
from ..utils.exceptions import CollectorError


class WalmartCollector(BaseCollector):
    """Walmart-specific product data collector."""
    
    def __init__(self):
        super().__init__("Walmart")
        self.base_url = "https://www.walmart.com"
        self.search_url = "https://www.walmart.com/search"
        self.last_search_diagnostics = {}

        # Walmart blocks bare requests aggressively. Keep these close to a
        # normal browser request while still using the shared BaseCollector
        # retry/rate-limit behavior.
        self.session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/126.0.0.0 Safari/537.36'
            ),
            'Accept': (
                'text/html,application/xhtml+xml,application/xml;q=0.9,'
                'image/avif,image/webp,*/*;q=0.8'
            ),
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Sec-CH-UA': '"Chromium";v="126", "Google Chrome";v="126", ";Not A Brand";v="99"',
            'Sec-CH-UA-Mobile': '?0',
            'Sec-CH-UA-Platform': '"Windows"',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
            'Referer': self.base_url + '/',
            'Upgrade-Insecure-Requests': '1',
        })
    
    def search_products(self, query: str, max_results: int = 20) -> List[ProductData]:
        """Search for products on Walmart.
        
        Args:
            query: Search query string
            max_results: Maximum number of results to return
            
        Returns:
            List of product data
        """
        products = []
        page = 1
        self.last_search_diagnostics = {
            "original_query": query,
            "normalized_query": query,
            "search_scope": "walmart",
            "data_source": None,
            "platform_filter": None,
            "request_url": None,
            "request_headers": None,
            "response_status_code": None,
            "response_final_url": None,
            "response_text_preview": None,
            "page_type": "error",
            "raw_record_count": 0,
            "normalized_record_count": 0,
            "final_matched_count": 0,
            "rejection_reasons": [],
            "bot_check_detected": False,
        }
        
        while len(products) < max_results:
            try:
                params = {
                    'query': query,
                    'page': page
                }
                request_headers = dict(self.session.headers)
                response = self._make_walmart_request(self.search_url, params=params)
                soup = BeautifulSoup(response.content, 'html.parser')
                page_text = response.text or ""
                page_title = soup.title.get_text(" ", strip=True) if soup.title else ""
                redirect_chain = " -> ".join([resp.url for resp in getattr(response, "history", [])] + [getattr(response, "url", "")])
                request_url = response.request.url if getattr(response, "request", None) is not None else None
                page_type = "product_results"
                if self._is_block_page(soup):
                    page_type = "bot_check"
                elif not page_text.strip():
                    page_type = "error"
                elif not list(self._extract_json_product_candidates(soup)) and not soup.select('[data-testid="item-stack"], .search-result-gridview-item, .search-result-product-tile, a[href*="/ip/"]'):
                    page_type = "no_results"
                self.last_search_diagnostics.update({
                    "request_url": request_url,
                    "request_headers": request_headers,
                    "response_status_code": getattr(response, "status_code", None),
                    "response_final_url": getattr(response, "url", None),
                    "response_text_preview": page_text[:300],
                    "page_type": page_type,
                    "bot_check_detected": page_type == "bot_check",
                })
                self.logger.debug(
                    "Walmart search page summary query=%r page=%s status=%s final_url=%r redirects=%r title=%r has_ip=%s has_redux=%s has_captcha=%s body_preview=%r",
                    query,
                    page,
                    getattr(response, "status_code", None),
                    getattr(response, "url", None),
                    redirect_chain,
                    page_title,
                    "/ip/" in page_text,
                    "__WML_REDUX_INITIAL_STATE__" in page_text,
                    any(marker in page_text.lower() or marker in str(soup).lower() for marker in ("captcha", "bot", "human", "challenge", "access denied")),
                    page_text[:500],
                )
                self.logger.info(
                    "Walmart request diagnostics query=%r normalized_query=%r search_scope=%s data_source=%r platform_filter=%r request_url=%r status=%s final_url=%r page_type=%s preview=%r headers=%s",
                    self.last_search_diagnostics["original_query"],
                    self.last_search_diagnostics["normalized_query"],
                    self.last_search_diagnostics["search_scope"],
                    self.last_search_diagnostics["data_source"],
                    self.last_search_diagnostics["platform_filter"],
                    self.last_search_diagnostics["request_url"],
                    self.last_search_diagnostics["response_status_code"],
                    self.last_search_diagnostics["response_final_url"],
                    self.last_search_diagnostics["page_type"],
                    self.last_search_diagnostics["response_text_preview"],
                    {k: request_headers.get(k) for k in ["User-Agent", "Accept", "Accept-Language", "Accept-Encoding", "Referer", "Sec-CH-UA", "Sec-CH-UA-Mobile", "Sec-CH-UA-Platform"] if request_headers.get(k) is not None},
                )

                if self._is_block_page(soup):
                    self.logger.warning("Walmart returned a bot check page")
                    if self._browser_fallback_enabled():
                        products.extend(self._search_products_with_browser(query, max_results - len(products)))
                        break
                    raise CollectorError("Walmart bot check page detected")

                json_candidates = list(self._extract_json_product_candidates(soup))
                self.last_search_diagnostics["raw_record_count"] = len(json_candidates)
                self.logger.debug(
                    "Walmart page %s yielded %s JSON candidates for query %r",
                    page,
                    len(json_candidates),
                    query,
                )

                for item in json_candidates:
                    if len(products) >= max_results:
                        break
                    try:
                        product = self._extract_product_from_json(item)
                        if product and self.validate_product_data(product):
                            products.append(product)
                    except Exception as e:
                        self.logger.warning(f"Error extracting product: {e}")
                        self.last_search_diagnostics["rejection_reasons"].append(f"json_extract_error: {e}")
                        continue

                # Fallback to HTML parsing if JSON extraction fails
                if not products:
                    html_products = self._extract_from_html(soup, max_results - len(products))
                    products.extend(html_products)
                    self.last_search_diagnostics["rejection_reasons"].append(f"html_fallback_count={len(html_products)}")

                # Final fallback: extract product tiles from Walmart IP links.
                if not products:
                    ip_products = self._extract_from_ip_links(soup, max_results - len(products))
                    products.extend(ip_products)
                    self.last_search_diagnostics["rejection_reasons"].append(f"ip_link_fallback_count={len(ip_products)}")
                self.last_search_diagnostics["normalized_record_count"] = len(products)
                
                if len(products) == 0:
                    self.logger.info("No more products found")
                    break
                
                page += 1
                
            except CollectorError:
                raise
            except Exception as e:
                self.logger.error(f"Error searching Walmart: {e}")
                self.last_search_diagnostics["rejection_reasons"].append(f"collector_error: {e}")
                break
        
        self.last_search_diagnostics["final_matched_count"] = len(products)
        self.logger.info(f"Found {len(products)} products for query: {query}")
        return products

    def _browser_fallback_enabled(self) -> bool:
        """Return True when the experimental Selenium fallback is enabled."""
        return os.getenv("WALMART_BROWSER_FALLBACK", "false").lower() in {"1", "true", "yes", "on"}

    def _search_products_with_browser(self, query: str, max_results: int) -> List[ProductData]:
        """Experimental browser-backed Walmart search used only after bot-check detection."""
        self.last_search_diagnostics["browser_fallback_attempted"] = True
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.support.ui import WebDriverWait
        except Exception as exc:
            self.last_search_diagnostics["rejection_reasons"].append(f"browser_fallback_import_error: {exc}")
            raise CollectorError(f"Walmart bot check page detected; browser fallback unavailable: {exc}")

        driver = None
        try:
            options = Options()
            if os.getenv("WALMART_BROWSER_HEADLESS", "true").lower() in {"1", "true", "yes", "on"}:
                options.add_argument("--headless=new")
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--no-sandbox")
            options.add_argument("--window-size=1365,900")
            options.add_argument(f"--user-agent={self.session.headers.get('User-Agent')}")
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option("useAutomationExtension", False)

            driver = webdriver.Chrome(options=options)
            timeout = int(os.getenv("WALMART_BROWSER_TIMEOUT", "25"))
            request_url = f"{self.search_url}?{urlencode({'query': query, 'page': 1})}"
            driver.get(request_url)
            WebDriverWait(driver, timeout).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            time.sleep(float(os.getenv("WALMART_BROWSER_SETTLE_SECONDS", "2.5")))

            page_text = driver.page_source or ""
            soup = BeautifulSoup(page_text, "html.parser")
            self.last_search_diagnostics.update({
                "browser_request_url": request_url,
                "browser_response_final_url": driver.current_url,
                "browser_title": driver.title,
                "browser_response_text_preview": page_text[:300],
            })

            if self._is_block_page(soup):
                self.last_search_diagnostics.update({
                    "page_type": "bot_check",
                    "bot_check_detected": True,
                    "browser_page_type": "bot_check",
                })
                raise CollectorError("Walmart bot check page detected in browser fallback")

            products = self._extract_products_from_soup(soup, max_results)
            self.last_search_diagnostics.update({
                "page_type": "product_results" if products else "no_results",
                "browser_page_type": "product_results" if products else "no_results",
                "normalized_record_count": len(products),
                "final_matched_count": len(products),
            })
            return products
        except CollectorError:
            raise
        except Exception as exc:
            self.last_search_diagnostics["rejection_reasons"].append(f"browser_fallback_error: {exc}")
            raise CollectorError(f"Walmart bot check page detected; browser fallback failed: {exc}")
        finally:
            if driver is not None:
                driver.quit()

    def _extract_products_from_soup(self, soup: BeautifulSoup, max_results: int) -> List[ProductData]:
        """Extract products from an already-loaded Walmart search page."""
        products = []
        json_candidates = list(self._extract_json_product_candidates(soup))
        self.last_search_diagnostics["raw_record_count"] = len(json_candidates)
        for item in json_candidates:
            if len(products) >= max_results:
                break
            product = self._extract_product_from_json(item)
            if product and self.validate_product_data(product):
                products.append(product)
        if not products:
            products.extend(self._extract_from_html(soup, max_results))
            self.last_search_diagnostics["rejection_reasons"].append(f"html_fallback_count={len(products)}")
        if not products:
            products.extend(self._extract_from_ip_links(soup, max_results))
            self.last_search_diagnostics["rejection_reasons"].append(f"ip_link_fallback_count={len(products)}")
        return products
    
    def _extract_product_from_json(self, item: dict) -> Optional[ProductData]:
        """Extract product data from JSON structure."""
        try:
            # Basic product info
            product_id = str(
                item.get('usItemId')
                or item.get('itemId')
                or item.get('productId')
                or item.get('id')
                or ''
            )
            name = item.get('name') or item.get('title') or ''
            
            if not product_id or not name:
                return None
            
            # Price
            price = self._extract_price_from_json(item)
            
            # URL
            relative_url = (
                item.get('canonicalUrl')
                or item.get('productPageUrl')
                or item.get('productUrl')
                or item.get('url')
                or f"/ip/{product_id}"
            )
            url = urljoin(self.base_url, relative_url)
            
            # Image
            image_url = None
            image = item.get('image') or item.get('imageInfo', {}).get('thumbnailUrl')
            if isinstance(image, dict):
                image_url = image.get('src') or image.get('url')
            elif isinstance(image, str):
                image_url = image
            
            # Rating
            rating = None
            if item.get('averageRating') is not None:
                rating = float(item['averageRating'])
            
            # Review count
            review_count = None
            if item.get('numberOfReviews') is not None:
                review_count = int(item['numberOfReviews'])
            
            # Brand
            brand = item.get('brand', None)
            
            # Availability
            availability = "Available"
            if str(item.get('availabilityStatus', '')).upper() == 'OUT_OF_STOCK':
                availability = "Out of Stock"
            
            return ProductData(
                platform=self.platform_name,
                product_id=product_id,
                name=name,
                price=price,
                currency="USD",
                availability=availability,
                url=url,
                image_url=image_url,
                rating=rating,
                review_count=review_count,
                brand=brand
            )
            
        except Exception as e:
            self.logger.error(f"Error extracting Walmart product from JSON: {e}")
            return None
    
    def _extract_from_html(self, soup: BeautifulSoup, max_results: int) -> List[ProductData]:
        """Fallback HTML extraction method."""
        products = []
        
        # Find product containers using common selectors
        selectors = [
            '[data-testid="item-stack"]',
            '.search-result-gridview-item',
            '.search-result-product-tile'
        ]
        
        containers = []
        for selector in selectors:
            containers = soup.select(selector)
            if containers:
                break
        
        for container in containers[:max_results]:
            try:
                product_data = self._extract_product_from_html(container)
                if product_data and self.validate_product_data(product_data):
                    products.append(product_data)
            except Exception as e:
                self.logger.warning(f"Error extracting product from HTML: {e}")
                continue
        
        return products
    
    def _extract_product_from_html(self, container) -> Optional[ProductData]:
        """Extract product data from HTML container."""
        try:
            # Product name and URL
            link_elem = container.find('a')
            if not link_elem:
                return None
            
            url = urljoin(self.base_url, link_elem.get('href', ''))
            product_id = self.extract_product_id(url)
            
            # Name
            name_elem = container.find(['span', 'h3', 'h4'], string=True)
            if not name_elem:
                return None
            name = name_elem.get_text(strip=True)
            
            # Price
            price = 0.0
            price_elem = container.find(['span', 'div'], string=re.compile(r'\$[\d,]+\.?\d*'))
            if price_elem:
                price_text = price_elem.get_text(strip=True)
                price = self._parse_price(price_text)
            
            # Image
            image_url = None
            img_elem = container.find('img')
            if img_elem:
                image_url = img_elem.get('src') or img_elem.get('data-src')
            
            if not product_id:
                return None
            
            return ProductData(
                platform=self.platform_name,
                product_id=product_id,
                name=name,
                price=price,
                currency="USD",
                availability="Available",
                url=url,
                image_url=image_url
            )
            
        except Exception as e:
            self.logger.error(f"Error extracting Walmart product from HTML: {e}")
            return None

    def _extract_from_ip_links(self, soup: BeautifulSoup, max_results: int) -> List[ProductData]:
        """Final fallback using product links that contain /ip/ in the href."""
        products = []
        seen_ids = set()

        for link in soup.select('a[href*="/ip/"]'):
            if len(products) >= max_results:
                break
            href = link.get('href', '')
            url = urljoin(self.base_url, href)
            product_id = self.extract_product_id(url)
            if not product_id or product_id in seen_ids:
                continue
            seen_ids.add(product_id)

            name = link.get_text(" ", strip=True)
            if not name:
                parent = link.parent or link
                name = parent.get_text(" ", strip=True)
            if not name:
                continue

            parent_text = (link.parent.get_text(" ", strip=True) if link.parent else name)
            price = 0.0
            price_match = re.search(r'\$[\d,]+(?:\.\d{1,2})?', parent_text or '')
            if price_match:
                price = self._parse_price(price_match.group(0))

            products.append(ProductData(
                platform=self.platform_name,
                product_id=product_id,
                name=name,
                price=price,
                currency="USD",
                availability="Available",
                url=url,
                image_url=(link.find('img').get('src') if link.find('img') else None),
            ))

        if products:
            self.logger.debug("Walmart IP-link fallback yielded %s products", len(products))
        return products
    
    def get_product_details(self, product_url: str) -> Optional[ProductData]:
        """Get detailed information for a specific Walmart product."""
        try:
            response = self._make_walmart_request(product_url)
            soup = BeautifulSoup(response.content, 'html.parser')

            if self._is_block_page(soup):
                self.logger.warning("Walmart returned a bot check page")
                return None
            
            product_id = self.extract_product_id(product_url)
            if not product_id:
                return None
            
            structured_product = self._extract_details_from_json_ld(soup, product_id, product_url)
            if structured_product:
                return structured_product

            for item in self._extract_json_product_candidates(soup):
                product = self._extract_product_from_json(item)
                if product:
                    product.url = product_url
                    return product
            
            # Fallback to HTML parsing
            return self._extract_details_from_html(soup, product_id, product_url)
            
        except Exception as e:
            self.logger.error(f"Error getting Walmart product details: {e}")
            return None
    
    def _extract_details_from_html(
        self,
        soup: BeautifulSoup,
        product_id: str,
        url: str
    ) -> Optional[ProductData]:
        """Extract product details from HTML."""
        try:
            # Product name
            name_selectors = [
                'h1[data-automation-id="product-title"]',
                'h1.prod-ProductTitle',
                'h1',
            ]
            name = None
            for selector in name_selectors:
                elem = soup.select_one(selector)
                if elem:
                    name = elem.get_text(strip=True)
                    break
            
            if not name:
                return None
            
            # Price
            price = 0.0
            price_selectors = [
                'span[itemprop="price"]',
                '[data-automation-id="product-price"] span',
                '.price-current span',
                '.price span',
            ]
            
            for selector in price_selectors:
                elem = soup.select_one(selector)
                if elem:
                    price_text = elem.get_text(strip=True)
                    price = self._parse_price(price_text)
                    if price > 0:
                        break
            
            return ProductData(
                platform=self.platform_name,
                product_id=product_id,
                name=name,
                price=price,
                currency="USD",
                availability="Available",
                url=url
            )
            
        except Exception as e:
            self.logger.error(f"Error extracting Walmart details from HTML: {e}")
            return None
    
    def extract_product_id(self, url: str) -> Optional[str]:
        """Extract product ID from Walmart URL."""
        parsed_url = urlparse(url)
        query_params = parse_qs(parsed_url.query)
        for key in ('product_id', 'itemId', 'selectedSellerId'):
            if query_params.get(key):
                return query_params[key][0]

        # Walmart product ID patterns
        patterns = [
            r'/ip/[^/]+/(\d+)',
            r'/ip/(\d+)',
            r'/product/(\d+)',
            r'product_id=(\d+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        
        return None
    
    def _parse_price(self, price_text: str) -> float:
        """Parse price string to float."""
        if not price_text:
            return 0.0
        
        price_match = re.search(r'\$?\s*([\d,]+(?:\.\d{1,2})?)', str(price_text))
        if not price_match:
            return 0.0

        price_clean = price_match.group(1).replace(',', '')
        
        try:
            return float(price_clean)
        except (ValueError, TypeError):
            return 0.0

    def _make_walmart_request(self, url: str, **kwargs):
        """Make a Walmart request with optional configured proxy support."""
        proxy = self._get_configured_proxy()
        if proxy and 'proxies' not in kwargs:
            kwargs['proxies'] = {'http': proxy, 'https': proxy}
        return self._make_request(url, **kwargs)

    def _get_configured_proxy(self) -> Optional[str]:
        """Return the first configured proxy URL when proxy usage is enabled."""
        if not getattr(self.config.scraping, 'use_proxy', False):
            return None
        proxy_list = getattr(self.config.scraping, 'proxy_list', []) or []
        return proxy_list[0] if proxy_list else None

    def _is_block_page(self, soup: BeautifulSoup) -> bool:
        """Detect Walmart bot-check/CAPTCHA pages."""
        page_text = soup.get_text(" ", strip=True).lower()
        page_html = str(soup).lower()
        block_markers = (
            "robot or human",
            "confirm that you're human",
            "confirm that you’re human",
            "captcha",
            "access denied",
            "verify you are human",
            "unusual traffic",
            "security challenge",
            "bot detection",
            "something went wrong",
        )
        challenge_markers = (
            "cf-chl",
            "challenge-form",
            "hcaptcha",
            "recaptcha",
            "akamai",
            "perimeterx",
            "px-captcha",
        )
        return any(marker in page_text for marker in block_markers) or any(marker in page_html for marker in challenge_markers)

    def _extract_json_product_candidates(self, soup: BeautifulSoup) -> Iterable[Dict[str, Any]]:
        """Yield product-shaped dictionaries from Walmart's embedded JSON."""
        seen_ids = set()

        for script in soup.find_all('script'):
            script_text = script.string or script.get_text()
            if not script_text:
                continue

            for payload in self._load_json_payloads(script_text):
                for candidate in self._walk_json_dicts(payload):
                    if not self._looks_like_product(candidate):
                        continue
                    fingerprint = (
                        candidate.get('usItemId')
                        or candidate.get('itemId')
                        or candidate.get('productId')
                        or candidate.get('id')
                        or candidate.get('canonicalUrl')
                    )
                    if fingerprint in seen_ids:
                        continue
                    seen_ids.add(fingerprint)
                    yield candidate

    def _load_json_payloads(self, script_text: str) -> Iterable[Any]:
        """Parse JSON embedded in script tags."""
        candidates = []
        stripped = script_text.strip()

        if stripped.startswith('{') or stripped.startswith('['):
            candidates.append(stripped)
        elif 'window.__WML_REDUX_INITIAL_STATE__' in stripped:
            json_start = stripped.find('{')
            json_end = stripped.rfind('}') + 1
            candidates.append(stripped[json_start:json_end])

        for candidate in candidates:
            try:
                yield json.loads(candidate)
            except (TypeError, json.JSONDecodeError):
                continue

    def _walk_json_dicts(self, value: Any) -> Iterable[Dict[str, Any]]:
        """Recursively walk dictionaries inside a JSON payload."""
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from self._walk_json_dicts(child)
        elif isinstance(value, list):
            for child in value:
                yield from self._walk_json_dicts(child)

    def _looks_like_product(self, value: Dict[str, Any]) -> bool:
        """Return True when a JSON dictionary has enough product fields."""
        has_id = any(value.get(key) for key in ('usItemId', 'itemId', 'productId', 'id'))
        has_name = bool(value.get('name') or value.get('title'))
        has_price = self._extract_price_from_json(value) > 0
        has_url = bool(
            value.get('canonicalUrl')
            or value.get('productPageUrl')
            or value.get('productUrl')
        )
        has_brand = bool(value.get('brand'))
        has_image = bool(value.get('image') or value.get('imageInfo'))
        has_category = bool(value.get('category'))
        return has_id and has_name and (has_price or has_url or has_brand or has_image or has_category)

    def _extract_price_from_json(self, item: Dict[str, Any]) -> float:
        """Extract a numeric price from common Walmart JSON shapes."""
        price_info = item.get('priceInfo', {})
        current_price = price_info.get('currentPrice', {})

        price_candidates = [
            current_price.get('price') if isinstance(current_price, dict) else current_price,
            price_info.get('linePrice'),
            price_info.get('priceDisplay'),
            item.get('price'),
            item.get('currentPrice'),
        ]

        for price_candidate in price_candidates:
            if isinstance(price_candidate, dict):
                price_candidate = price_candidate.get('price')
            if isinstance(price_candidate, (int, float)):
                return float(price_candidate)
            parsed_price = self._parse_price(str(price_candidate or ''))
            if parsed_price > 0:
                return parsed_price

        return 0.0

    def _extract_details_from_json_ld(
        self,
        soup: BeautifulSoup,
        product_id: str,
        product_url: str
    ) -> Optional[ProductData]:
        """Extract product details from JSON-LD structured data."""
        for script in soup.find_all('script', type='application/ld+json'):
            script_text = script.string or script.get_text()
            if not script_text:
                continue

            try:
                data = json.loads(script_text)
            except json.JSONDecodeError:
                continue

            for item in self._walk_json_dicts(data):
                item_type = item.get('@type')
                if isinstance(item_type, list):
                    is_product = 'Product' in item_type
                else:
                    is_product = item_type == 'Product'
                if not is_product:
                    continue

                name = item.get('name', '')
                if not name:
                    continue

                offers = item.get('offers', {})
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                price = self._parse_price(str(offers.get('price', '')))

                image = item.get('image')
                image_url = image[0] if isinstance(image, list) and image else image

                brand = None
                brand_data = item.get('brand')
                if isinstance(brand_data, dict):
                    brand = brand_data.get('name')
                elif brand_data:
                    brand = str(brand_data)

                rating = None
                rating_data = item.get('aggregateRating')
                if isinstance(rating_data, dict) and rating_data.get('ratingValue'):
                    rating = float(rating_data['ratingValue'])

                review_count = None
                if isinstance(rating_data, dict) and rating_data.get('reviewCount'):
                    review_count = int(str(rating_data['reviewCount']).replace(',', ''))

                availability = "Available"
                if 'OutOfStock' in str(offers.get('availability', '')):
                    availability = "Out of Stock"

                return ProductData(
                    platform=self.platform_name,
                    product_id=product_id,
                    name=name,
                    price=price,
                    currency="USD",
                    availability=availability,
                    url=product_url,
                    image_url=image_url,
                    rating=rating,
                    review_count=review_count,
                    brand=brand
                )

        return None
