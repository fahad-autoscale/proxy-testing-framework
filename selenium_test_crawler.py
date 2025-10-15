import undetected_chromedriver as uc
import time
import random
import re
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
from urllib.parse import urljoin, urlparse
import socket
from typing import Dict, List, Any, Optional, Tuple

from proxy_test_framework import SeleniumTestFramework, CrawlMetrics

class SeleniumTestCrawler(SeleniumTestFramework):
    """Selenium-based crawler with metrics and proxy rotation"""
    
    def __init__(self, domains: List[str], proxies: List[str], max_listings: int = 30, headless: bool = False):
        super().__init__(domains, proxies, max_listings)
        self.headless = headless
        self.temp_dirs = []  # Track temporary directories for cleanup
        
        # Captcha detection patterns
        self.captcha_patterns = {
            'datadome': {
                'keywords': ['datadome', 'geo.captcha-delivery.com', 'datadome-captcha'],
                'patterns': [r'datadome[^>]*blocked', r'geo\.captcha-delivery\.com', r'datadome-captcha'],
                'confidence_threshold': 0.7
            },
            'cloudflare': {
                'keywords': ['cloudflare', 'cf-chl-bypass', 'turnstile', 'challenge'],
                'patterns': [r'cloudflare[^>]*challenge', r'cf-chl-bypass', r'turnstile', r'checking.*browser'],
                'confidence_threshold': 0.8
            },
            'recaptcha': {
                'keywords': ['recaptcha', 'google.com/recaptcha', 'g-recaptcha'],
                'patterns': [r'google\.com/recaptcha', r'g-recaptcha', r'recaptcha[^>]*challenge'],
                'confidence_threshold': 0.9
            },
            'hcaptcha': {
                'keywords': ['hcaptcha', 'hcaptcha.com', 'h-captcha'],
                'patterns': [r'hcaptcha\.com', r'h-captcha', r'hcaptcha[^>]*challenge'],
                'confidence_threshold': 0.9
            },
            'generic_block': {
                'keywords': ['access denied', 'blocked', 'forbidden', 'rate limit', 'cmsg', 'animation', 'opacity'],
                'patterns': [r'access.*denied', r'blocked.*request', r'forbidden', r'rate.*limit', r'#cmsg', r'animation.*opacity'],
                'confidence_threshold': 0.3
            }
        }
        
        # Common selectors for car listings
        self.listing_selectors = [
            ".vehicle-card", ".inventory-item", ".car-listing", ".vehicle-item",
            ".inventory-card", ".vehicle-listing", ".car-item", ".vehicle",
            ".inventory-vehicle", ".listing-item", "[data-vehicle-id]",
            "[class*='vehicle']", "[class*='inventory']", "[class*='listing']",
            "[class*='car']", "tr[data-vehicle]", "tr.vehicle-row",
            ".grid-item", ".col-vehicle"
        ]
        
        # Inventory navigation keywords
        self.inventory_keywords = [
            "inventory", "vehicles", "new vehicles", "used vehicles", 
            "cars", "trucks", "search inventory", "view inventory",
            "new cars", "used cars", "pre-owned", "certified"
        ]
    
    def detect_captcha(self, driver) -> Tuple[bool, str, float]:
        """Detect captcha/blocking with confidence scoring"""
        try:
            html = driver.page_source
            page_title = driver.title
            url = driver.current_url
            
            if not html:
                return False, "none", 0.0
            
            text = html.lower()
            title_lower = page_title.lower() if page_title else ""
            url_lower = url.lower() if url else ""
            
            # Check for very short pages (likely captcha/block pages)
            if len(html) < 5000:
                captcha_indicators = [
                    'cmsg', 'animation', 'opacity', 'keyframes', 'cfasync',
                    'datadome', 'cloudflare', 'recaptcha', 'hcaptcha',
                    'verify', 'human', 'robot', 'blocked', 'access denied'
                ]
                
                captcha_found = any(indicator in text for indicator in captcha_indicators)
                
                if captcha_found:
                    return True, "generic_block", 0.9
                elif len(html) < 2000:
                    return True, "generic_block", 0.7
            
            # Score each captcha type
            scores = {}
            
            for captcha_type, config in self.captcha_patterns.items():
                score = 0.0
                total_checks = 0
                
                # Check keywords
                for keyword in config['keywords']:
                    total_checks += 1
                    if keyword in text:
                        score += 0.3
                    if keyword in title_lower:
                        score += 0.2
                    if keyword in url_lower:
                        score += 0.1
                
                # Check regex patterns
                for pattern in config['patterns']:
                    total_checks += 1
                    if re.search(pattern, text, re.IGNORECASE):
                        score += 0.4
                    if re.search(pattern, title_lower, re.IGNORECASE):
                        score += 0.2
                
                # Normalize score
                if total_checks > 0:
                    scores[captcha_type] = min(score / total_checks, 1.0)
                else:
                    scores[captcha_type] = 0.0
            
            # Find the highest scoring captcha type
            if scores:
                best_type = max(scores, key=scores.get)
                best_score = scores[best_type]
                threshold = self.captcha_patterns[best_type]['confidence_threshold']
                
                if best_score >= threshold:
                    return True, best_type, best_score
            
            return False, "none", 0.0
            
        except Exception as e:
            print(f"[!] Error detecting captcha: {e}")
            return False, "none", 0.0
    
    def _run_single_test(self, domain: str, initial_proxy: str):
        """Run single domain test with Selenium"""
        driver = None
        metrics = self.create_metrics(domain, initial_proxy, "selenium")
        current_proxy = initial_proxy
        
        try:
            print(f"\n[+] Starting Selenium test for {domain} with proxy {current_proxy}")
            print(f"[+] Step 1: Setting up Chrome driver...")
            
            # Setup driver
            driver = self._setup_driver(current_proxy)
            print(f"[+] Step 2: Chrome driver setup complete")
            metrics.detailed_timings['driver_setup'] = time.time() - metrics.start_time
            
            # Navigate to domain
            print(f"[+] Step 3: Navigating to {domain}...")
            nav_start = time.time()
            driver.get(domain)
            print(f"[+] Step 4: Navigation complete, waiting for page load...")
            self._random_delay(2, 4)
            metrics.detailed_timings['initial_navigation'] = time.time() - nav_start
            
            # Check for captcha on homepage
            print(f"[+] Step 5: Checking for captcha...")
            html = driver.page_source
            page_title = driver.title
            print(f"[+] Page title: {page_title}")
            print(f"[+] HTML length: {len(html)} characters")
            is_blocked, captcha_type, confidence = self.detect_captcha(driver)
            
            if is_blocked:
                print(f"[!] Captcha detected on homepage: {captcha_type} (confidence: {confidence:.2f})")
                metrics.captcha_blocked = True
                metrics.captcha_type = captcha_type
                metrics.blocked_at_listing = 0
                return
            
            # Try to navigate to inventory
            inventory_found = self._find_and_click_inventory_link(driver)
            if inventory_found:
                self._random_delay(2, 4)
                metrics.pages_crawled += 1
            
            # Start crawling listings
            crawl_start = time.time()
            listings_crawled = 0
            
            while listings_crawled < self.max_listings:
                try:
                    # Find listings on current page
                    listings = self._find_vehicle_listings(driver, domain)
                    
                    if not listings:
                        print(f"[!] No more listings found on {domain}")
                        break
                    
                    # Process each listing
                    for listing_element in listings:
                        if listings_crawled >= self.max_listings:
                            break
                        
                        try:
                            # Extract listing data
                            listing_data = self._extract_vehicle_data(listing_element, domain)
                            
                            if listing_data and listing_data.get('raw_text'):
                                listings_crawled += 1
                                metrics.listings_extracted += 1
                                print(f"[+] Extracted listing {listings_crawled}: {listing_data['raw_text'][:100]}...")
                                
                                # Check for captcha after each listing
                                current_html = driver.page_source
                                current_title = driver.title
                                is_blocked, captcha_type, confidence = self.detect_captcha(driver)
                                
                                if is_blocked:
                                    print(f"[!] Captcha detected after listing {listings_crawled}: {captcha_type}")
                                    metrics.captcha_blocked = True
                                    metrics.captcha_type = captcha_type
                                    metrics.blocked_at_listing = listings_crawled
                                    
                                    # Try proxy rotation
                                    if current_proxy in metrics.proxies_used:
                                        metrics.proxies_used.append(current_proxy)
                                    
                                    new_proxy = self.proxy_manager.rotate_proxy(current_proxy)
                                    if new_proxy:
                                        print(f"[+] Rotating to proxy: {new_proxy}")
                                        metrics.proxy_rotations += 1
                                        current_proxy = new_proxy
                                        
                                        # Restart with new proxy
                                        driver.quit()
                                        driver = self._setup_driver(current_proxy)
                                        driver.get(domain)
                                        self._random_delay(2, 4)
                                        
                                        # Try to continue from where we left off
                                        if self._find_and_click_inventory_link(driver):
                                            self._random_delay(2, 4)
                                            metrics.pages_crawled += 1
                                        
                                        # Reset captcha flag and continue
                                        metrics.captcha_blocked = False
                                        metrics.captcha_type = "none"
                                    else:
                                        print(f"[!] No more proxies available, stopping crawl")
                                        break
                                
                                # Small delay between listings
                                self._random_delay(0.5, 1.5)
                        
                        except Exception as e:
                            print(f"[!] Error processing listing: {e}")
                            metrics.errors.append(f"Listing processing error: {str(e)}")
                            continue
                    
                    # Try to navigate to next page if available
                    if not self._navigate_to_next_page(driver):
                        print(f"[!] No more pages available on {domain}")
                        break
                    
                    metrics.pages_crawled += 1
                    self._random_delay(1, 3)
                    
                except Exception as e:
                    print(f"[!] Error during listing crawl: {e}")
                    metrics.errors.append(f"Crawl error: {str(e)}")
                    break
            
            metrics.detailed_timings['total_crawl_time'] = time.time() - crawl_start
            print(f"[+] Completed crawling {domain}: {metrics.listings_extracted} listings in {metrics.detailed_timings['total_crawl_time']:.2f}s")
            
        except Exception as e:
            print(f"[!] Fatal error in Selenium test for {domain}: {e}")
            metrics.errors.append(f"Fatal error: {str(e)}")
        
        finally:
            if driver:
                try:
                    driver.quit()
                except:
                    pass
            
            # Clean up temporary directories
            self._cleanup_temp_dirs()
            
            # Finalize metrics
            self.finalize_metrics(metrics)
    
    def _setup_driver(self, proxy: str):
        """Setup undetected Chrome driver with proxy"""
        import tempfile
        import os
        
        try:
            print(f"[+] Creating Chrome options...")
            options = uc.ChromeOptions()
            
            if self.headless:
                options.add_argument('--headless')
                print(f"[+] Running in headless mode")
            
            # Add proxy
            options.add_argument(f'--proxy-server={proxy}')
            print(f"[+] Using proxy: {proxy}")
            
            # Additional options to avoid detection
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-blink-features=AutomationControlled')
            options.add_argument('--disable-extensions')
            options.add_argument('--disable-plugins')
            options.add_argument('--disable-gpu')
            options.add_argument('--disable-web-security')
            options.add_argument('--allow-running-insecure-content')
            print(f"[+] Chrome options configured")
            
            # Create unique user data directory for each instance
            user_data_dir = tempfile.mkdtemp(prefix='chrome_selenium_')
            self.temp_dirs.append(user_data_dir)  # Track for cleanup
            options.add_argument(f'--user-data-dir={user_data_dir}')
            print(f"[+] User data directory: {user_data_dir}")
            
            # Random user agent
            user_agents = [
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            ]
            options.add_argument(f'--user-agent={random.choice(user_agents)}')
            
            # Use Chrome version 139 to match installed Chrome
            print(f"[+] Starting Chrome with version 139...")
            driver = uc.Chrome(options=options, version_main=139)
            print(f"[+] Chrome started successfully!")
            
            # Execute script to remove webdriver property
            print(f"[+] Removing webdriver property...")
            driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            print(f"[+] Webdriver property removed")
            
            return driver
            
        except Exception as e:
            print(f"[!] Failed to setup driver: {e}")
            raise
    
    def _random_delay(self, min_seconds: float = 1, max_seconds: float = 3):
        """Add random delay to avoid detection"""
        delay = random.uniform(min_seconds, max_seconds)
        time.sleep(delay)
    
    def _find_and_click_inventory_link(self, driver) -> bool:
        """Find and click on inventory/vehicles navigation links"""
        for keyword in self.inventory_keywords:
            try:
                # Try multiple approaches to find inventory links
                selectors = [
                    f"//a[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{keyword}')]",
                    f"//a[contains(@href, '{keyword}')]",
                    f"//*[contains(text(), '{keyword}')]//a",
                    f"//a[contains(@class, '{keyword}')]"
                ]
                
                for selector in selectors:
                    try:
                        elements = driver.find_elements(By.XPATH, selector)
                        if elements:
                            print(f"[+] Found inventory link with keyword: {keyword}")
                            # Scroll to element and click
                            driver.execute_script("arguments[0].scrollIntoView(true);", elements[0])
                            self._random_delay(0.5, 1)
                            elements[0].click()
                            self._random_delay(2, 4)
                            return True
                    except:
                        continue
                        
            except Exception as e:
                continue
                
        return False
    
    def _find_vehicle_listings(self, driver, site_name: str) -> List[Any]:
        """Find vehicle listings using multiple strategies"""
        listings = []
        
        # Try specific selectors
        for selector in self.listing_selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                if elements:
                    print(f"[+] Found {len(elements)} listings with selector: {selector}")
                    return elements[:10]  # Limit to first 10 for performance
            except:
                continue
        
        # Fallback to content-based search
        if not listings:
            try:
                xpath_patterns = [
                    "//*[contains(text(), '$') and contains(text(), 'miles')]",
                    "//*[contains(text(), '2024') or contains(text(), '2023') or contains(text(), '2022')]",
                    "//*[contains(text(), 'Ford') or contains(text(), 'Chevrolet') or contains(text(), 'Mazda')]"
                ]
                
                for pattern in xpath_patterns:
                    try:
                        elements = driver.find_elements(By.XPATH, pattern)
                        if elements:
                            return elements[:10]
                    except:
                        continue
            except:
                pass
        
        return listings
    
    def _extract_vehicle_data(self, element, site_name: str) -> Optional[Dict[str, Any]]:
        """Extract vehicle information from a listing element"""
        try:
            element_text = element.text.strip() if element.text else ''
            
            if not element_text or len(element_text) < 10:
                return None
            
            vehicle_data = {
                'site': site_name,
                'timestamp': time.time(),
                'raw_text': element_text,
                'extracted_data': {}
            }
            
            text = element_text.lower()
            
            # Extract year
            year_match = re.search(r'\b(19|20)\d{2}\b', text)
            if year_match:
                vehicle_data['extracted_data']['year'] = year_match.group()
            
            # Extract price
            price_match = re.search(r'\$(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)', text)
            if price_match:
                vehicle_data['extracted_data']['price'] = price_match.group(1)
            
            # Extract mileage
            mileage_match = re.search(r'(\d{1,3}(?:,\d{3})*)\s*miles?', text)
            if mileage_match:
                vehicle_data['extracted_data']['mileage'] = mileage_match.group(1)
            
            # Extract make/model (basic approach)
            make_model_match = re.search(r'([a-z]+)\s+([a-z]+)', text)
            if make_model_match:
                vehicle_data['extracted_data']['make'] = make_model_match.group(1)
                vehicle_data['extracted_data']['model'] = make_model_match.group(2)
            
            return vehicle_data
            
        except Exception as e:
            print(f"[!] Error extracting vehicle data: {e}")
            return None
    
    def _navigate_to_next_page(self, driver) -> bool:
        """Try to navigate to next page of listings"""
        try:
            # Common next page selectors
            next_selectors = [
                "//a[contains(text(), 'Next')]",
                "//a[contains(text(), '>')]",
                "//a[contains(@class, 'next')]",
                "//a[contains(@class, 'pagination-next')]",
                ".pagination-next",
                ".next-page"
            ]
            
            for selector in next_selectors:
                try:
                    if selector.startswith("//"):
                        elements = driver.find_elements(By.XPATH, selector)
                    else:
                        elements = driver.find_elements(By.CSS_SELECTOR, selector)
                    
                    if elements and elements[0].is_enabled():
                        elements[0].click()
                        self._random_delay(2, 4)
                        return True
                except:
                    continue
            
            return False
            
        except Exception as e:
            print(f"[!] Error navigating to next page: {e}")
            return False
    
    def _cleanup_temp_dirs(self):
        """Clean up temporary directories"""
        import shutil
        import os
        for temp_dir in self.temp_dirs:
            try:
                if os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir)
            except Exception as e:
                print(f"[!] Failed to cleanup temp dir {temp_dir}: {e}")
        self.temp_dirs.clear()
