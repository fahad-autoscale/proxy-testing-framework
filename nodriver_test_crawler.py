import nodriver as uc
import asyncio
import time
import random
import re
from urllib.parse import urljoin, urlparse
import socket
from typing import Dict, List, Any, Optional, Tuple

from proxy_test_framework import NodriverTestFramework, CrawlMetrics

class NodriverTestCrawler(NodriverTestFramework):
    """Nodriver-based crawler with metrics and proxy rotation"""
    
    def __init__(self, domains: List[str], proxies: List[str], max_listings: int = 30, headless: bool = False):
        super().__init__(domains, proxies, max_listings)
        self.headless = headless
        
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
    
    async def detect_captcha(self, page) -> Tuple[bool, str, float]:
        """Detect captcha/blocking with confidence scoring"""
        try:
            html = await page.get_content()
            page_title = await page.evaluate("document.title", await_promise=True, return_by_value=True)
            url = page.url
            
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
    
    async def _run_single_test(self, domain: str, initial_proxy: str):
        """Run single domain test with nodriver"""
        browser = None
        page = None
        metrics = self.create_metrics(domain, initial_proxy, "nodriver")
        current_proxy = initial_proxy
        
        try:
            print(f"\n[+] Starting nodriver test for {domain} with proxy {current_proxy}")
            
            # Setup browser
            browser = await self._setup_browser(current_proxy)
            page = await browser.get(domain)
            metrics.detailed_timings['browser_setup'] = time.time() - metrics.start_time
            
            # Wait for page to fully load
            print(f"[+] Waiting for page to fully load...")
            try:
                # Wait for page to be ready using nodriver method
                await page.sleep(10)  # Wait for initial load
                print(f"[+] Initial wait completed")
                
                # Check if page is still loading and wait if needed
                loading_state = await page.evaluate("document.readyState", await_promise=True, return_by_value=True)
                print(f"[+] Document ready state: {loading_state}")
                
                # If still loading, wait more
                if loading_state != "complete":
                    print(f"[+] Page still loading, waiting more...")
                    await page.sleep(5)
                    loading_state = await page.evaluate("document.readyState", await_promise=True, return_by_value=True)
                    print(f"[+] Final document ready state: {loading_state}")
                
            except Exception as e:
                print(f"[!] Page load error: {e}")
                metrics.errors.append(f"Page load error: {str(e)}")
            
            # Check for captcha on homepage
            try:
                html = await page.get_content()
                page_title = await page.evaluate("document.title", await_promise=True, return_by_value=True)
                print(f"[+] Page title: {page_title}")
                print(f"[+] HTML length: {len(html)} characters")
                
                is_blocked, captcha_type, confidence = await self.detect_captcha(page)
                
                if is_blocked:
                    print(f"[!] Captcha detected on homepage: {captcha_type} (confidence: {confidence:.2f})")
                    
                    # Try proxy rotation
                    if current_proxy not in metrics.proxies_used:
                        metrics.proxies_used.append(current_proxy)
                    
                    # Get available proxies for debugging
                    available_proxies = self.proxy_manager.get_available_proxies()
                    print(f"[+] Available proxies: {available_proxies}")
                    print(f"[+] Current proxy: {current_proxy}")
                    
                    new_proxy = self.proxy_manager.rotate_proxy(current_proxy, exclude_proxies=[current_proxy])
                    if new_proxy:
                        print(f"[+] Rotating to proxy: {new_proxy}")
                        metrics.proxy_rotations += 1
                        current_proxy = new_proxy
                        
                        # Restart with new proxy
                        print(f"[+] Stopping current browser...")
                        try:
                            await browser.stop()
                        except:
                            pass
                        
                        print(f"[+] Starting new browser with proxy {current_proxy}...")
                        try:
                            browser = await self._setup_browser(current_proxy)
                            print(f"[+] Getting page for {domain}...")
                            page = await browser.get(domain)
                            
                            # Wait for page to fully load with new proxy
                            print(f"[+] Waiting for page to fully load with new proxy...")
                            await page.sleep(10)  # Wait for initial load
                            loading_state = await page.evaluate("document.readyState", await_promise=True, return_by_value=True)
                            print(f"[+] New proxy page ready state: {loading_state}")
                            
                            # If still loading, wait more
                            if loading_state != "complete":
                                print(f"[+] New proxy page still loading, waiting more...")
                                await page.sleep(5)
                                loading_state = await page.evaluate("document.readyState", await_promise=True, return_by_value=True)
                                print(f"[+] Final new proxy page ready state: {loading_state}")
                            
                            # Check captcha again with new proxy
                            html = await page.get_content()
                            page_title = await page.evaluate("document.title", await_promise=True, return_by_value=True)
                            print(f"[+] New proxy page title: {page_title}")
                            print(f"[+] New proxy HTML length: {len(html)} characters")
                            is_blocked, captcha_type, confidence = await self.detect_captcha(page)
                        except Exception as e:
                            print(f"[!] Error during browser restart or captcha check: {e}")
                            # If we can't restart browser or check captcha, assume it's still blocked
                            is_blocked, captcha_type, confidence = True, "unknown", 0.5
                        
                        if is_blocked:
                            print(f"[!] Still blocked with new proxy: {captcha_type}")
                            metrics.captcha_blocked = True
                            metrics.captcha_type = captcha_type
                            metrics.blocked_at_listing = 0
                            return
                        else:
                            print(f"[+] New proxy works! No captcha detected")
                            # Continue with the rest of the crawling process
                    else:
                        print(f"[!] No more proxies available, stopping crawl")
                        metrics.captcha_blocked = True
                        metrics.captcha_type = captcha_type
                        metrics.blocked_at_listing = 0
                        return
                else:
                    print(f"[+] No captcha detected on homepage")
            except Exception as e:
                print(f"[!] Error checking for captcha: {e}")
                metrics.errors.append(f"Captcha check error: {str(e)}")
            
            # Try to navigate to inventory
            print(f"[+] Looking for inventory links on {domain}")
            await self._simulate_human_behavior(page)
            inventory_found = await self._find_and_click_inventory_link(page)
            if inventory_found:
                print(f"[+] Inventory link found and clicked")
                await self._human_like_delay()  # Human-like delay after clicking
                metrics.pages_crawled += 1
            else:
                print(f"[!] No inventory link found, proceeding with current page")
            
            # Start crawling listings
            crawl_start = time.time()
            listings_crawled = 0
            
            while listings_crawled < self.max_listings:
                try:
                    print(f"[+] Searching for listings on {domain} (found {listings_crawled} so far)")
                    # Find listings on current page
                    listings = await self._find_vehicle_listings(page, domain)
                    
                    if not listings:
                        print(f"[!] No more listings found on {domain}")
                        break
                    
                    print(f"[+] Found {len(listings)} listings on current page")
                    
                    # Get all listing URLs from the vehicle cards
                    print(f"[+] Extracting listing URLs from vehicle cards...")
                    listing_urls = await page.evaluate("""
                        () => {
                            const urls = [];
                            const vehicleCards = document.querySelectorAll('li.vehicle-card');
                            
                            console.log('Found', vehicleCards.length, 'vehicle cards');
                            
                            vehicleCards.forEach((card, index) => {
                                // Look for links that contain Inventory/Details
                                const allLinks = card.querySelectorAll('a');
                                let foundUrl = false;
                                
                                allLinks.forEach(link => {
                                    const href = link.href;
                                    if (href && href.includes('Inventory/Details')) {
                                        console.log('Found listing URL:', href);
                                        urls.push(href);
                                        foundUrl = true;
                                    }
                                });
                                
                                if (!foundUrl) {
                                    console.log('No detail link found in card', index + 1);
                                }
                            });
                            
                            console.log('Total URLs extracted:', urls.length);
                            return urls;
                        }
                    """, await_promise=True, return_by_value=True)
                    
                    if not listing_urls or len(listing_urls) == 0:
                        print(f"[!] No listing URLs found on page")
                        break
                    
                    print(f"[+] Found {len(listing_urls)} listing URLs to process")
                    
                    # Process each listing URL
                    for i, listing_url in enumerate(listing_urls):
                        if listings_crawled >= self.max_listings:
                            break
                        
                        try:
                            print(f"[+] Processing listing {i+1}/{len(listing_urls)}: {listing_url}")
                            
                            # Simulate human behavior before processing each listing
                            await self._simulate_human_behavior(page)
                            
                            # Navigate to the listing detail page
                            detail_page = await browser.get(listing_url)
                            await self._human_like_delay()
                            
                            # Extract data from the detail page
                            listing_data = await self._extract_vehicle_data_from_detail_page(detail_page, domain)
                            
                            if listing_data and listing_data.get('raw_text'):
                                listings_crawled += 1
                                metrics.listings_extracted += 1
                                print(f"[+] Extracted listing {listings_crawled}: {listing_data['extracted_data'].get('title', 'Unknown')}")
                                
                                # Check for captcha after every 3 listings (not every single one)
                                if listings_crawled % 3 == 0:
                                    print(f"[+] Checking for captcha after {listings_crawled} listings...")
                                    current_html = await detail_page.get_content()
                                    current_title = await detail_page.evaluate("document.title", await_promise=True, return_by_value=True)
                                    is_blocked, captcha_type, confidence = await self.detect_captcha(detail_page)
                                    
                                    if is_blocked:
                                        print(f"[!] Captcha detected after listing {listings_crawled}: {captcha_type}")
                                        metrics.captcha_blocked = True
                                        metrics.captcha_type = captcha_type
                                        metrics.blocked_at_listing = listings_crawled
                                        
                                        # Try proxy rotation
                                        if current_proxy not in metrics.proxies_used:
                                            metrics.proxies_used.append(current_proxy)
                                        
                                        new_proxy = self.proxy_manager.rotate_proxy(current_proxy, exclude_proxies=[current_proxy])
                                        if new_proxy:
                                            print(f"[+] Rotating to proxy: {new_proxy}")
                                            metrics.proxy_rotations += 1
                                            current_proxy = new_proxy
                                            
                                            # Restart with new proxy
                                            await browser.stop()
                                            browser = await self._setup_browser(current_proxy)
                                            page = await browser.get(domain)
                                            await self._human_like_delay()
                                            
                                            # Try to continue from where we left off
                                            if await self._find_and_click_inventory_link(page):
                                                await self._human_like_delay()
                                                metrics.pages_crawled += 1
                                            
                                            # Reset captcha flag and continue
                                            metrics.captcha_blocked = False
                                            metrics.captcha_type = "none"
                                        else:
                                            print(f"[!] No more proxies available, stopping crawl")
                                            break
                                
                                # Go back to main listing page
                                print(f"[+] Going back to main listing page...")
                                page = await browser.get(f"{domain}/cars-for-sale")
                                await self._human_like_delay()
                                
                                # Human-like delay between listings
                                await self._human_like_delay()
                            else:
                                print(f"[!] Failed to extract data from listing {i+1}")
                                # Go back to main listing page
                                page = await browser.get(f"{domain}/cars-for-sale")
                                await self._human_like_delay()
                        
                        except Exception as e:
                            print(f"[!] Error processing listing {i+1}: {e}")
                            metrics.errors.append(f"Listing processing error: {str(e)}")
                            # Try to go back to main page
                            try:
                                page = await browser.get(f"{domain}/cars-for-sale")
                                await self._human_like_delay()
                            except:
                                pass
                            continue
                    
                    # Try to navigate to next page if available
                    if not await self._navigate_to_next_page(page):
                        print(f"[!] No more pages available on {domain}")
                        break
                    
                    metrics.pages_crawled += 1
                    await self._random_delay(1, 3)
                    
                except Exception as e:
                    print(f"[!] Error during listing crawl: {e}")
                    metrics.errors.append(f"Crawl error: {str(e)}")
                    break
            
            metrics.detailed_timings['total_crawl_time'] = time.time() - crawl_start
            print(f"[+] Completed crawling {domain}: {metrics.listings_extracted} listings in {metrics.detailed_timings['total_crawl_time']:.2f}s")
            
        except Exception as e:
            print(f"[!] Fatal error in nodriver test for {domain}: {e}")
            metrics.errors.append(f"Fatal error: {str(e)}")
        
        finally:
            if browser:
                try:
                    await browser.stop()
                except:
                    pass
            
            # Finalize metrics
            self.finalize_metrics(metrics)
    
    async def _setup_browser(self, proxy: str):
        """Setup nodriver browser with proxy"""
        try:
            browser_args = [
                f"--proxy-server={proxy}",
                "--disable-dev-shm-usage",
                "--start-maximized",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-gpu",
                "--disable-web-security",
                "--allow-running-insecure-content",
                "--user-agent=Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/136.0.0.0 Safari/537.36"
            ]
            
            if self.headless:
                browser_args.append("--headless")
            
            print(f"[+] Using proxy: {proxy}")
            
            # Use the same approach as the working app_windows.py but with Chrome version
            browser = await uc.start(
                headless=self.headless,
                browser_args=browser_args,
                lang="en-US",
                chrome_version=139  # Match installed Chrome version
            )
            
            return browser
            
        except Exception as e:
            print(f"[!] Failed to setup browser: {e}")
            raise
    
    async def _random_delay(self, min_seconds: float = 1, max_seconds: float = 3):
        """Add random delay to avoid detection"""
        delay = random.uniform(min_seconds, max_seconds)
        await asyncio.sleep(delay)
    
    async def _human_like_delay(self):
        """Add human-like delay with variation"""
        # Human-like delays: 2-8 seconds between actions
        delay = random.uniform(2, 8)
        print(f"[+] Human-like delay: {delay:.1f}s")
        await asyncio.sleep(delay)
    
    async def _simulate_human_behavior(self, page):
        """Simulate human-like behavior"""
        try:
            # Random mouse movement
            await page.evaluate("""
                () => {
                    const event = new MouseEvent('mousemove', {
                        clientX: Math.random() * window.innerWidth,
                        clientY: Math.random() * window.innerHeight
                    });
                    document.dispatchEvent(event);
                }
            """, await_promise=True, return_by_value=True)
            
            # Random scroll
            await page.evaluate("""
                () => {
                    window.scrollBy(0, Math.random() * 200 - 100);
                }
            """, await_promise=True, return_by_value=True)
            
            await self._random_delay(0.5, 1.5)
        except Exception as e:
            print(f"[!] Error simulating human behavior: {e}")
    
    async def _find_and_click_inventory_link(self, page) -> bool:
        """Find and click on inventory/vehicles navigation links"""
        print(f"[+] AGGRESSIVE SEARCH for inventory links...")
        
        # Method 1: Direct JavaScript search for ALL links and print them
        try:
            print(f"[+] Method 1: Getting ALL links with JavaScript...")
            all_links_info = await page.evaluate("""
                () => {
                    const links = Array.from(document.querySelectorAll('a'));
                    return links.map(link => ({
                        text: link.textContent.trim(),
                        href: link.href,
                        pathname: link.pathname,
                        innerHTML: link.innerHTML.trim().substring(0, 100)
                    })).filter(link => 
                        link.text.toLowerCase().includes('inventory') || 
                        link.text.toLowerCase().includes('cars') ||
                        link.text.toLowerCase().includes('all') ||
                        link.pathname.includes('cars-for-sale') ||
                        link.href.includes('cars-for-sale')
                    );
                }
            """, await_promise=True, return_by_value=True)
            
            if all_links_info and len(all_links_info) > 0:
                print(f"[+] Found {len(all_links_info)} potential inventory links:")
                for i, link_info in enumerate(all_links_info):
                    print(f"[+]   {i+1}. TEXT: '{link_info['text']}' | HREF: {link_info['href']} | PATH: {link_info['pathname']}")
                
                # Try to click the first one
                first_link = all_links_info[0]
                print(f"[+] ATTEMPTING TO CLICK: '{first_link['text']}' -> {first_link['href']}")
                
                # Try multiple ways to click
                try:
                    # Method 1: Direct href match
                    link_element = await page.select(f"a[href='{first_link['href']}']")
                    if link_element:
                        await link_element.click()
                        await self._random_delay(3, 5)
                        print(f"[+] SUCCESS: Clicked via href match")
                        return True
                except Exception as e:
                    print(f"[!] Failed href match: {e}")
                
                try:
                    # Method 2: Pathname match
                    link_element = await page.select(f"a[pathname='{first_link['pathname']}']")
                    if link_element:
                        await link_element.click()
                        await self._random_delay(3, 5)
                        print(f"[+] SUCCESS: Clicked via pathname match")
                        return True
                except Exception as e:
                    print(f"[!] Failed pathname match: {e}")
                
                try:
                    # Method 3: JavaScript click
                    await page.evaluate(f"""
                        () => {{
                            const link = document.querySelector('a[href="{first_link['href']}"]');
                            if (link) {{
                                link.click();
                                return true;
                            }}
                            return false;
                        }}
                    """, await_promise=True, return_by_value=True)
                    await self._random_delay(3, 5)
                    print(f"[+] SUCCESS: Clicked via JavaScript")
                    return True
                except Exception as e:
                    print(f"[!] Failed JavaScript click: {e}")
                    
            else:
                print(f"[!] No inventory links found with JavaScript search")
                
        except Exception as e:
            print(f"[!] Error with JavaScript search: {e}")
        
        # Method 2: Direct CSS selector attempts
        try:
            print(f"[+] Method 2: Trying direct CSS selectors...")
            selectors_to_try = [
                "a[href*='cars-for-sale']",
                "a[href='/cars-for-sale']",
                "a[href*='inventory']",
                "a:contains('All Inventory')",
                "a:contains('inventory')",
                "a:contains('cars')"
            ]
            
            for selector in selectors_to_try:
                try:
                    print(f"[+] Trying selector: {selector}")
                    elements = await page.select_all(selector)
                    if elements and len(elements) > 0:
                        print(f"[+] Found {len(elements)} elements with selector: {selector}")
                        await elements[0].click()
                        await self._random_delay(3, 5)
                        print(f"[+] SUCCESS: Clicked via selector {selector}")
                        return True
                except Exception as e:
                    print(f"[!] Failed selector {selector}: {e}")
                    continue
                    
        except Exception as e:
            print(f"[!] Error with CSS selectors: {e}")
        
        print(f"[!] FAILED: No inventory links found with any method")
        return False
    
    async def _find_vehicle_listings(self, page, site_name: str) -> List[Any]:
        """Find vehicle listings using multiple strategies"""
        print(f"[+] Searching for vehicle listings on {site_name}...")
        
        # Use JavaScript to find vehicle listings
        try:
            listings = await page.evaluate("""
                () => {
                    const selectors = [
                        '.vehicle-card', '.inventory-item', '.car-listing', '.vehicle-item',
                        '.inventory-card', '.vehicle-listing', '.car-item', '.vehicle',
                        '.inventory-vehicle', '.listing-item', '[data-vehicle-id]',
                        '.grid-item', '.col-vehicle', '.car-card', '.vehicle-card'
                    ];
                    
                    const found = [];
                    
                    selectors.forEach(selector => {
                        const elements = document.querySelectorAll(selector);
                        if (elements.length > 0) {
                            found.push({
                                selector: selector,
                                count: elements.length,
                                elements: Array.from(elements).slice(0, 10).map(el => ({
                                    text: el.textContent.trim().substring(0, 100),
                                    hasLink: el.querySelector('a') !== null,
                                    linkHref: el.querySelector('a')?.href || null
                                }))
                            });
                        }
                    });
                    
                    return found;
                }
            """, await_promise=True, return_by_value=True)
            
            if listings and len(listings) > 0:
                print(f"[+] Found listings with JavaScript search:")
                for listing_group in listings:
                    print(f"[+]   Selector '{listing_group['selector']}': {listing_group['count']} items")
                    for i, item in enumerate(listing_group['elements'][:3]):  # Show first 3
                        print(f"[+]     {i+1}. {item['text']} (has link: {item['hasLink']})")
                
                # Return the first group with the most items
                best_group = max(listings, key=lambda x: x['count'])
                print(f"[+] Using selector '{best_group['selector']}' with {best_group['count']} items")
                
                # Get the actual elements
                elements = await page.select_all(best_group['selector'])
                return elements  # Return all elements, not limited to 10
                
        except Exception as e:
            print(f"[!] Error with JavaScript listing search: {e}")
        
        # Fallback to original method
        for selector in self.listing_selectors:
            try:
                elements = await page.select_all(selector)
                if elements:
                    print(f"[+] Found {len(elements)} listings with selector: {selector}")
                    return elements  # Return all elements, not limited to 10
            except:
                continue
        
        print(f"[!] No vehicle listings found")
        return []
    
    async def _extract_vehicle_data_from_detail_page(self, page, site_name: str) -> Optional[Dict[str, Any]]:
        """Extract vehicle data from a detail page"""
        try:
            print(f"[+] Extracting data from detail page: {page.url}")
            
            # Wait for page to load
            await page.sleep(3)
            
            # Extract data using JavaScript
            vehicle_data = await page.evaluate("""
                () => {
                    const data = {
                        title: '',
                        price: '',
                        mileage: '',
                        year: '',
                        make: '',
                        model: '',
                        engine: '',
                        transmission: '',
                        drivetrain: '',
                        color: '',
                        vin: '',
                        raw_text: document.body.textContent.trim()
                    };
                    
                    // Extract title from page title or h1
                    const titleElement = document.querySelector('h1, .vehicle-title, .inventory-title');
                    if (titleElement) {
                        data.title = titleElement.textContent.trim();
                        
                        // Extract year, make, model from title
                        const titleMatch = data.title.match(/(\d{4})\s+([A-Za-z]+)\s+(.+)/);
                        if (titleMatch) {
                            data.year = titleMatch[1];
                            data.make = titleMatch[2];
                            data.model = titleMatch[3];
                        }
                    }
                    
                    // Extract price
                    const priceSelectors = ['.price', '.vehicle-price', '.listing-price', '[class*="price"]'];
                    for (const selector of priceSelectors) {
                        const priceElement = document.querySelector(selector);
                        if (priceElement) {
                            const priceText = priceElement.textContent.trim();
                            const priceMatch = priceText.match(/\$?(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)/);
                            if (priceMatch) {
                                data.price = priceMatch[1];
                                break;
                            }
                        }
                    }
                    
                    // Extract mileage
                    const mileageSelectors = ['.mileage', '.vehicle-mileage', '[class*="mileage"]'];
                    for (const selector of mileageSelectors) {
                        const mileageElement = document.querySelector(selector);
                        if (mileageElement) {
                            const mileageText = mileageElement.textContent.trim();
                            const mileageMatch = mileageText.match(/(\d{1,3}(?:,\d{3})*)/);
                            if (mileageMatch) {
                                data.mileage = mileageMatch[1];
                                break;
                            }
                        }
                    }
                    
                    // Extract VIN
                    const vinElement = document.querySelector('[class*="vin"], [id*="vin"]');
                    if (vinElement) {
                        data.vin = vinElement.textContent.trim();
                    }
                    
                    // Extract features from detail sections
                    const featureSections = document.querySelectorAll('.feature, .spec, .detail');
                    featureSections.forEach(section => {
                        const text = section.textContent.toLowerCase();
                        if (text.includes('engine:') || text.includes('engine')) {
                            data.engine = section.textContent.trim();
                        } else if (text.includes('transmission:') || text.includes('transmission')) {
                            data.transmission = section.textContent.trim();
                        } else if (text.includes('drivetrain:') || text.includes('drivetrain')) {
                            data.drivetrain = section.textContent.trim();
                        } else if (text.includes('color:') || text.includes('exterior color')) {
                            data.color = section.textContent.trim();
                        }
                    });
                    
                    return data;
                }
            """, await_promise=True, return_by_value=True)
            
            if not vehicle_data or not vehicle_data.get('title'):
                print(f"[DEBUG] No vehicle data extracted from detail page")
                return None
            
            # Convert to our format
            result = {
                'site': site_name,
                'timestamp': time.time(),
                'raw_text': vehicle_data.get('raw_text', ''),
                'extracted_data': {
                    'title': vehicle_data.get('title', ''),
                    'year': vehicle_data.get('year', ''),
                    'make': vehicle_data.get('make', ''),
                    'model': vehicle_data.get('model', ''),
                    'price': vehicle_data.get('price', ''),
                    'mileage': vehicle_data.get('mileage', ''),
                    'engine': vehicle_data.get('engine', ''),
                    'transmission': vehicle_data.get('transmission', ''),
                    'drivetrain': vehicle_data.get('drivetrain', ''),
                    'color': vehicle_data.get('color', ''),
                    'vin': vehicle_data.get('vin', ''),
                    'detail_url': page.url
                }
            }
            
            print(f"[+] Extracted from detail page: {result['extracted_data']['title']} - ${result['extracted_data']['price']} - {result['extracted_data']['mileage']} miles")
            return result
            
        except Exception as e:
            print(f"[!] Error extracting vehicle data from detail page: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    async def _extract_vehicle_data(self, element, site_name: str) -> Optional[Dict[str, Any]]:
        """Extract vehicle information from a listing element"""
        try:
            print(f"[DEBUG] Attempting to extract data from element...")
            
            # First, let's check if the element is valid
            if not element:
                print(f"[DEBUG] Element is None")
                return None
            
            # Get the raw text content first
            try:
                raw_text = element.text
                print(f"[DEBUG] Raw text length: {len(raw_text)} characters")
                print(f"[DEBUG] Raw text preview: {raw_text[:200]}...")
            except Exception as e:
                print(f"[DEBUG] Could not get element text: {e}")
                return None
            
            # Extract data using page-level JavaScript evaluation
            vehicle_data = await element.page.evaluate(f"""
                () => {{
                    const element = document.querySelector('.vehicle-card:nth-child({element.index + 1})');
                    if (!element) return null;
                    
                    try {{
                        const data = {{
                            title: '',
                            price: '',
                            mileage: '',
                            year: '',
                            make: '',
                            model: '',
                            engine: '',
                            transmission: '',
                            drivetrain: '',
                            color: '',
                            raw_text: element.textContent.trim()
                        }};
                        
                        // Extract title from inventory-title
                        const titleElement = element.querySelector('.inventory-title span');
                        if (titleElement) {{
                            data.title = titleElement.textContent.trim();
                            
                            // Extract year, make, model from title
                            const titleMatch = data.title.match(/(\\d{{4}})\\s+([A-Za-z]+)\\s+(.+)/);
                            if (titleMatch) {{
                                data.year = titleMatch[1];
                                data.make = titleMatch[2];
                                data.model = titleMatch[3];
                            }}
                        }}
                        
                        // Extract price
                        const priceElements = element.querySelectorAll('.price-mileage-block .value');
                        if (priceElements.length > 0) {{
                            const priceText = priceElements[0].textContent.trim();
                            const priceMatch = priceText.match(/\\$?(\\d{{1,3}}(?:,\\d{{3}})*(?:\\.\\d{{2}})?)/);
                            if (priceMatch) {{
                                data.price = priceMatch[1];
                            }}
                        }}
                        
                        // Extract mileage
                        if (priceElements.length > 1) {{
                            data.mileage = priceElements[1].textContent.trim();
                        }}
                        
                        // Extract features
                        const features = element.querySelectorAll('.features-list .feature');
                        features.forEach(feature => {{
                            const label = feature.querySelector('.feature-label')?.textContent.trim();
                            const value = feature.querySelector('.feature-value')?.textContent.trim();
                            
                            if (label && value) {{
                                if (label.includes('Engine:')) data.engine = value;
                                else if (label.includes('Transmission:')) data.transmission = value;
                                else if (label.includes('Drivetrain:')) data.drivetrain = value;
                                else if (label.includes('Ext. Color:')) data.color = value;
                            }}
                        }});
                        
                        return data;
                    }} catch (error) {{
                        console.error('Error in vehicle data extraction:', error);
                        return null;
                    }}
                }}
            """, await_promise=True, return_by_value=True)
            
            if not vehicle_data:
                print(f"[DEBUG] JavaScript evaluation returned None or empty data")
                return None
            
            if not vehicle_data.get('title'):
                print(f"[DEBUG] No title found in extracted data: {vehicle_data}")
                return None
            
            # Convert to our format
            result = {
                'site': site_name,
                'timestamp': time.time(),
                'raw_text': vehicle_data.get('raw_text', ''),
                'extracted_data': {
                    'title': vehicle_data.get('title', ''),
                    'year': vehicle_data.get('year', ''),
                    'make': vehicle_data.get('make', ''),
                    'model': vehicle_data.get('model', ''),
                    'price': vehicle_data.get('price', ''),
                    'mileage': vehicle_data.get('mileage', ''),
                    'engine': vehicle_data.get('engine', ''),
                    'transmission': vehicle_data.get('transmission', ''),
                    'drivetrain': vehicle_data.get('drivetrain', ''),
                    'color': vehicle_data.get('color', '')
                }
            }
            
            print(f"[+] Extracted: {result['extracted_data']['title']} - ${result['extracted_data']['price']} - {result['extracted_data']['mileage']} miles")
            return result
            
        except Exception as e:
            print(f"[!] Error extracting vehicle data: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    async def _navigate_to_next_page(self, page) -> bool:
        """Try to navigate to next page of listings"""
        try:
            print(f"[+] Looking for next page button...")
            
            # Try to find next page button using JavaScript
            next_page_found = await page.evaluate("""
                () => {
                    // Look for next page button in pagination
                    const nextButtons = document.querySelectorAll('a[aria-label="Go to the next page"], a[title="Go to the next page"]');
                    
                    for (let button of nextButtons) {
                        // Check if button is not disabled
                        if (!button.classList.contains('disabled') && !button.hasAttribute('aria-disabled')) {
                            console.log('Found next page button:', button.textContent.trim());
                            button.click();
                            return true;
                        }
                    }
                    
                    // Fallback: look for any pagination next button
                    const paginationNext = document.querySelector('.pagination .fa-arrow-right');
                    if (paginationNext && !paginationNext.closest('.disabled')) {
                        console.log('Found fallback next button');
                        paginationNext.click();
                        return true;
                    }
                    
                    return false;
                }
            """, await_promise=True, return_by_value=True)
            
            if next_page_found:
                print(f"[+] Successfully clicked next page button")
                await self._human_like_delay()  # Human-like delay after clicking
                return True
            else:
                print(f"[!] No next page button found or all are disabled")
                return False
            
        except Exception as e:
            print(f"[!] Error navigating to next page: {e}")
            return False
