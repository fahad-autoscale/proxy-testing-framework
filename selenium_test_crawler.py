import undetected_chromedriver as uc
import time
import random
import re
import asyncio
import json
import os
from datetime import datetime
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
from urllib.parse import urljoin, urlparse
import socket
from typing import Dict, List, Any, Optional, Tuple

from proxy_test_framework import SeleniumTestFramework, CrawlMetrics

class SeleniumTestCrawler(SeleniumTestFramework):
    """Selenium-based crawler with comprehensive vehicle data extraction and pagination"""
    
    def __init__(self, domains: List[str], proxies: List[str], max_listings: int = 100, headless: bool = False):
        super().__init__(domains, proxies, max_listings)
        self.headless = headless
        self.temp_dirs = []  # Track temporary directories for cleanup
        self.extracted_data = []  # Store extracted vehicle data
        
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
        
        # Inventory navigation keywords
        self.inventory_keywords = [
            "inventory", "vehicles", "new vehicles", "used vehicles", 
            "cars", "trucks", "search inventory", "view inventory",
            "new cars", "used cars", "pre-owned", "certified", "cars-for-sale"
        ]
    
    async def run_parallel_tests(self) -> Dict[str, Any]:
        """Run parallel tests for all domains"""
        results = {}
        
        for domain in self.domains:
            print(f"\n[+] Starting Selenium test for {domain}")
            
            # Get initial proxy
            initial_proxy = self.proxy_manager.get_next_proxy()
            
            try:
                # Extract all listing URLs first
                listing_urls = await self._extract_all_listing_urls(domain, initial_proxy)
                
                if not listing_urls:
                    print(f"[!] No listing URLs found for {domain}")
                    results[domain.replace('https://', '').replace('www.', '').replace('/', '')] = {
                        'listings_extracted': 0,
                        'captcha_blocked': False,
                        'captcha_type': 'none',
                        'errors': ['No listing URLs found']
                    }
                    continue
                
                print(f"[+] Found {len(listing_urls)} listing URLs for {domain}")
                
                # Process listings in parallel
                metrics = self.create_metrics(domain, initial_proxy, "selenium")
                successful_extractions = await self._process_listings_in_parallel(
                    listing_urls, initial_proxy, domain, metrics
                )
                
                # Save extracted data
                await self._save_extracted_data(domain, successful_extractions)
                
                results[domain.replace('https://', '').replace('www.', '').replace('/', '')] = {
                    'listings_extracted': successful_extractions,
                    'captcha_blocked': metrics.captcha_blocked,
                    'captcha_type': metrics.captcha_type,
                    'errors': metrics.errors
                }
                
            except Exception as e:
                print(f"[!] Error processing domain {domain}: {e}")
                results[domain.replace('https://', '').replace('www.', '').replace('/', '')] = {
                    'listings_extracted': 0,
                    'captcha_blocked': True,
                    'captcha_type': 'error',
                    'errors': [str(e)]
                }
        
        return results
    
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
            if len(html) < 2000:
                captcha_indicators = [
                    'cmsg', 'cfasync', 'datadome', 'cloudflare', 'recaptcha', 'hcaptcha',
                    'verify', 'human', 'robot', 'blocked', 'access denied'
                ]
                
                captcha_found = any(indicator in text for indicator in captcha_indicators)
                
                if captcha_found:
                    return True, "generic_block", 0.9
                else:
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
    
    async def _extract_all_listing_urls(self, domain: str, proxy: str, retry_count: int = 0) -> List[str]:
        """Extract all listing URLs from all pages using HTML parsing with proxy rotation"""
        driver = None
        all_urls = []
        max_retries = 3  # Maximum retry attempts
        
        try:
            print(f"[+] Step 1: Extracting listing URLs from inventory page...")
            print(f"[+] Using proxy: {proxy}")
            if retry_count > 0:
                print(f"[+] Retry attempt {retry_count}/{max_retries}")
            
            # Setup driver
            driver = self._setup_driver(proxy)
            if not driver:
                raise Exception("Failed to setup driver")
            
            # Navigate to domain
            print(f"[+] Quick page load check...")
            driver.get(domain)
            
            # ADVANCED HUMAN BEHAVIOR SIMULATION - Match nodriver effectiveness
            await self._simulate_human_behavior(driver)
            
            # Browser startup delay (same as nodriver)
            startup_delay = random.uniform(3.0, 8.0)
            print(f"[DEBUG] Browser startup delay: {startup_delay:.1f}s to avoid detection...")
            await asyncio.sleep(startup_delay)
            
            # Check for captcha on homepage
            html = driver.page_source
            page_title = driver.title
            print(f"[+] Document ready state: loading")
            print(f"[+] Final document ready state: loading")
            print(f"[+] Page title: {page_title}")
            print(f"[+] HTML length: {len(html)} characters")
            
            is_blocked, captcha_type, confidence = self.detect_captcha(driver)
            if is_blocked:
                print(f"[!] Captcha detected on homepage: {captcha_type} (confidence: {confidence:.2f})")
                
                # Clean up current driver
                try:
                    driver.quit()
                except:
                    pass
                
                # Try proxy rotation (same as nodriver)
                if retry_count < max_retries:
                    new_proxy = self.proxy_manager.rotate_proxy(proxy)
                    if new_proxy and new_proxy != proxy:
                        print(f"[+] Rotating to new proxy: {new_proxy}")
                        await asyncio.sleep(random.uniform(2.0, 5.0))  # Delay before retry
                        
                        # Retry with new proxy
                        return await self._extract_all_listing_urls(domain, new_proxy, retry_count + 1)
                    else:
                        print(f"[!] No more proxies available, trying same proxy again...")
                        await asyncio.sleep(random.uniform(5.0, 10.0))  # Longer delay
                        return await self._extract_all_listing_urls(domain, proxy, retry_count + 1)
                else:
                    print(f"[!] Maximum retry attempts ({max_retries}) reached")
                    return []
            
            print(f"[+] No captcha detected on homepage")
            
            # Find and click inventory link
            print(f"[+] Looking for inventory links on {domain}")
            await self._simulate_human_behavior(driver)
            inventory_found = self._find_and_click_inventory_link(driver)
            if inventory_found:
                print(f"[+] Inventory link found and clicked")
                await self._human_like_delay()
            else:
                print(f"[!] No inventory link found, using current page")
            
            print(f"[+] Extracting listing URLs from inventory page...")
            
            # Extract URLs from all pages
            current_page = 1
            total_pages = 1
            
            while current_page <= total_pages:
                print(f"[+] Extracting URLs from page {current_page}...")
                
                # Human-like pause before extraction
                await asyncio.sleep(random.uniform(1, 3))
                
                # Scroll to top of page
                driver.execute_script("window.scrollTo(0, 0);")
                await asyncio.sleep(0.5)
                
                # Extract URLs from current page using HTML parsing
                page_urls = self._extract_listing_urls_from_single_page(driver, domain)
                print(f"[+] Page {current_page}: Found {len(page_urls)} URLs (Total so far: {len(all_urls) + len(page_urls)})")
                
                all_urls.extend(page_urls)
                
                # Parse pagination info to determine total pages
                if current_page == 1:
                    pagination_info = self._parse_pagination_info(driver.page_source)
                    total_pages = pagination_info.get('total_pages', 1)
                    print(f"[DEBUG] Pagination info: {pagination_info}")
                
                # Check if we should continue to next page
                if current_page < total_pages:
                    current_page += 1
                    print(f"[+] Found {total_pages} total pages, navigating to page {current_page}...")
                    
                    # Construct next page URL
                    current_url = driver.current_url
                    if '?' in current_url:
                        base_url = current_url.split('?')[0]
                    else:
                        base_url = current_url
                    page_url = f"{base_url}?Paging.Page={current_page}"
                    
                    print(f"[DEBUG] Next page URL: {page_url}")
                    
                    # Navigate to next page
                    driver.get(page_url)
                    print(f"[DEBUG] Waiting {random.uniform(5, 10):.1f}s for page to load...")
                    await asyncio.sleep(random.uniform(5, 10))
                    
                    # Human-like delay between pages
                    await asyncio.sleep(random.uniform(3, 6))
                else:
                    break
            
            print(f"[+] Completed pagination: Found {len(all_urls)} total URLs across {total_pages} pages")
            print(f"[+] Successfully extracted {len(all_urls)} listing URLs")
            
            return all_urls
            
        except Exception as e:
            print(f"[!] Error extracting listing URLs: {e}")
            return []
        
        finally:
            if driver:
                try:
                    driver.quit()
                except:
                    pass
    
    def _extract_listing_urls_from_single_page(self, driver, domain: str) -> List[str]:
        """Extract listing URLs from a single page using HTML parsing"""
        try:
            html = driver.page_source
            urls = []
            
            # Extract URLs using HTML parsing (same as nodriver)
            pattern = r'href="(/Inventory/Details/[^"]+)"'
            matches = re.findall(pattern, html, re.IGNORECASE)
            
            for m in matches:
                # Convert to absolute URL
                current_url = driver.current_url
                if '://' in current_url:
                    base_domain = current_url.split('://')[1].split('/')[0]
                    abs_url = f"https://{base_domain}{m}" if m.startswith('/') else m
                else:
                    abs_url = m
                
                if abs_url not in urls:
                    urls.append(abs_url)
            
            print(f"[+] Using HTML parsing to find detail links...")
            print(f"[+] HTML parsing found {len(urls)} URLs")
            
            return urls
            
        except Exception as e:
            print(f"[!] Error extracting URLs from page: {e}")
            return []
    
    def _parse_pagination_info(self, html: str) -> Dict[str, int]:
        """Parse pagination information from HTML"""
        try:
            # Look for "Showing X - Y of Z" pattern
            pattern = r'Showing\s+(\d+)\s*-\s*(\d+)\s+of\s+(\d+)'
            match = re.search(pattern, html, re.IGNORECASE)
            
            if match:
                start = int(match.group(1))
                end = int(match.group(2))
                total = int(match.group(3))
                
                # Calculate total pages (assuming 24 items per page like nodriver)
                items_per_page = end - start + 1
                total_pages = (total + items_per_page - 1) // items_per_page
                
                return {
                    'start': start,
                    'end': end,
                    'total_records': total,
                    'total_pages': total_pages,
                    'current_page': 1
                }
            
            return {'total_pages': 1, 'current_page': 1}
            
        except Exception as e:
            print(f"[!] Error parsing pagination info: {e}")
            return {'total_pages': 1, 'current_page': 1}
    
    async def _process_listings_in_parallel(self, listing_urls: List[str], proxy: str, 
                                          domain: str, metrics) -> int:
        """Process multiple listings in parallel with fresh browser sessions"""
        # Process all listings in batches of 8
        batch_size = 8
        total_processed = 0
        total_successful = 0
        
        print(f"[+] Processing {len(listing_urls)} listings in batches of {batch_size} with proxy: {proxy}")
        
        for batch_start in range(0, len(listing_urls), batch_size):
            batch_end = min(batch_start + batch_size, len(listing_urls))
            batch_urls = listing_urls[batch_start:batch_end]
            batch_size_actual = len(batch_urls)
            
            print(f"[+] Processing batch {batch_start//batch_size + 1}: listings {batch_start+1}-{batch_end} ({batch_size_actual} listings)")
            
            tasks = []
            for i, listing_url in enumerate(batch_urls):
                listing_num = batch_start + i + 1
                task = asyncio.create_task(
                    self._process_single_listing_with_fresh_session(
                        listing_url, proxy, listing_num, domain, metrics
                    )
                )
                tasks.append(task)
                
                if i < batch_size_actual - 1:
                    await asyncio.sleep(random.uniform(0.5, 2.0))
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            batch_successful = 0
            for i, result in enumerate(results):
                listing_num = batch_start + i + 1
                if isinstance(result, Exception):
                    print(f"[!] Task {listing_num} failed with exception: {result}")
                    metrics.errors.append(f"Parallel task {listing_num} error: {str(result)}")
                elif result:
                    batch_successful += 1
                    print(f"[+] Task {listing_num} completed successfully")
                else:
                    print(f"[!] Task {listing_num} failed")
            
            total_processed += batch_size_actual
            total_successful += batch_successful
            
            print(f"[+] Batch {batch_start//batch_size + 1} completed: {batch_successful}/{batch_size_actual} successful")
            
            if batch_end < len(listing_urls):
                batch_delay = random.uniform(2.0, 5.0)
                print(f"[DEBUG] Batch delay: {batch_delay:.1f}s before next batch...")
                await asyncio.sleep(batch_delay)
        
        print(f"[+] All parallel processing completed: {total_successful}/{total_processed} successful")
        return total_successful
    
    async def _process_single_listing_with_fresh_session(self, listing_url: str, proxy: str, 
                                                       listing_num: int, domain: str, metrics) -> bool:
        """Process a single listing with a fresh browser session"""
        driver = None
        
        try:
            print(f"[DEBUG] Opening detail page attempt 1/3 with proxy: {proxy}")
            
            # Setup fresh driver
            driver = self._setup_driver(proxy)
            if not driver:
                print(f"[!] Failed to setup driver for listing {listing_num}")
                return False
            
            # Navigate to listing
            driver.get(listing_url)
            
            # Wait for page to load with human behavior
            await asyncio.sleep(random.uniform(5, 10))
            
            # Simulate human behavior on detail page
            await self._simulate_human_behavior(driver)
            
            # Additional human-like reading time
            reading_time = random.uniform(3.0, 8.0)
            print(f"[DEBUG] Human-like reading time: {reading_time:.1f}s...")
            await asyncio.sleep(reading_time)
            
            # Check for captcha
            is_blocked, captcha_type, confidence = self.detect_captcha(driver)
            if is_blocked:
                print(f"[!] Captcha detected on detail page: {captcha_type} (confidence: {confidence:.2f})")
                
                # Try proxy rotation for detail page
                new_proxy = self.proxy_manager.rotate_proxy(proxy)
                if new_proxy and new_proxy != proxy:
                    print(f"[+] Rotating to new proxy for detail page: {new_proxy}")
                    
                    # Clean up current driver
                    try:
                        driver.quit()
                    except:
                        pass
                    
                    # Retry with new proxy
                    return await self._process_single_listing_with_fresh_session(
                        listing_url, new_proxy, listing_num, domain, metrics
                    )
                else:
                    print(f"[!] No more proxies available for detail page")
                    return False
            
            # Extract vehicle data
            vehicle_data = self._extract_vehicle_data_from_detail_page(driver, listing_url)
            
            if vehicle_data and vehicle_data.get('title'):
                # Store the extracted data
                self.extracted_data.append({
                    'url': listing_url,
                    'listing_number': listing_num,
                    'extraction_timestamp': time.time(),
                    'proxy_used': proxy,
                    'vehicle_data': vehicle_data
                })
                
                print(f"[+] Extracted data for listing {listing_num}: {vehicle_data['title']}")
                print(f"[+] Stored vehicle data for listing {listing_num}: {vehicle_data['title']}")
                return True
            else:
                print(f"[!] Failed to extract meaningful data from listing {listing_num}")
                return False
                
        except Exception as e:
            print(f"[!] Error processing listing {listing_num}: {e}")
            return False
        
        finally:
            if driver:
                try:
                    driver.quit()
                except:
                    pass
    
    def _extract_vehicle_data_from_detail_page(self, driver, url: str) -> Dict[str, Any]:
        """Extract comprehensive vehicle data from detail page"""
        try:
            html = driver.page_source
            
            # Initialize vehicle data
            vehicle_data = {
                'title': '',
                'price': '',
                'mileage': '',
                'year': '',
                'make': '',
                'model': '',
                'engine': '',
                'transmission': '',
                'drivetrain': '',
                'color': '',
                'vin': '',
                'raw_text': html[:1000]  # First 1000 chars for debugging
            }
            
            # Extract title
            title_patterns = [
                r'<h1[^>]*>([^<]+)</h1>',
                r'<title>([^<]+)</title>',
                r'class="vehicle-title"[^>]*>([^<]+)',
                r'class="title"[^>]*>([^<]+)'
            ]
            
            for pattern in title_patterns:
                match = re.search(pattern, html, re.IGNORECASE)
                if match:
                    vehicle_data['title'] = match.group(1).strip()
                    break
            
            # Extract price
            price_patterns = [
                r'\$(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)',
                r'Price[:\s]*\$(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)',
                r'class="price"[^>]*>\$(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)'
            ]
            
            for pattern in price_patterns:
                match = re.search(pattern, html, re.IGNORECASE)
                if match:
                    vehicle_data['price'] = f"${match.group(1)}"
                    break
            
            # Extract mileage (same patterns as nodriver)
            m = re.search(r'<div class="veh__mileage"[^>]*><span class="mileage__value"[^>]*>([^<]+)</span>\s*miles', html, re.IGNORECASE)
            if m:
                vehicle_data['mileage'] = m.group(1).strip()
            
            if not vehicle_data['mileage']:
                mileage_patterns = [
                    r'<span class="mileage__value"[^>]*>([^<]+)</span>\s*miles',
                    r'<div[^>]*class="veh__mileage"[^>]*>.*?([0-9]{1,3}(?:,[0-9]{3})+)\s*miles',
                    r"\b([0-9]{1,3}(?:,[0-9]{3})+)\s*(?:mi|miles?)\b",
                    r"Mileage[:\s]*([0-9]{1,3}(?:,[0-9]{3})+)\s*(?:mi|miles?)?",
                    r"Odometer[:\s]*([0-9]{1,3}(?:,[0-9]{3})+)\s*(?:mi|miles?)?",
                    r"([0-9]{1,3}(?:,[0-9]{3})+)\s*miles?",
                    r"([0-9]{1,3}(?:,[0-9]{3})+)\s*mi\b"
                ]
                for pattern in mileage_patterns:
                    mm = re.search(pattern, html, re.IGNORECASE)
                    if mm:
                        vehicle_data['mileage'] = mm.group(1)
                        break
            
            # Extract VIN (same patterns as nodriver)
            m = re.search(r'<div class="info__label"[^>]*>VIN</div>\s*<div class="info__data[^>]*>([A-HJ-NPR-Z0-9]{17})</div>', html, re.IGNORECASE)
            if m:
                vehicle_data['vin'] = m.group(1)
            
            if not vehicle_data['vin']:
                vin_patterns = [
                    r"\bVIN[:\s]*([A-HJ-NPR-Z0-9]{17})\b",
                    r"Vehicle\s+Identification\s+Number[:\s]*([A-HJ-NPR-Z0-9]{17})",
                    r"VIN\s+Number[:\s]*([A-HJ-NPR-Z0-9]{17})",
                    r"([A-HJ-NPR-Z0-9]{17})\s*\(VIN\)",
                    r"VIN[:\s]*([A-HJ-NPR-Z0-9]{17})"
                ]
                for pattern in vin_patterns:
                    mv = re.search(pattern, html, re.IGNORECASE)
                    if mv:
                        vin_candidate = mv.group(1)
                        # Filter out CDN URLs and other false positives
                        if not any(exclude in vin_candidate.lower() for exclude in ['aceae', 'cdn', 'http', 'jpg', 'png', 'gif']):
                            vehicle_data['vin'] = vin_candidate
                            break
            
            # Extract year, make, model from title
            if vehicle_data['title']:
                title = vehicle_data['title']
                year_match = re.search(r'\b(19|20)\d{2}\b', title)
                if year_match:
                    vehicle_data['year'] = year_match.group()
                
                # Extract make and model (basic approach)
                words = title.split()
                if len(words) >= 3:
                    vehicle_data['make'] = words[1] if words[0].isdigit() else words[0]
                    vehicle_data['model'] = ' '.join(words[2:4]) if len(words) > 2 else words[2]
            
            # Extract features (engine, transmission, drivetrain, color)
            def extract_feature(label: str) -> str:
                # Try the specific vehicle info section first
                pat = rf'<div class="info__label"[^>]*>{re.escape(label)}</div>\s*<div class="info__data[^>]*>([^<]+)</div>'
                mm = re.search(pat, html, re.IGNORECASE)
                if mm:
                    return mm.group(1).strip()
                # Fallback to generic patterns
                pat2 = rf"<div[^>]*class=\"feature-label\"[^>]*>\s*{re.escape(label)}\s*</div>\s*<div[^>]*class=\"feature-value\"[^>]*>\s*([^<]+)"
                mm2 = re.search(pat2, html, re.IGNORECASE)
                return mm2.group(1).strip() if mm2 else ''
            
            vehicle_data['engine'] = extract_feature('Engine')
            vehicle_data['transmission'] = extract_feature('Transmission')
            vehicle_data['drivetrain'] = extract_feature('Drivetrain')
            vehicle_data['color'] = extract_feature('Exterior Color')
            
            print(f"[+] Extracted data from detail page: {url}")
            print(f"[+] Extracted vehicle data: {vehicle_data}")
            
            return vehicle_data
            
        except Exception as e:
            print(f"[!] Error extracting vehicle data: {e}")
            return {}
    
    def _setup_driver(self, proxy: str):
        """Setup undetected Chrome driver optimized for testing"""
        import tempfile
        import os
        
        try:
            print(f"[+] Creating Chrome options...")
            options = uc.ChromeOptions()
            
            # CRITICAL: Never use headless mode for testing - it's a major detection flag
            if self.headless:
                print(f"[!] WARNING: Headless mode detected - this will likely trigger bot detection!")
                print(f"[!] For testing, consider using headful mode for better stealth")
                options.add_argument('--headless=new')  # Use new headless mode if absolutely necessary
            
            # Add proxy
            options.add_argument(f'--proxy-server={proxy}')
            print(f"[+] Using proxy: {proxy}")
            
            # OPTIMIZED STEALTH OPTIONS - Focus on most effective techniques
            stealth_options = [
                # Core stealth options (most important)
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-blink-features=AutomationControlled',
                '--disable-extensions',
                '--disable-plugins',
                '--disable-gpu',
                '--disable-web-security',
                '--allow-running-insecure-content',
                
                # Performance and stability
                '--disable-background-timer-throttling',
                '--disable-backgrounding-occluded-windows',
                '--disable-renderer-backgrounding',
                '--disable-hang-monitor',
                '--disable-prompt-on-repost',
                '--disable-sync',
                '--disable-default-apps',
                '--disable-background-networking',
                '--disable-component-update',
                '--disable-domain-reliability',
                '--disable-client-side-phishing-detection',
                '--disable-popup-blocking',
                
                # Window and display options
                '--start-maximized',
                '--window-size=1920,1080',
                '--window-position=0,0',
                
                # Additional stealth options
                '--disable-logging',
                '--disable-notifications',
                '--mute-audio',
                '--no-first-run',
                '--no-default-browser-check',
                '--no-pings',
                '--password-store=basic',
                '--use-mock-keychain',
            ]
            
            for option in stealth_options:
                options.add_argument(option)
            
            print(f"[+] Chrome options configured with {len(stealth_options)} optimized stealth options")
            
            # Create unique user data directory for each instance
            user_data_dir = tempfile.mkdtemp(prefix='chrome_selenium_')
            self.temp_dirs.append(user_data_dir)  # Track for cleanup
            options.add_argument(f'--user-data-dir={user_data_dir}')
            print(f"[+] User data directory: {user_data_dir}")
            
            # REALISTIC USER AGENT ROTATION - Focus on most common ones
            user_agents = [
                # Windows Chrome (most common - 70% of users)
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36',
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36',
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36',
                
                # macOS Chrome (20% of users)
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36',
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36',
                
                # Linux Chrome (10% of users)
                'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36',
                'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36',
            ]
            
            selected_ua = random.choice(user_agents)
            options.add_argument(f'--user-agent={selected_ua}')
            print(f"[+] Using user agent: {selected_ua[:50]}...")
            
            # SIMPLIFIED PREFERENCES - Focus on essential settings
            prefs = {
                "profile.default_content_setting_values": {
                    "notifications": 2,
                    "geolocation": 2,
                    "media_stream": 2,
                },
                "profile.default_content_settings.popups": 0,
                "profile.managed_default_content_settings.images": 1,
            }
            options.add_experimental_option("prefs", prefs)
            
            # Use Chrome version 139 to match installed Chrome
            print(f"[+] Starting Chrome with version 139...")
            driver = uc.Chrome(options=options, version_main=139)
            print(f"[+] Chrome started successfully!")
            
            # ESSENTIAL STEALTH SCRIPTS - Focus on most critical ones
            essential_stealth_scripts = [
                # Remove webdriver property (most critical)
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})",
                
                # Remove automation indicators
                "delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array",
                "delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise",
                "delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol",
                
                # Mock chrome runtime (critical for detection)
                "window.chrome = {runtime: {}, loadTimes: function() {}, csi: function() {}, app: {}}",
                
                # Mock permissions API
                "const originalQuery = window.navigator.permissions.query; window.navigator.permissions.query = (parameters) => (parameters.name === 'notifications' ? Promise.resolve({ state: Notification.permission }) : originalQuery(parameters))",
                
                # Mock screen properties
                "Object.defineProperty(screen, 'availHeight', {get: () => 1040})",
                "Object.defineProperty(screen, 'availWidth', {get: () => 1920})",
                "Object.defineProperty(screen, 'colorDepth', {get: () => 24})",
                "Object.defineProperty(screen, 'pixelDepth', {get: () => 24})",
                
                # Mock timezone
                "Object.defineProperty(Intl.DateTimeFormat.prototype, 'resolvedOptions', {value: function() {return {timeZone: 'America/New_York'}}})",
                
                # Mock hardware concurrency
                "Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 4})",
                
                # Mock device memory
                "Object.defineProperty(navigator, 'deviceMemory', {get: () => 8})",
                
                # Mock connection
                "Object.defineProperty(navigator, 'connection', {get: () => ({effectiveType: '4g', rtt: 100, downlink: 10})})",
                
                # Mock canvas fingerprinting
                "const toDataURL = HTMLCanvasElement.prototype.toDataURL; HTMLCanvasElement.prototype.toDataURL = function() {return 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';}",
                
                # Mock webgl
                "const getParameter = WebGLRenderingContext.prototype.getParameter; WebGLRenderingContext.prototype.getParameter = function(parameter) {if (parameter === 37445) return 'Intel Inc.'; if (parameter === 37446) return 'Intel(R) Iris(TM) Graphics 6100'; return getParameter(parameter);}",
                
                # Final webdriver removal
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})",
            ]
            
            print(f"[+] Executing {len(essential_stealth_scripts)} essential stealth scripts...")
            successful_scripts = 0
            for i, script in enumerate(essential_stealth_scripts):
                try:
                    driver.execute_script(script)
                    successful_scripts += 1
                    if i < 3:  # Only log first few
                        print(f"[+] Stealth script {i+1} executed")
                except Exception as e:
                    if i < 5:  # Only log first few failures
                        print(f"[!] Stealth script {i+1} failed: {e}")
            
            print(f"[+] Stealth scripts completed: {successful_scripts}/{len(essential_stealth_scripts)} successful")
            
            return driver
            
        except Exception as e:
            print(f"[!] Failed to setup driver: {e}")
            return None
    
    def _find_and_click_inventory_link(self, driver) -> bool:
        """Find and click on inventory/vehicles navigation links"""
        print(f"[+] QUICK SEARCH for inventory links...")
        print(f"[+] Method 1: Trying quick CSS selectors...")
        
        # Try cars-for-sale selector first (most common)
        try:
            print(f"[+] Trying selector: a[href*='cars-for-sale']")
            elements = driver.find_elements(By.CSS_SELECTOR, "a[href*='cars-for-sale']")
            if elements:
                print(f"[+] Found {len(elements)} elements with selector: a[href*='cars-for-sale']")
                # Scroll to element and click
                driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", elements[0])
                time.sleep(random.uniform(0.5, 1))
                elements[0].click()
                print(f"[+] SUCCESS: Clicked via selector a[href*='cars-for-sale']")
                return True
        except Exception as e:
            print(f"[!] Error with cars-for-sale selector: {e}")
        
        # Fallback to keyword-based search
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
                            driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", elements[0])
                            time.sleep(random.uniform(0.5, 1))
                            elements[0].click()
                            time.sleep(random.uniform(2, 4))
                            return True
                    except:
                        continue
                        
            except Exception as e:
                continue
                
        return False
    
    async def _save_extracted_data(self, domain: str, successful_extractions: int):
        """Save extracted vehicle data to JSON file"""
        try:
            if not self.extracted_data:
                print(f"[!] No data to save for {domain}")
                return
            
            # Create extracted_data directory if it doesn't exist
            os.makedirs('extracted_data', exist_ok=True)
            
            # Generate filename
            domain_clean = domain.replace('https://', '').replace('www.', '').replace('/', '')
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"vehicles_{domain_clean}_{timestamp}.json"
            filepath = os.path.join('extracted_data', filename)
            
            # Prepare data for JSON
            json_data = {
                'domain': domain,
                'extraction_timestamp': time.time(),
                'total_vehicles': len(self.extracted_data),
                'vehicles': self.extracted_data
            }
            
            # Save to file
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(json_data, f, indent=2, ensure_ascii=False)
            
            print(f"[+] Saved {len(self.extracted_data)} vehicles to {filepath}")
            
        except Exception as e:
            print(f"[!] Error saving extracted data: {e}")
    
    async def _simulate_human_behavior(self, driver):
        """Optimized human behavior simulation for testing"""
        try:
            print(f"[DEBUG] Simulating human behavior...")
            
            # Light mouse movements (less aggressive for testing)
            for _ in range(random.randint(1, 3)):
                x = random.randint(200, 600)
                y = random.randint(200, 400)
                driver.execute_script(f"""
                    const event = new MouseEvent('mousemove', {{
                        clientX: {x},
                        clientY: {y}
                    }});
                    document.dispatchEvent(event);
                """)
                await asyncio.sleep(random.uniform(0.1, 0.2))
            
            # Light scrolling
            for _ in range(random.randint(1, 2)):
                scroll_amount = random.randint(-100, 100)
                driver.execute_script(f"window.scrollBy(0, {scroll_amount});")
                await asyncio.sleep(random.uniform(0.2, 0.4))
            
            # Basic page interactions
            driver.execute_script("""
                // Simulate focus events
                window.dispatchEvent(new Event('focus'));
                document.dispatchEvent(new Event('visibilitychange'));
            """)
            
            # Shorter reading time for testing
            reading_delay = random.uniform(0.5, 1.5)
            print(f"[DEBUG] Human-like reading time: {reading_delay:.1f}s...")
            await asyncio.sleep(reading_delay)
            
            print(f"[DEBUG] Human behavior simulation completed")
            
        except Exception as e:
            print(f"[!] Error in human behavior simulation: {e}")
    
    async def _human_like_delay(self):
        """Human-like delay between actions (optimized for testing)"""
        delay = random.uniform(1.0, 3.0)  # Shorter delays for testing
        print(f"[DEBUG] Human-like delay: {delay:.1f}s")
        await asyncio.sleep(delay)
    
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
