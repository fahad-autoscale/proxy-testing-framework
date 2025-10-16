import nodriver as uc
import asyncio
import time
import random
import re
from urllib.parse import urljoin, urlparse
import socket
from typing import Dict, List, Any, Optional, Tuple
import os
import json
from datetime import datetime

from proxy_test_framework import NodriverTestFramework, CrawlMetrics

class NodriverTestCrawler(NodriverTestFramework):
    """Nodriver-based crawler with metrics and proxy rotation"""
    
    def __init__(self, domains: List[str], proxies: List[str], max_listings: int = 30, headless: bool = False):
        super().__init__(domains, proxies, max_listings)
        self.headless = headless
        self.extracted_data = []  # Store all extracted vehicle data
        
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
        """Detect captcha/blocking with confidence scoring - optimized for speed"""
        try:
            html = await page.get_content()
            page_title = await page.evaluate("document.title", await_promise=True, return_by_value=True)
            url = page.url
            
            if not html:
                return False, "none", 0.0
            
            text = html.lower()
            title_lower = page_title.lower() if page_title else ""
            url_lower = url.lower() if url else ""
            
            # Quick check for very short pages (likely captcha/block pages)
            if len(html) < 3000:  # Increased threshold for better detection
                # Quick captcha indicators check
                quick_indicators = ['cmsg', 'cfasync', 'datadome', 'cloudflare', 'recaptcha', 'hcaptcha', 'verify', 'human', 'robot', 'blocked', 'access denied', 'challenge', 'turnstile']
                captcha_found = any(indicator in text for indicator in quick_indicators)
                
                if captcha_found:
                    print(f"[DEBUG] Quick captcha detection: {captcha_found}")
                    return True, "generic_block", 0.95
                elif len(html) < 1000:  # Very short pages are likely blocked
                    print(f"[DEBUG] Very short page detected: {len(html)} chars")
                    return True, "generic_block", 0.8
            
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
        """Run single domain test with nodriver - optimized for fresh sessions per listing"""
        metrics = self.create_metrics(domain, initial_proxy, "nodriver")
        current_proxy = initial_proxy
        
        try:
            print(f"\n[+] Starting nodriver test for {domain} with proxy {current_proxy}")
            
            # Step 1: Get inventory page and extract all listing URLs in one session
            inventory_browser = None
            listing_urls = []
            
            try:
                print(f"[+] Step 1: Extracting listing URLs from inventory page...")
                inventory_browser = await self._setup_browser(current_proxy)
                if not inventory_browser:
                    raise Exception("Failed to setup browser")
                inventory_page = await inventory_browser.get(domain)
                metrics.detailed_timings['browser_setup'] = time.time() - metrics.start_time
                
                # Quick page load check
                print(f"[+] Quick page load check...")
                await inventory_page.sleep(3)
                loading_state = await inventory_page.evaluate("document.readyState", await_promise=True, return_by_value=True)
                print(f"[+] Document ready state: {loading_state}")
                
                if loading_state != "complete":
                    await inventory_page.sleep(2)
                    loading_state = await inventory_page.evaluate("document.readyState", await_promise=True, return_by_value=True)
                    print(f"[+] Final document ready state: {loading_state}")
                
                # Check for captcha on homepage
                html = await inventory_page.get_content()
                page_title = await inventory_page.evaluate("document.title", await_promise=True, return_by_value=True)
                print(f"[+] Page title: {page_title}")
                print(f"[+] HTML length: {len(html)} characters")
                
                is_blocked, captcha_type, confidence = await self.detect_captcha(inventory_page)
                
                if is_blocked:
                    print(f"[!] Captcha detected on homepage: {captcha_type} (confidence: {confidence:.2f})")
                    
                    # Try proxy rotation
                    if current_proxy not in metrics.proxies_used:
                        metrics.proxies_used.append(current_proxy)
                    
                    new_proxy = self.proxy_manager.rotate_proxy(current_proxy, exclude_proxies=[current_proxy])
                    if new_proxy:
                        print(f"[+] Rotating to proxy: {new_proxy}")
                        metrics.proxy_rotations += 1
                        current_proxy = new_proxy
                        
                        # Restart with new proxy
                        try:
                            if inventory_browser:
                                await inventory_browser.stop()
                        except:
                            pass
                        inventory_browser = await self._setup_browser(current_proxy)
                        if not inventory_browser:
                            raise Exception("Failed to setup browser with new proxy")
                        inventory_page = await inventory_browser.get(domain)
                        
                        await inventory_page.sleep(3)
                        html = await inventory_page.get_content()
                        page_title = await inventory_page.evaluate("document.title", await_promise=True, return_by_value=True)
                        print(f"[+] New proxy page title: {page_title}")
                        print(f"[+] New proxy HTML length: {len(html)} characters")
                        is_blocked, captcha_type, confidence = await self.detect_captcha(inventory_page)
                        
                        if is_blocked:
                            print(f"[!] Still blocked with new proxy: {captcha_type}")
                            metrics.captcha_blocked = True
                            metrics.captcha_type = captcha_type
                            metrics.blocked_at_listing = 0
                            return
                        else:
                            print(f"[+] New proxy works! No captcha detected")
                    else:
                        print(f"[!] No more proxies available, stopping crawl")
                        metrics.captcha_blocked = True
                        metrics.captcha_type = captcha_type
                        metrics.blocked_at_listing = 0
                        return
                else:
                    print(f"[+] No captcha detected on homepage")
                
                # Navigate to inventory page
                print(f"[+] Looking for inventory links on {domain}")
                await self._simulate_human_behavior(inventory_page)
                inventory_found = await self._find_and_click_inventory_link(inventory_page)
                if inventory_found:
                    print(f"[+] Inventory link found and clicked")
                    await self._human_like_delay()
                    metrics.pages_crawled += 1
                else:
                    print(f"[!] No inventory link found, proceeding with current page")
                
                # Skip debug dump to avoid detection
                
                # Extract all listing URLs from inventory page
                print(f"[+] Extracting listing URLs from inventory page...")
                listing_urls = await self._extract_all_listing_urls(inventory_page)
                
                if not listing_urls:
                    print(f"[!] No listing URLs found on inventory page")
                    return
                
                print(f"[+] Successfully extracted {len(listing_urls)} listing URLs")
                for idx, url in enumerate(listing_urls):
                    print(f"[DEBUG] LISTING URL {idx+1}: {url}")
                
            except Exception as e:
                print(f"[!] Error during inventory extraction: {e}")
                metrics.errors.append(f"Inventory extraction error: {str(e)}")
                return
            finally:
                # Always close inventory browser session
                if inventory_browser:
                    try:
                        await inventory_browser.stop()
                        print(f"[DEBUG] Inventory browser session closed")
                    except Exception as cleanup_error:
                        print(f"[!] Error cleaning up inventory browser: {cleanup_error}")
                        # Don't let cleanup errors propagate
            
            # Step 2: Process listings in parallel with fresh sessions
            print(f"[+] Step 2: Processing {len(listing_urls)} listings in parallel with fresh sessions...")
            crawl_start = time.time()
            
            # Process listings in parallel
            success_count = await self._process_listings_in_parallel(
                listing_urls, current_proxy, domain, metrics
            )
            
            listings_crawled = success_count
            
            metrics.detailed_timings['total_crawl_time'] = time.time() - crawl_start
            metrics.listings_extracted = listings_crawled
            print(f"[+] Completed crawling {domain}: {listings_crawled} listings in {metrics.detailed_timings['total_crawl_time']:.2f}s")
            print(f"[+] Total extracted data records: {len(self.extracted_data)}")
            
            # Save extracted data to file
            await self._save_extracted_data(domain)
            
        except Exception as e:
            print(f"[!] Fatal error in nodriver test for {domain}: {e}")
            metrics.errors.append(f"Fatal error: {str(e)}")
        
        finally:
            # Finalize metrics
            self.finalize_metrics(metrics)
    
    async def _debug_dump_page(self, page, label: str, preview_chars: int = 1500, save_dir: str = "debug_pages"):
        """Dump page HTML preview to console and save full HTML to file for debugging."""
        try:
            url = getattr(page, 'url', '') or ''
            html = await page.get_content()
            if not html:
                print(f"[DEBUG] ({label}) Empty HTML for {url}")
                return
            # Try to extract <title> from HTML
            title_match = re.search(r"<title>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
            title = title_match.group(1).strip() if title_match else ''
            # Console preview
            print("\n" + "="*60)
            print(f"DEBUG DUMP [{label}] URL: {url}")
            print(f"Title: {title}")
            print(f"HTML length: {len(html)}")
            preview = html[:preview_chars].replace("\n", "\n")
            print(f"Preview (first {preview_chars} chars):\n{preview}")
            print("="*60 + "\n")
            # Save full HTML
            try:
                os.makedirs(save_dir, exist_ok=True)
                parsed = urlparse(url) if url else None
                host = (parsed.netloc if parsed else 'nohost').replace(':', '_')
                path = (parsed.path if parsed else 'nopath').strip('/').replace('/', '_') or 'root'
                timestamp = str(int(time.time()))
                fname = f"{timestamp}_{label}_{host}_{path}.html"
                safe_path = os.path.join(save_dir, fname)
                with open(safe_path, 'w', encoding='utf-8') as f:
                    f.write(html)
                print(f"[DEBUG] Saved full HTML to {safe_path}")
            except Exception as e:
                print(f"[DEBUG] Failed saving HTML dump: {e}")
        except Exception as e:
            print(f"[DEBUG] _debug_dump_page error for {label}: {e}")

    async def _extract_all_listing_urls(self, page) -> List[str]:
        """Extract all listing URLs from all pages of the inventory"""
        all_listing_urls = []
        current_page = page
        page_num = 1
        
        # Parse pagination info from the first page only
        print(f"[+] Parsing pagination info from first page...")
        html_content = await current_page.get_content()
        pagination_info = self._parse_pagination_info(html_content)
        
        if pagination_info:
            total_records = pagination_info['total_records']
            total_pages = pagination_info['total_pages']
            print(f"[+] Pagination info: {total_records} total records across {total_pages} pages")
        else:
            print(f"[+] Could not parse pagination info, will extract from current page only")
            total_pages = 1
        
        # Extract URLs from all pages
        for current_page_num in range(1, total_pages + 1):
            print(f"[+] Extracting URLs from page {current_page_num}/{total_pages}...")
            
            # Navigate to the specific page if not on page 1
            if current_page_num > 1:
                # Extract base URL from current page URL
                current_url = current_page.url
                if '?' in current_url:
                    base_url = current_url.split('?')[0]
                else:
                    base_url = current_url
                page_url = f"{base_url}?Paging.Page={current_page_num}"
                print(f"[DEBUG] Navigating to: {page_url}")
                
                current_page = await current_page.get(page_url)
                
                # Wait for page to load with human-like timing
                page_load_delay = random.uniform(5.0, 10.0)
                print(f"[DEBUG] Waiting {page_load_delay:.1f}s for page to load...")
                await asyncio.sleep(page_load_delay)
            
            # Extract URLs from current page
            page_urls = await self._extract_listing_urls_from_single_page(current_page)
            all_listing_urls.extend(page_urls)
            
            print(f"[+] Page {current_page_num}: Found {len(page_urls)} URLs (Total so far: {len(all_listing_urls)})")
            
            # Add delay between pages (except after the last page)
            if current_page_num < total_pages:
                between_pages_delay = random.uniform(3.0, 8.0)
                print(f"[DEBUG] Human-like delay between pages: {between_pages_delay:.1f}s...")
                await asyncio.sleep(between_pages_delay)
        
        print(f"[+] Completed pagination: Found {len(all_listing_urls)} total URLs across {total_pages} pages")
        return all_listing_urls
    
    async def _extract_listing_urls_from_single_page(self, page) -> List[str]:
        """Extract listing URLs from a single inventory page with human-like behavior"""
        listing_urls = []
        
        try:
            # Human-like pause before starting extraction
            extraction_delay = random.uniform(1.0, 3.0)
            print(f"[DEBUG] Human-like pause before extraction: {extraction_delay:.1f}s...")
            await asyncio.sleep(extraction_delay)
            
            # Use HTML parsing only (nodriver API is unreliable)
            print(f"[+] Using HTML parsing to find detail links...")
            
            # Parse raw HTML for detail links
            html_content = await page.get_content()
            if html_content:
                # Find hrefs pointing to /Inventory/Details/...
                hrefs_dbl = re.findall(r'href="(/Inventory/Details/[^"#?\s]+)"', html_content, flags=re.IGNORECASE)
                hrefs_sgl = re.findall(r"href='(/Inventory/Details/[^'#?\s]+)'", html_content, flags=re.IGNORECASE)
                matches = hrefs_dbl + hrefs_sgl
                # Deduplicate while preserving order
                seen = set()
                for m in matches:
                    if m not in seen:
                        seen.add(m)
                        # Extract base domain from current page URL
                        current_url = page.url
                        if '://' in current_url:
                            base_domain = current_url.split('://')[1].split('/')[0]
                            abs_url = f"https://{base_domain}{m}" if m.startswith('/') else m
                        else:
                            abs_url = m
                        listing_urls.append(abs_url)
                print(f"[+] HTML parsing found {len(listing_urls)} URLs")
            else:
                print(f"[!] No HTML content available")
                
        except Exception as e:
            print(f"[!] HTML parsing failed: {e}")
            listing_urls = []
        
        return listing_urls
    
    def _parse_pagination_info(self, html_content: str) -> dict:
        """Parse pagination information from HTML content"""
        try:
            # Look for "Showing X - Y of Z" pattern
            showing_match = re.search(r'Showing\s+(\d+)\s*-\s*(\d+)\s+of\s+(\d+)', html_content, re.IGNORECASE)
            if showing_match:
                start_record = int(showing_match.group(1))
                end_record = int(showing_match.group(2))
                total_records = int(showing_match.group(3))
                
                # Calculate total pages (assuming 24 records per page based on the website)
                records_per_page = end_record - start_record + 1
                if records_per_page > 0:
                    total_pages = (total_records + records_per_page - 1) // records_per_page
                    
                    # Find current page number
                    current_page = (start_record - 1) // records_per_page + 1
                    
                    # Validate the calculated values
                    if total_pages > 0 and current_page > 0 and current_page <= total_pages:
                        return {
                            'total_records': total_records,
                            'total_pages': total_pages,
                            'current_page': current_page,
                            'records_per_page': records_per_page,
                            'start_record': start_record,
                            'end_record': end_record
                        }
            
            # Fallback: Look for pagination numbers in the HTML
            page_numbers = re.findall(r'<li[^>]*><a[^>]*>(\d+)</a></li>', html_content, re.IGNORECASE)
            if page_numbers:
                page_nums = [int(num) for num in page_numbers if num.isdigit()]
                if page_nums:
                    total_pages = max(page_nums)
                    current_page = 1  # Assume we're on page 1 if we can't determine
                    
                    # Look for active page
                    active_match = re.search(r'<li[^>]*class="[^"]*active[^"]*"[^>]*><a[^>]*>(\d+)</a></li>', html_content, re.IGNORECASE)
                    if active_match:
                        current_page = int(active_match.group(1))
                    
                    # Validate the values
                    if total_pages > 0 and current_page > 0 and current_page <= total_pages:
                        return {
                            'total_records': total_pages * 24,  # Estimate based on typical page size
                            'total_pages': total_pages,
                            'current_page': current_page,
                            'records_per_page': 24,
                            'start_record': (current_page - 1) * 24 + 1,
                            'end_record': min(current_page * 24, total_pages * 24)
                        }
            
            return None
            
        except Exception as e:
            print(f"[DEBUG] Error parsing pagination info: {e}")
            return None
    
    async def _process_listings_in_parallel(self, listing_urls: List[str], proxy: str, 
                                          domain: str, metrics) -> int:
        """Process multiple listings in parallel with fresh browser sessions"""
        # Process all listings in batches of 8
        batch_size = 8
        total_processed = 0
        total_successful = 0
        
        print(f"[+] Processing {len(listing_urls)} listings in batches of {batch_size} with proxy: {proxy}")
        
        # Process listings in batches
        for batch_start in range(0, len(listing_urls), batch_size):
            batch_end = min(batch_start + batch_size, len(listing_urls))
            batch_urls = listing_urls[batch_start:batch_end]
            batch_size_actual = len(batch_urls)
            
            print(f"[+] Processing batch {batch_start//batch_size + 1}: listings {batch_start+1}-{batch_end} ({batch_size_actual} listings)")
            
            # Create tasks for this batch
            tasks = []
            for i, listing_url in enumerate(batch_urls):
                listing_num = batch_start + i + 1
                task = asyncio.create_task(
                    self._process_single_listing_with_fresh_session(
                        listing_url, proxy, listing_num, domain, metrics
                    )
                )
                tasks.append(task)
                
                # Add a small delay between task creation to avoid overwhelming
                if i < batch_size_actual - 1:
                    await asyncio.sleep(random.uniform(0.5, 2.0))
            
            # Wait for all tasks in this batch to complete
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Count successful results in this batch
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
            
            # Add a small delay between batches
            if batch_end < len(listing_urls):
                batch_delay = random.uniform(2.0, 5.0)
                print(f"[DEBUG] Batch delay: {batch_delay:.1f}s before next batch...")
                await asyncio.sleep(batch_delay)
        
        print(f"[+] All parallel processing completed: {total_successful}/{total_processed} successful")
        return total_successful
    
    async def _process_single_listing_with_fresh_session(self, listing_url: str, proxy: str, 
                                                       listing_num: int, domain: str, metrics) -> bool:
        """Process a single listing with a completely fresh browser session"""
        detail_browser = None
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                print(f"[DEBUG] Opening detail page attempt {retry_count + 1}/{max_retries} with proxy: {proxy}")
                
                # Create a completely fresh browser session for this detail page
                detail_browser = await self._setup_browser(proxy)
                if not detail_browser:
                    raise Exception("Failed to setup detail browser")
                detail_page = await detail_browser.get(listing_url)
                
                # Human-like page loading behavior (reduced for parallel processing)
                initial_load_delay = random.uniform(5.0, 10.0)  # Further reduced for parallel processing
                print(f"[DEBUG] Waiting {initial_load_delay:.1f}s for page to fully load...")
                await detail_page.sleep(initial_load_delay)
                
                # Check for captcha on detail page
                captcha_detected, captcha_type, confidence = await self.detect_captcha(detail_page)
                if captcha_detected:
                    print(f"[!] Captcha detected on detail page: {captcha_type} (confidence: {confidence})")
                    try:
                        if detail_browser:
                            await detail_browser.stop()
                    except:
                        pass
                    detail_browser = None
                    
                    # Try next proxy if available
                    if retry_count < max_retries - 1:
                        new_proxy = self.proxy_manager.rotate_proxy(proxy, exclude_proxies=[proxy])
                        if new_proxy:
                            proxy = new_proxy
                            print(f"[DEBUG] Rotating to proxy: {proxy}")
                    
                    retry_count += 1
                    continue
                
                # Verify we got reasonable content
                html = await detail_page.get_content()
                html_len = len(html) if html else 0
                print(f"[DEBUG] Detail page content length: {html_len}")
                
                if html_len < 1000:  # Basic sanity check for completely empty pages
                    print(f"[!] Detail page content too short ({html_len} chars), waiting extra time...")
                    extra_wait = random.uniform(5.0, 10.0)  # Reduced extra wait time
                    print(f"[DEBUG] Extra wait time: {extra_wait:.1f}s...")
                    await detail_page.sleep(extra_wait)
                    
                    # Check again after extra wait
                    html = await detail_page.get_content()
                    html_len = len(html) if html else 0
                    print(f"[DEBUG] After extra wait, content length: {html_len}")
                    
                    if html_len < 1000:
                        print(f"[!] Still no content after extra wait, trying next proxy...")
                        try:
                            if detail_browser:
                                await detail_browser.stop()
                        except:
                            pass
                        detail_browser = None
                        
                        # Try next proxy if available
                        if retry_count < max_retries - 1:
                            new_proxy = self.proxy_manager.rotate_proxy(proxy, exclude_proxies=[proxy])
                            if new_proxy:
                                proxy = new_proxy
                                print(f"[DEBUG] Rotating to proxy: {proxy}")
                        
                        retry_count += 1
                        continue
                
                # Success! We have a valid page
                print(f"[+] Successfully loaded detail page with {html_len} characters")
                
                # Post-navigation pause - human-like reading time (reduced for parallel processing)
                reading_delay = random.uniform(3.0, 8.0)  # Further reduced for parallel processing
                print(f"[DEBUG] Human-like reading time: {reading_delay:.1f}s...")
                await asyncio.sleep(reading_delay)
                
                # Skip debug dumps to avoid detection
                
                # Extract vehicle data from detail page
                vehicle_data = await self._extract_vehicle_data_from_detail_page(detail_page, domain)
                
                if vehicle_data:
                    print(f"[+] Extracted data for listing {listing_num}: {vehicle_data.get('title', 'Unknown')}")
                    
                    # Store the extracted data with additional metadata
                    full_vehicle_record = {
                        'url': listing_url,
                        'listing_number': listing_num,
                        'extraction_timestamp': time.time(),
                        'proxy_used': proxy,
                        'domain': domain,
                        'vehicle_data': vehicle_data
                    }
                    
                    # Add to extracted data list
                    self.extracted_data.append(full_vehicle_record)
                    print(f"[+] Stored vehicle data for listing {listing_num}: {vehicle_data.get('title', 'Unknown')}")
                    return True
                else:
                    print(f"[!] Failed to extract data from listing {listing_num}")
                    return False
                
            except Exception as nav_error:
                print(f"[!] Navigation failed on attempt {retry_count + 1}: {nav_error}")
                try:
                    if detail_browser:
                        await detail_browser.stop()
                except:
                    pass
                detail_browser = None
                
                # Try next proxy if available
                if retry_count < max_retries - 1:
                    new_proxy = self.proxy_manager.rotate_proxy(proxy, exclude_proxies=[proxy])
                    if new_proxy:
                        proxy = new_proxy
                        print(f"[DEBUG] Rotating to proxy: {proxy}")
                
                retry_count += 1
                continue
            finally:
                # Always clean up the detail browser session
                if detail_browser:
                    try:
                        await detail_browser.stop()
                        print(f"[DEBUG] Detail browser session closed successfully")
                    except Exception as cleanup_error:
                        print(f"[!] Error cleaning up detail browser: {cleanup_error}")
                        # Don't let cleanup errors propagate
        
        print(f"[!] Failed to load detail page after {max_retries} attempts")
        return False
    
    async def _save_extracted_data(self, domain: str):
        """Save extracted vehicle data to JSON file"""
        try:
            if not self.extracted_data:
                print(f"[!] No extracted data to save for {domain}")
                return
            
            # Create output directory
            output_dir = "extracted_data"
            os.makedirs(output_dir, exist_ok=True)
            
            # Generate filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            domain_clean = urlparse(domain).netloc.replace('www.', '').replace('.', '_')
            filename = f"{output_dir}/vehicles_{domain_clean}_{timestamp}.json"
            
            # Prepare data for JSON serialization
            json_data = {
                'domain': domain,
                'extraction_timestamp': time.time(),
                'total_vehicles': len(self.extracted_data),
                'vehicles': self.extracted_data
            }
            
            # Save to file
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(json_data, f, indent=2, ensure_ascii=False)
            
            print(f"[+] Saved {len(self.extracted_data)} vehicle records to {filename}")
            
            # Also save a summary CSV
            csv_filename = f"{output_dir}/vehicles_{domain_clean}_{timestamp}.csv"
            await self._save_csv_summary(csv_filename)
            
        except Exception as e:
            print(f"[!] Error saving extracted data: {e}")
    
    async def _save_csv_summary(self, csv_filename: str):
        """Save a CSV summary of extracted vehicle data"""
        try:
            import csv
            
            with open(csv_filename, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = [
                    'listing_number', 'url', 'title', 'year', 'make', 'model', 
                    'price', 'mileage', 'engine', 'transmission', 'drivetrain', 
                    'color', 'vin', 'extraction_timestamp'
                ]
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                
                writer.writeheader()
                for record in self.extracted_data:
                    vehicle_data = record['vehicle_data']
                    writer.writerow({
                        'listing_number': record['listing_number'],
                        'url': record['url'],
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
                        'extraction_timestamp': record['extraction_timestamp']
                    })
            
            print(f"[+] Saved CSV summary to {csv_filename}")
            
        except Exception as e:
            print(f"[!] Error saving CSV summary: {e}")
    
    async def _setup_browser_with_proxy(self, proxy: str):
        """Setup a fresh browser instance with the given proxy"""
        return await self._setup_browser(proxy)
    
    async def _rotate_proxy(self, browser, current_proxy: str):
        """Rotate to the next available proxy"""
        try:
            new_proxy = self.proxy_manager.rotate_proxy(current_proxy, exclude_proxies=[current_proxy])
            if new_proxy:
                return new_proxy
            else:
                print(f"[!] No more proxies available, using current: {current_proxy}")
                return current_proxy
        except Exception as e:
            print(f"[!] Error rotating proxy: {e}")
            return current_proxy
    
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
            
            # Add delay after browser startup to avoid triggering anti-bot detection
            startup_delay = random.uniform(3.0, 8.0)
            print(f"[DEBUG] Browser startup delay: {startup_delay:.1f}s to avoid detection...")
            await asyncio.sleep(startup_delay)
            
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
    
    async def _open_with_retries(self, browser, url: str, max_retries: int = 2, base_wait: float = 2.5):
        """Open a URL with retries and basic sanity checks (HTML length)."""
        attempt = 0
        last_exc = None
        while attempt <= max_retries:
            try:
                print(f"[DEBUG] NAVIGATE attempt {attempt+1}/{max_retries+1}: {url}")
                
                # Check if browser is still valid before navigation
                try:
                    # Test browser health with a simple operation
                    await browser.sleep(0.1)
                except Exception as browser_check_error:
                    print(f"[DEBUG] Browser health check failed: {browser_check_error}")
                    raise RuntimeError(f"Browser session invalid: {browser_check_error}")
                
                page = await browser.get(url)
                # Give it some time to load
                await page.sleep(base_wait)
                try:
                    html = await page.get_content()
                    html_len = len(html) if html else 0
                    print(f"[DEBUG] NAVIGATE content length: {html_len}")
                    if html_len >= 1500:
                        return page
                except Exception as e:
                    print(f"[DEBUG] NAVIGATE get_content failed: {e}")
                # Not good enough, retry after a longer wait
                await asyncio.sleep(base_wait + attempt * 1.5)
            except Exception as e:
                last_exc = e
                print(f"[DEBUG] NAVIGATE exception on attempt {attempt+1}: {e}")
                
                # If it's a StopIteration or browser session error, we need to recover
                if "StopIteration" in str(e) or "browser" in str(e).lower():
                    print(f"[DEBUG] Browser session issue detected, attempting recovery...")
                    try:
                        # Try to close any existing pages and reset
                        await browser.sleep(1.0)
                        # Test if browser is still responsive
                        await browser.sleep(0.5)
                        print(f"[DEBUG] Browser recovery successful")
                    except Exception as recovery_error:
                        print(f"[DEBUG] Browser recovery failed: {recovery_error}")
                        # If recovery fails, we need to restart the browser
                        raise RuntimeError(f"Browser session completely invalid, needs restart: {recovery_error}")
                
                await asyncio.sleep(base_wait + attempt * 2.0)
            attempt += 1
        if last_exc:
            raise last_exc
        raise RuntimeError("Failed to open page with sufficient content after retries")
    
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
        """Find and click on inventory/vehicles navigation links - optimized"""
        print(f"[+] QUICK SEARCH for inventory links...")
        
        # Method 1: Quick direct CSS selector attempts first
        try:
            print(f"[+] Method 1: Trying quick CSS selectors...")
            quick_selectors = [
                "a[href*='cars-for-sale']",
                "a[href*='inventory']", 
                "a:contains('All Inventory')",
                "a:contains('inventory')",
                "a:contains('cars')"
            ]
            
            for selector in quick_selectors:
                try:
                    print(f"[+] Trying selector: {selector}")
                    elements = await page.select_all(selector)
                    if elements and len(elements) > 0:
                        print(f"[+] Found {len(elements)} elements with selector: {selector}")
                        await elements[0].click()
                        await self._random_delay(2, 3)  # Reduced delay
                        print(f"[+] SUCCESS: Clicked via selector {selector}")
                        return True
                except Exception as e:
                    print(f"[!] Failed selector {selector}: {e}")
                    continue
                    
        except Exception as e:
            print(f"[!] Error with quick CSS selectors: {e}")
        
        # Method 2: Limited link search (only first 50 links to save time)
        try:
            print(f"[+] Method 2: Limited link search (first 50 links)...")
            all_links_info = []
            
            # Get only first 50 links to save time
            all_links = await page.select_all('a')
            limited_links = all_links[:50]  # Limit to first 50 links
            print(f"[DEBUG] Checking first {len(limited_links)} links on page")
            
            for link in limited_links:
                try:
                    # Use the correct nodriver API methods
                    text = await link.text() or ""
                    href = await link.get_attribute("href") or ""
                    
                    if text and href:
                        text_lower = text.lower().strip()
                        href_lower = href.lower()
                        
                        # Check if this is an inventory link
                        if ('inventory' in text_lower or 
                            'cars' in text_lower or 
                            'all' in text_lower or
                            'cars-for-sale' in href_lower):
                            
                            all_links_info.append({
                                'text': text.strip(),
                                'href': href,
                                'pathname': href.split('?')[0] if '?' in href else href,
                                'innerHTML': text.strip()[:100]
                            })
                            
                except Exception as e:
                    print(f"[DEBUG] Error processing link: {e}")
                    continue
            
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
        
        # Use direct element selection to find vehicle listings
        try:
            # Just use the working .vehicle-card selector
            elements = await page.select_all('.vehicle-card')
            if elements and len(elements) > 0:
                print(f"[+] Found {len(elements)} vehicle cards with .vehicle-card selector")
                return elements
                
        except Exception as e:
            print(f"[!] Error with direct element listing search: {e}")
        
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
    
    async def _find_next_page_link(self, page) -> Optional[Any]:
        """Find next page link for pagination"""
        try:
            # Look for next page link
            next_selectors = [
                'a[aria-label="Go to the next page"]',
                'a[title="Go to the next page"]',
                '.pagination .page-item:not(.disabled) a[aria-label*="next"]',
                '.pagination .page-item:not(.disabled) a[title*="next"]',
                'a.page-link:not([aria-disabled]) i.fa-arrow-right'
            ]
            
            for selector in next_selectors:
                try:
                    next_links = await page.select_all(selector)
                    if next_links and len(next_links) > 0:
                        print(f"[+] Found next page link with selector: {selector}")
                        return next_links[0]
                except:
                    continue
            
            print(f"[!] No next page link found")
            return None
            
        except Exception as e:
            print(f"[!] Error finding next page link: {e}")
            return None
    
    async def _extract_vehicle_data_from_detail_page(self, page, site_name: str) -> Optional[Dict[str, Any]]:
        """Extract vehicle data from a detail page with resilient HTML parsing."""
        try:
            print(f"[+] Extracting data from detail page: {page.url}")
            await page.sleep(2)

            vehicle_data: Dict[str, str] = {
                'title': '', 'price': '', 'mileage': '', 'year': '', 'make': '', 'model': '',
                'engine': '', 'transmission': '', 'drivetrain': '', 'color': '', 'vin': '', 'raw_text': ''
            }

            # Prefer parsing from full HTML to avoid flaky DOM calls
            html = ''
            try:
                html = await page.get_content()
            except Exception as e:
                print(f"[DEBUG] get_content failed: {e}")

            if html:
                # Title: prefer inventory title wrapper else fall back to <title>, trimming boilerplate
                m = re.search(r"<div[^>]*class=\"inventory-title-wrapper[\s\S]*?<h[1-6][^>]*class=\"inventory-title\"[^>]*>\s*<span[^>]*>(.*?)</span>", html, re.IGNORECASE)
                if not m:
                    m = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
                if m:
                    raw_title = re.sub(r"\s+", " ", m.group(1)).strip()
                    # Clean suffix like " for sale in ... - JE Autoworks LLC"
                    cleaned_title = re.split(r"\s+for sale\b", raw_title, flags=re.IGNORECASE)[0].strip()
                    vehicle_data['title'] = cleaned_title or raw_title
                    # Derive year/make/model from cleaned title
                    m2 = re.match(r"^(\d{4})\s+([A-Za-z0-9\-]+)\s+(.+)$", vehicle_data['title'])
                    if m2:
                        vehicle_data['year'] = m2.group(1)
                        vehicle_data['make'] = m2.group(2)
                        vehicle_data['model'] = m2.group(3)

                # Price: try visible blocks, else meta description ($...)
                m = re.search(r"<div[^>]*class=\"label\"[^>]*>\s*Price\s*</div>\s*<div[^>]*class=\"value\"[^>]*>[\s\S]*?(\$\s*[0-9,]+)", html, re.IGNORECASE)
                if m:
                    vehicle_data['price'] = re.sub(r"\s+", "", m.group(1))
                if not vehicle_data['price']:
                    md = re.search(r"<meta[^>]*name=\"description\"[^>]*content=\"([^\"]+)\"", html, re.IGNORECASE)
                    if md:
                        pm = re.search(r"(\$\s*[0-9,]+)", md.group(1))
                        if pm:
                            vehicle_data['price'] = re.sub(r"\s+", "", pm.group(1))

                # Mileage: try multiple patterns for better extraction
                # Pattern 1: Look for mileage in the vehicle heading section
                m = re.search(r'<div class="veh__mileage"[^>]*><span class="mileage__value"[^>]*>([^<]+)</span>\s*miles', html, re.IGNORECASE)
                if m:
                    vehicle_data['mileage'] = m.group(1).strip()
                
                # Pattern 2: Look for mileage in various other formats
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

                # Engine, Transmission, Drivetrain, Color - improved extraction
                def extract_feature(label: str) -> str:
                    # Try the specific vehicle info section first
                    pat = rf'<div class="info__label"[^>]*>{re.escape(label)}</div>\s*<div class="info__data[^>]*>([^<]+)</div>'
                    mm = re.search(pat, html, re.IGNORECASE)
                    if mm:
                        return mm.group(1).strip()
                    
                    # Fallback to generic patterns
                    pat2 = rf"<div[^>]*class=\\\"feature-label\\\"[^>]*>\s*{re.escape(label)}\s*</div>\s*<div[^>]*class=\\\"feature-value\\\"[^>]*>\s*([^<]+)"
                    mm2 = re.search(pat2, html, re.IGNORECASE)
                    return mm2.group(1).strip() if mm2 else ''

                vehicle_data['engine'] = extract_feature('Engine')
                vehicle_data['transmission'] = extract_feature('Transmission')
                vehicle_data['drivetrain'] = extract_feature('Drivetrain')
                vehicle_data['color'] = extract_feature('Exterior Color')

                # VIN: try multiple patterns for better extraction
                # Pattern 1: Look for VIN in the vehicle info section (most specific)
                m = re.search(r'<div class="info__label"[^>]*>VIN</div>\s*<div class="info__data[^>]*>([A-HJ-NPR-Z0-9]{17})</div>', html, re.IGNORECASE)
                if m:
                    vehicle_data['vin'] = m.group(1)
                
                # Pattern 2: Look for VIN in various other formats (but exclude CDN URLs)
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

                # Raw text (trimmed)
                vehicle_data['raw_text'] = re.sub(r"<[^>]+>", " ", html)
                vehicle_data['raw_text'] = re.sub(r"\s+", " ", vehicle_data['raw_text']).strip()[:2000]

            # If key fields are still empty, try minimal DOM-based fallbacks without calling .text()
            try:
                if not vehicle_data['title']:
                    el = await page.query_selector('h1, .inventory-title, .vehicle-title')
                    if el and hasattr(el, 'inner_text'):
                        try:
                            t = await el.inner_text()
                            vehicle_data['title'] = (t or '').strip()
                        except Exception:
                            pass
            except Exception as e:
                print(f"[DEBUG] DOM fallback for title failed: {e}")

            print(f"[+] Extracted vehicle data: {vehicle_data}")
            return vehicle_data

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
