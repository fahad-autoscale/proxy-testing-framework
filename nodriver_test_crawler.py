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
        
        # Track processed URLs for retry mechanism
        self.processed_urls = set()  # Track URLs that were successfully processed
        self.run_type = "first_run"  # Track if this is first run or retry run
        
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
                
                # Human-like page loading and exploration
                print(f"[+] Loading page naturally...")
                await self._human_page_load_behavior(inventory_page)
                
                # Natural page exploration
                await self._simulate_page_exploration(inventory_page)
                
                # Human-like captcha detection
                is_blocked, captcha_type, confidence = await self._human_captcha_detection(inventory_page)
                
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
                        
                        # Human-like behavior with new proxy
                        print(f"[+] Loading page naturally with new proxy...")
                        await self._human_page_load_behavior(inventory_page)
                        await self._simulate_page_exploration(inventory_page)
                        is_blocked, captcha_type, confidence = await self._human_captcha_detection(inventory_page)
                        
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
                listing_urls, template_type = await self._extract_all_listing_urls(inventory_page)
                
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
                listing_urls, current_proxy, domain, metrics, template_type
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

    async def _extract_all_listing_urls(self, page) -> Tuple[List[str], str]:
        """Extract all listing URLs from all pages of the inventory"""
        all_listing_urls = []
        current_page = page
        page_num = 1
        
        # Detect template type first
        template_type = await self._detect_template_type(current_page)
        print(f"[+] Using template type: {template_type}")
        
        # Parse pagination info from the first page only
        print(f"[+] Parsing pagination info from first page...")
        html_content = await current_page.get_content()
        pagination_info = self._parse_pagination_info(html_content, template_type)
        
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
                
                # Use different pagination URL format based on template type
                if template_type == "template2":
                    page_url = f"{base_url}?PageNumber={current_page_num}"
                else:
                    page_url = f"{base_url}?Paging.Page={current_page_num}"
                
                print(f"[DEBUG] Navigating to: {page_url}")
                
                current_page = await current_page.get(page_url)
                
                # Wait for page to load with human-like timing
                page_load_delay = random.uniform(5.0, 10.0)
                print(f"[DEBUG] Waiting {page_load_delay:.1f}s for page to load...")
                await asyncio.sleep(page_load_delay)
            
            # Extract URLs from current page
            page_urls = await self._extract_listing_urls_from_single_page(current_page, template_type)
            all_listing_urls.extend(page_urls)
            
            print(f"[+] Page {current_page_num}: Found {len(page_urls)} URLs (Total so far: {len(all_listing_urls)})")
            
            # Add delay between pages (except after the last page)
            if current_page_num < total_pages:
                between_pages_delay = random.uniform(3.0, 8.0)
                print(f"[DEBUG] Human-like delay between pages: {between_pages_delay:.1f}s...")
                await asyncio.sleep(between_pages_delay)
        
        print(f"[+] Completed pagination: Found {len(all_listing_urls)} total URLs across {total_pages} pages")
        return all_listing_urls, template_type
    
    async def _extract_listing_urls_from_single_page(self, page, template_type: str = "template1") -> List[str]:
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
                if template_type == "template2":
                    # Template 2: Look for hrefs pointing to /details/...
                    hrefs_dbl = re.findall(r'href="(/details/[^"#?\s]+)"', html_content, flags=re.IGNORECASE)
                    hrefs_sgl = re.findall(r"href='(/details/[^'#?\s]+)'", html_content, flags=re.IGNORECASE)
                else:
                    # Template 1: Look for hrefs pointing to /Inventory/Details/...
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
    
    def _parse_pagination_info(self, html_content: str, template_type: str = "template1") -> dict:
        """Parse pagination information from HTML content"""
        try:
            if template_type == "template2":
                return self._parse_template2_pagination(html_content)
            else:
                return self._parse_template1_pagination(html_content)
            
        except Exception as e:
            print(f"[DEBUG] Error parsing pagination info: {e}")
            return None
    
    def _parse_template1_pagination(self, html_content: str) -> dict:
        """Parse pagination information for Template 1 (jeautoworks/myprestigecar-like)"""
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
            print(f"[DEBUG] Error parsing Template 1 pagination info: {e}")
            return None
    
    def _parse_template2_pagination(self, html_content: str) -> dict:
        """Parse pagination information for Template 2 (gtxagroup.com-like)"""
        try:
            # Template 2 uses "Results X - Y of Z" pattern
            # Example: "Results 1 - 24 of 245"
            # But looking at the actual HTML, it's "Results&nbsp;<span class="font-weight-600" data-vehiclesperpage="24">1</span>&nbsp;-&nbsp;<span class="font-weight-600">24</span>&nbsp;of&nbsp;<span class="font-weight-600">245</span>"
            
            # First try the exact pattern from the HTML
            results_match = re.search(r'Results&nbsp;<span[^>]*>(\d+)</span>&nbsp;-&nbsp;<span[^>]*>(\d+)</span>&nbsp;of&nbsp;<span[^>]*>(\d+)</span>', html_content, re.IGNORECASE)
            if results_match:
                start_record = int(results_match.group(1))
                end_record = int(results_match.group(2))
                total_records = int(results_match.group(3))
                
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
            
            # Fallback: Look for "Results X - Y of Z" pattern (simplified)
            results_match = re.search(r'Results\s+(\d+)\s*-\s*(\d+)\s+of\s+(\d+)', html_content, re.IGNORECASE)
            if results_match:
                start_record = int(results_match.group(1))
                end_record = int(results_match.group(2))
                total_records = int(results_match.group(3))
                
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
            
            # Look for "Page X of Y" pattern (this is the actual pattern from the HTML)
            # Try the specific HTML structure first: <li class="inventory-pagination__numbers">Page 1 of 11</li>
            page_match = re.search(r'<li[^>]*class="inventory-pagination__numbers"[^>]*>Page\s+(\d+)\s+of\s+(\d+)</li>', html_content, re.IGNORECASE)
            if page_match:
                current_page = int(page_match.group(1))
                total_pages = int(page_match.group(2))
                
                # Estimate total records (assuming 24 per page)
                total_records = total_pages * 24
                
                return {
                    'total_records': total_records,
                    'total_pages': total_pages,
                    'current_page': current_page,
                    'records_per_page': 24,
                    'start_record': (current_page - 1) * 24 + 1,
                    'end_record': min(current_page * 24, total_records)
                }
            
            # Fallback: Look for generic "Page X of Y" pattern
            page_match = re.search(r'Page\s+(\d+)\s+of\s+(\d+)', html_content, re.IGNORECASE)
            if page_match:
                current_page = int(page_match.group(1))
                total_pages = int(page_match.group(2))
                
                # Estimate total records (assuming 24 per page)
                total_records = total_pages * 24
                
                return {
                    'total_records': total_records,
                    'total_pages': total_pages,
                    'current_page': current_page,
                    'records_per_page': 24,
                    'start_record': (current_page - 1) * 24 + 1,
                    'end_record': min(current_page * 24, total_records)
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
            print(f"[DEBUG] Error parsing Template 2 pagination info: {e}")
            return None
    
    async def _process_listings_in_parallel(self, listing_urls: List[str], proxy: str, 
                                          domain: str, metrics, template_type: str) -> int:
        """Process multiple listings in parallel with fresh browser sessions"""
        # Process all listings in batches of 8
        batch_size = 6
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
                        listing_url, proxy, listing_num, domain, metrics, template_type
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
                                                       listing_num: int, domain: str, metrics, template_type: str) -> bool:
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
                
                # Human-like page loading behavior for detail pages
                print(f"[DEBUG] Loading detail page naturally...")
                await self._human_page_load_behavior(detail_page)
                
                # Check for captcha on detail page using human-like detection
                captcha_detected, captcha_type, confidence = await self._human_captcha_detection(detail_page)
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
                
                # Human-like content verification
                print(f"[DEBUG] Checking if page loaded properly...")
                await self._simulate_visual_inspection(detail_page)
                
                html = await detail_page.get_content()
                html_len = len(html) if html else 0
                print(f"[DEBUG] Detail page content length: {html_len}")
                
                if html_len < 1000:  # Basic sanity check for completely empty pages
                    print(f"[!] Detail page seems empty ({html_len} chars), exploring more...")
                    
                    # Human-like exploration to see if content loads
                    await self._simulate_page_exploration(detail_page)
                    await self._natural_scroll_behavior(detail_page)
                    
                    # Check again after exploration
                    html = await detail_page.get_content()
                    html_len = len(html) if html else 0
                    print(f"[DEBUG] After exploration, content length: {html_len}")
                    
                    if html_len < 1000:
                        print(f"[!] Still no content after exploration, trying next proxy...")
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
                
                # Post-navigation pause - human-like reading time
                print(f"[DEBUG] Reading the page content naturally...")
                await self._simulate_page_exploration(detail_page)
                await self._natural_scroll_behavior(detail_page)
                
                # Skip debug dumps to avoid detection
                
                # Extract vehicle data from detail page
                vehicle_data = await self._extract_vehicle_data_from_detail_page(detail_page, domain, template_type)
                
                if vehicle_data:
                    print(f"[+] Extracted data for listing {listing_num}: {vehicle_data.get('title', 'Unknown')}")
                    
                    # Store the extracted data with additional metadata
                    full_vehicle_record = {
                        'url': listing_url,
                        'listing_number': listing_num,
                        'extraction_timestamp': time.time(),
                        'proxy_used': proxy,
                        'domain': domain,
                        'run_type': self.run_type,  # Mark as first_run or retry_run
                        'vehicle_data': vehicle_data
                    }
                    
                    # Add to extracted data list
                    self.extracted_data.append(full_vehicle_record)
                    
                    # Track this URL as successfully processed
                    self.processed_urls.add(listing_url)
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
    
    def get_missing_urls(self, all_urls: List[str]) -> List[str]:
        """Get URLs that weren't processed in the first run"""
        missing_urls = []
        for url in all_urls:
            if url not in self.processed_urls:
                missing_urls.append(url)
        return missing_urls
    
    def set_retry_mode(self):
        """Set the crawler to retry mode"""
        self.run_type = "retry_run"
        print(f"[+] Set crawler to retry mode")
    
    def get_processed_count(self) -> int:
        """Get the number of successfully processed URLs"""
        return len(self.processed_urls)
    
    def get_processed_urls(self) -> set:
        """Get the set of processed URLs"""
        return self.processed_urls.copy()
    
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
                # "--disable-dev-shm-usage",
                "--start-maximized",
                # "--disable-gpu",
                # "--disable-web-security",
                # "--allow-running-insecure-content",
                "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
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
    
    async def _random_delay(self, min_seconds: float = 2, max_seconds: float = 8):
        """Enhanced random delay with better distribution"""
        delay = random.uniform(min_seconds, max_seconds)
        await asyncio.sleep(delay)
    
    async def _human_like_delay(self):
        """Enhanced human-like delay with more variation"""
        # More realistic human delays: 3-12 seconds
        delay = random.uniform(3, 12)
        print(f"[+] Enhanced human-like delay: {delay:.1f}s")
        await asyncio.sleep(delay)
    
    async def _detect_template_type(self, page) -> str:
        """Detect which template the domain uses based on navigation button text"""
        try:
            print(f"[+] Detecting template type...")
            
            # Get HTML content to analyze
            html_content = await page.get_content()
            if not html_content:
                print(f"[!] No HTML content available for template detection")
                return "template1"  # Default fallback
            
            # Look for the specific navigation button text patterns
            # Template 1: "ALL INVENTORY"
            # Template 2: "ALL CARS FOR SALE"
            
            # Check for Template 2 pattern first (more specific)
            if re.search(r'All Cars For Sale', html_content, re.IGNORECASE):
                print(f"[+] Detected Template 2 (gtxagroup.com-like) - 'All Cars For Sale' found")
                return "template2"
            
            # Check for Template 1 pattern
            if re.search(r'All Inventory', html_content, re.IGNORECASE):
                print(f"[+] Detected Template 1 (jeautoworks/myprestigecar-like) - 'All Inventory' found")
                return "template1"
            
            # Fallback: look for cars-for-sale href pattern
            if re.search(r'href="[^"]*cars-for-sale[^"]*"', html_content, re.IGNORECASE):
                print(f"[+] Found cars-for-sale link, defaulting to Template 2")
                return "template2"
            
            # Default fallback
            print(f"[!] Could not determine template type, defaulting to Template 1")
            return "template1"
            
        except Exception as e:
            print(f"[!] Error detecting template type: {e}")
            return "template1"  # Safe fallback
    
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
    
    async def _human_page_load_behavior(self, page):
        """Simulate human page loading behavior"""
        try:
            # Humans don't immediately check readyState
            # They wait and look around naturally
            
            # Initial wait - humans don't time this precisely
            initial_wait = random.uniform(2.5, 6.0)
            print(f"[DEBUG] Initial page load wait: {initial_wait:.1f}s")
            await asyncio.sleep(initial_wait)
            
            # Simulate looking around the page
            await self._simulate_page_exploration(page)
            
            # Sometimes humans scroll immediately, sometimes they don't
            if random.random() < 0.7:  # 70% chance
                await self._natural_scroll_behavior(page)
            
            # Additional wait - humans process what they see
            processing_wait = random.uniform(1.5, 4.0)
            print(f"[DEBUG] Processing what I see: {processing_wait:.1f}s")
            await asyncio.sleep(processing_wait)
            
        except Exception as e:
            print(f"[!] Error in human page load behavior: {e}")
    
    async def _simulate_page_exploration(self, page):
        """Simulate natural human page exploration"""
        try:
            # Humans look around the page naturally
            exploration_movements = random.randint(2, 5)
            
            for i in range(exploration_movements):
                # Random mouse movements (humans don't move in patterns)
                await page.evaluate(f"""
                    () => {{
                        const event = new MouseEvent('mousemove', {{
                            clientX: {random.randint(50, 1800)},
                            clientY: {random.randint(50, 900)},
                            bubbles: true
                        }});
                        document.dispatchEvent(event);
                    }}
                """, await_promise=True, return_by_value=True)
                
                # Variable pause between movements
                movement_pause = random.uniform(0.3, 1.2)
                await asyncio.sleep(movement_pause)
            
            # Sometimes humans hover over elements
            if random.random() < 0.4:  # 40% chance
                await self._simulate_element_hover(page)
                
        except Exception as e:
            print(f"[!] Error simulating page exploration: {e}")
    
    async def _simulate_element_hover(self, page):
        """Simulate hovering over page elements"""
        try:
            # Get some common elements humans might hover over
            elements = await page.select_all('a, button, img')
            if elements and len(elements) > 0:
                # Pick a random element to hover over
                target_element = random.choice(elements[:min(10, len(elements))])
                
                # Simulate hover
                await target_element.hover()
                
                # Brief pause (humans don't hover for long)
                hover_pause = random.uniform(0.5, 2.0)
                await asyncio.sleep(hover_pause)
                
        except Exception as e:
            print(f"[!] Error simulating element hover: {e}")
    
    async def _natural_scroll_behavior(self, page):
        """Simulate natural human scrolling patterns"""
        try:
            # Humans scroll in different patterns
            scroll_patterns = [
                # Quick scroll down
                lambda: page.evaluate("window.scrollBy(0, 300)", await_promise=True, return_by_value=True),
                # Slow scroll down
                lambda: page.evaluate("window.scrollBy(0, 150)", await_promise=True, return_by_value=True),
                # Scroll up a bit (humans do this)
                lambda: page.evaluate("window.scrollBy(0, -100)", await_promise=True, return_by_value=True),
                # Scroll to top
                lambda: page.evaluate("window.scrollTo(0, 0)", await_promise=True, return_by_value=True)
            ]
            
            # Pick 1-3 scroll actions
            num_scrolls = random.randint(1, 3)
            selected_scrolls = random.sample(scroll_patterns, num_scrolls)
            
            for scroll_action in selected_scrolls:
                await scroll_action()
                
                # Natural pause between scrolls
                scroll_pause = random.uniform(0.8, 2.5)
                await asyncio.sleep(scroll_pause)
                
        except Exception as e:
            print(f"[!] Error in natural scroll behavior: {e}")
    
    async def _human_captcha_detection(self, page):
        """Detect captcha in a human-like way"""
        try:
            # Humans don't immediately analyze the page
            # They notice captchas naturally through visual inspection
            
            # First, simulate looking at the page content
            await self._simulate_visual_inspection(page)
            
            # Then get content (like a human would notice something's wrong)
            html = await page.get_content()
            
            # Only check if page seems suspicious
            if len(html) < 3000:  # Short page might indicate blocking
                print(f"[DEBUG] Page seems unusually short ({len(html)} chars), investigating...")
                
                # Human-like investigation
                await asyncio.sleep(random.uniform(1.0, 3.0))
                
                # Check for obvious captcha indicators
                captcha_indicators = ['captcha', 'verify', 'challenge', 'blocked', 'access denied']
                html_lower = html.lower()
                
                for indicator in captcha_indicators:
                    if indicator in html_lower:
                        print(f"[!] Detected potential blocking: '{indicator}' found")
                        return True, "generic_block", 0.9
                
                return True, "generic_block", 0.8
            
            return False, "none", 0.0
            
        except Exception as e:
            print(f"[!] Error in human captcha detection: {e}")
            return False, "none", 0.0
    
    async def _simulate_visual_inspection(self, page):
        """Simulate human visual inspection of the page"""
        try:
            # Humans look at different parts of the page
            inspection_points = [
                (100, 100),    # Top-left
                (900, 200),    # Top-center
                (1800, 150),   # Top-right
                (500, 500),    # Center
                (1200, 800),   # Bottom-center
            ]
            
            for x, y in random.sample(inspection_points, random.randint(2, 4)):
                await page.evaluate(f"""
                    () => {{
                        const event = new MouseEvent('mousemove', {{
                            clientX: {x},
                            clientY: {y},
                            bubbles: true
                        }});
                        document.dispatchEvent(event);
                    }}
                """, await_promise=True, return_by_value=True)
                
                # Pause to "look" at that area
                look_pause = random.uniform(0.5, 1.5)
                await asyncio.sleep(look_pause)
                
        except Exception as e:
            print(f"[!] Error simulating visual inspection: {e}")
    
    async def _find_and_click_inventory_link(self, page) -> bool:
        """Find and click on inventory/vehicles navigation links - optimized"""
        print(f"[+] QUICK SEARCH for inventory links...")
        
        # Method 1: Quick direct CSS selector attempts first
        try:
            print(f"[+] Method 1: Trying quick CSS selectors...")
            quick_selectors = [
                "a[href='/cars-for-sale']",
                "a:contains('ALL INVENTORY')",
                "a:contains('ALL CARS FOR SALE')",
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
                "a[href='/cars-for-sale']",
                "a:contains('ALL INVENTORY')",
                "a:contains('ALL CARS FOR SALE')",
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
    
    async def _extract_vehicle_data_from_detail_page(self, page, site_name: str, template_type: str) -> Optional[Dict[str, Any]]:
        """Extract vehicle data from a detail page with resilient HTML parsing."""
        try:
            print(f"[+] Extracting data from detail page: {page.url}")
            await page.sleep(2)

            print(f"[+] Using template type for detail extraction: {template_type}")

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
                if template_type == "template2":
                    # Template 2 extraction logic
                    vehicle_data = await self._extract_template2_vehicle_data(html, vehicle_data)
                else:
                    # Template 1 extraction logic (existing)
                    vehicle_data = await self._extract_template1_vehicle_data(html, vehicle_data)

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
    
    async def _extract_template1_vehicle_data(self, html: str, vehicle_data: Dict[str, str]) -> Dict[str, str]:
        """Extract vehicle data for Template 1 (jeautoworks/myprestigecar-like)"""
        try:
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

            return vehicle_data
            
        except Exception as e:
            print(f"[!] Error extracting Template 1 vehicle data: {e}")
            return vehicle_data
    
    async def _extract_template2_vehicle_data(self, html: str, vehicle_data: Dict[str, str]) -> Dict[str, str]:
        """Extract vehicle data for Template 2 (gtxagroup.com-like)"""
        try:
            # Title: Look for vdp-header-bar__title (main title on detail page)
            title_patterns = [
                r'<h1[^>]*class="vdp-header-bar__title[^"]*"[^>]*>\s*(.*?)\s*</h1>',
                r'<h3[^>]*class="vehicle-snapshot__title"[^>]*><a[^>]*>\s*(.*?)\s*</a></h3>',
                r"<title>(.*?)</title>"
            ]
            
            for pattern in title_patterns:
                m = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
                if m:
                    raw_title = re.sub(r"\s+", " ", m.group(1)).strip()
                    # Clean suffix like " for sale at ..."
                    cleaned_title = re.split(r"\s+for sale\b", raw_title, flags=re.IGNORECASE)[0].strip()
                    vehicle_data['title'] = cleaned_title or raw_title
                    # Derive year/make/model from cleaned title
                    m2 = re.match(r"^(\d{4})\s+([A-Za-z0-9\-]+)\s+(.+)$", vehicle_data['title'])
                    if m2:
                        vehicle_data['year'] = m2.group(1)
                        vehicle_data['make'] = m2.group(2)
                        vehicle_data['model'] = m2.group(3)
                    break

            # Price: Look for vdp-header-bar__price (main price on detail page)
            price_patterns = [
                r'<h3[^>]*class="vdp-header-bar__price[^"]*"[^>]*>\s*(\$\s*[0-9,]+)\s*</h3>',
                r'<div[^>]*class="vehicle-snapshot__main-info"[^>]*>\s*(\$\s*[0-9,]+)',
                r'<span[^>]*class="vehicle-snapshot__special-price"[^>]*>(\$\s*[0-9,]+)</span>',
                r'"price":\s*(\d+)'  # JSON-LD schema
            ]
            
            for pattern in price_patterns:
                m = re.search(pattern, html, re.IGNORECASE)
                if m:
                    if pattern == r'"price":\s*(\d+)':
                        # JSON-LD price without $ symbol
                        vehicle_data['price'] = f"${int(m.group(1)):,}"
                    else:
                        vehicle_data['price'] = re.sub(r"\s+", "", m.group(1))
                    break
            
            # Fallback: look for "Email For Price" pattern
            if not vehicle_data['price']:
                email_price_match = re.search(r'Email For Price', html, re.IGNORECASE)
                if email_price_match:
                    vehicle_data['price'] = "Email For Price"

            # Mileage: Look for vdp-header-bar__mileage (main mileage on detail page)
            mileage_patterns = [
                r'<h3[^>]*class="vdp-header-bar__mileage[^"]*"[^>]*>\s*([0-9,]+)\s*</h3>',
                r'<div[^>]*class="vehicle-snapshot__main-info"[^>]*>\s*([0-9]{1,3}(?:,[0-9]{3})+)\s*</div>',
                r"\b([0-9]{1,3}(?:,[0-9]{3})+)\s*(?:mi|miles?)\b",
                r"Mileage[:\s]*([0-9]{1,3}(?:,[0-9]{3})+)\s*(?:mi|miles?)?"
            ]
            
            for pattern in mileage_patterns:
                mm = re.search(pattern, html, re.IGNORECASE)
                if mm:
                    vehicle_data['mileage'] = mm.group(1)
                    break

            # Engine: Look for vdp-info-block__info-item-description with engine
            engine_patterns = [
                r'<div[^>]*class="vdp-info-block__info-item-description"[^>]*>\s*([0-9.]+L\s+[A-Z0-9]+)\s*</div>',
                r'<div[^>]*class="vehicle-snapshot__info-text"[^>]*>\s*([0-9.]+L\s+[A-Z0-9]+)\s*</div>',
                r'Engine[:\s]*([^<\n]+)',
                r'([0-9.]+L\s+[A-Z0-9]+)'
            ]
            
            for pattern in engine_patterns:
                me = re.search(pattern, html, re.IGNORECASE)
                if me:
                    engine_text = me.group(1).strip()
                    # Filter out generic patterns that might match HTML fragments
                    if engine_text and not engine_text.startswith('">') and len(engine_text) > 2:
                        vehicle_data['engine'] = engine_text
                        break

            # Transmission: Look for vdp-info-block__info-item-description with transmission
            transmission_patterns = [
                r'<div[^>]*class="vdp-info-block__info-item-description"[^>]*>\s*(Automatic\s+[0-9]+-Speed)\s*</div>',
                r'<div[^>]*class="vehicle-snapshot__info-text"[^>]*>\s*(Automatic\s+[0-9]+-Speed)\s*</div>',
                r'Transmission[:\s]*([^<\n]+)',
                r'(Automatic\s+[0-9]+-Speed)',
                r'(Manual\s+[0-9]+-Speed)'
            ]
            
            for pattern in transmission_patterns:
                mt = re.search(pattern, html, re.IGNORECASE)
                if mt:
                    transmission_text = mt.group(1).strip()
                    # Filter out generic patterns that might match HTML fragments
                    if transmission_text and not transmission_text.startswith('">') and len(transmission_text) > 2:
                        vehicle_data['transmission'] = transmission_text
                        break

            # Drivetrain: Look for vdp-info-block__info-item-description with drivetrain
            drivetrain_patterns = [
                r'<div[^>]*class="vdp-info-block__info-item-description"[^>]*>\s*(FWD|RWD|AWD|4WD|4X4)\s*</div>',
                r'<div[^>]*class="vehicle-snapshot__info-text"[^>]*>\s*(FWD|RWD|AWD|4WD|4X4)\s*</div>',
                r'Drivetrain[:\s]*([^<\n]+)',
                r'\b(FWD|RWD|AWD|4WD|4X4)\b'
            ]
            
            for pattern in drivetrain_patterns:
                md = re.search(pattern, html, re.IGNORECASE)
                if md:
                    drivetrain_text = md.group(1).strip()
                    # Filter out generic patterns that might match HTML fragments
                    if drivetrain_text and not drivetrain_text.startswith('">') and len(drivetrain_text) > 1:
                        vehicle_data['drivetrain'] = drivetrain_text
                        break

            # Color: Look for vdp-info-block__info-item-description with exterior color
            color_patterns = [
                r'<div[^>]*class="vdp-info-block__info-item-description"[^>]*>\s*(Black|White|Silver|Gray|Red|Blue|Green|Yellow|Orange|Brown|Gold|Tan|Beige)\s*</div>',
                r'<div[^>]*class="vehicle-snapshot__info-text"[^>]*>\s*(Black|White|Silver|Gray|Red|Blue|Green|Yellow|Orange|Brown|Gold|Tan|Beige)\s*</div>',
                r'Exterior Color[:\s]*([^<\n]+)',
                r'Interior Color[:\s]*([^<\n]+)',
                r'\b(Black|White|Silver|Gray|Red|Blue|Green|Yellow|Orange|Brown|Gold|Silver|Tan|Beige)\b'
            ]
            
            for pattern in color_patterns:
                mc = re.search(pattern, html, re.IGNORECASE)
                if mc:
                    color_text = mc.group(1).strip()
                    # Filter out generic patterns that might match HTML fragments
                    if color_text and not color_text.startswith('">') and len(color_text) > 1:
                        vehicle_data['color'] = color_text
                        break

            # VIN: Look for VIN in vdp-info-block__info-item-description
            vin_patterns = [
                r'<div[^>]*class="vdp-info-block__info-item-description[^"]*js-vin-message[^"]*"[^>]*>\s*([A-HJ-NPR-Z0-9]{17})\s*</div>',
                r"\bVIN[:\s]*([A-HJ-NPR-Z0-9]{17})\b",
                r"Vehicle\s+Identification\s+Number[:\s]*([A-HJ-NPR-Z0-9]{17})",
                r"([A-HJ-NPR-Z0-9]{17})\s*\(VIN\)"
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

            return vehicle_data
            
        except Exception as e:
            print(f"[!] Error extracting Template 2 vehicle data: {e}")
            return vehicle_data
    
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
