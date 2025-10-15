import time
import random
import threading
import asyncio
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, asdict
from urllib.parse import urlparse
import json
import os

@dataclass
class CrawlMetrics:
    """Structured metrics for crawl operations"""
    domain: str
    proxy: str
    crawler_type: str
    start_time: float
    end_time: Optional[float] = None
    total_duration_seconds: Optional[float] = None
    pages_crawled: int = 0
    listings_extracted: int = 0
    captcha_blocked: bool = False
    captcha_type: str = "none"
    blocked_at_listing: int = 0
    proxy_rotations: int = 0
    proxies_used: List[str] = None
    success_rate: float = 0.0
    avg_time_per_listing: float = 0.0
    errors: List[str] = None
    detailed_timings: Dict[str, float] = None
    
    def __post_init__(self):
        if self.proxies_used is None:
            self.proxies_used = []
        if self.errors is None:
            self.errors = []
        if self.detailed_timings is None:
            self.detailed_timings = {}
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)
    
    def finalize(self):
        """Calculate final metrics"""
        if self.end_time and self.start_time:
            self.total_duration_seconds = self.end_time - self.start_time
        
        if self.listings_extracted > 0 and self.total_duration_seconds:
            self.avg_time_per_listing = self.total_duration_seconds / self.listings_extracted
        
        if self.pages_crawled > 0:
            self.success_rate = self.listings_extracted / self.pages_crawled

class ProxyManager:
    """Manages proxy rotation and availability"""
    
    def __init__(self, proxies: List[str]):
        self.all_proxies = proxies.copy()
        self.used_proxies = set()
        self.lock = threading.Lock()
    
    def get_available_proxies(self) -> List[str]:
        """Get list of proxies not currently in use"""
        with self.lock:
            return [p for p in self.all_proxies if p not in self.used_proxies]
    
    def assign_proxy(self, proxy: str) -> bool:
        """Mark a proxy as in use"""
        with self.lock:
            if proxy in self.used_proxies:
                return False
            self.used_proxies.add(proxy)
            return True
    
    def release_proxy(self, proxy: str):
        """Release a proxy for reuse"""
        with self.lock:
            self.used_proxies.discard(proxy)
    
    def get_next_proxy(self, exclude_proxies: List[str] = None) -> Optional[str]:
        """Get next available proxy, excluding specified ones"""
        available = self.get_available_proxies()
        if exclude_proxies:
            available = [p for p in available if p not in exclude_proxies]
        
        if available:
            return random.choice(available)
        return None
    
    def rotate_proxy(self, current_proxy: str, exclude_proxies: List[str] = None) -> Optional[str]:
        """Rotate from current proxy to next available"""
        self.release_proxy(current_proxy)
        new_proxy = self.get_next_proxy(exclude_proxies)
        if new_proxy:
            self.assign_proxy(new_proxy)
        return new_proxy

class TestFramework:
    """Base framework for proxy testing"""
    
    def __init__(self, domains: List[str], proxies: List[str], max_listings: int = 30):
        self.domains = domains
        self.proxy_manager = ProxyManager(proxies)
        self.max_listings = max_listings
        self.results = {}
        self.lock = threading.Lock()
    
    def create_metrics(self, domain: str, proxy: str, crawler_type: str) -> CrawlMetrics:
        """Create initial metrics object"""
        return CrawlMetrics(
            domain=domain,
            proxy=proxy,
            crawler_type=crawler_type,
            start_time=time.time()
        )
    
    def update_metrics(self, metrics: CrawlMetrics, **kwargs):
        """Update metrics with new data"""
        for key, value in kwargs.items():
            if hasattr(metrics, key):
                setattr(metrics, key, value)
    
    def finalize_metrics(self, metrics: CrawlMetrics):
        """Finalize and store metrics"""
        metrics.end_time = time.time()
        metrics.finalize()
        
        with self.lock:
            domain_key = urlparse(metrics.domain).netloc.replace('www.', '')
            self.results[domain_key] = metrics.to_dict()
    
    def save_results(self, filename: str = None) -> str:
        """Save results to JSON file"""
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"test_results/proxy_test_{timestamp}.json"
        
        filepath = os.path.join(os.getcwd(), filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.results, f, indent=2, ensure_ascii=False)
        
        return filepath
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all test results"""
        if not self.results:
            return {}
        
        total_tests = len(self.results)
        blocked_tests = sum(1 for r in self.results.values() if r.get('captcha_blocked', False))
        total_listings = sum(r.get('listings_extracted', 0) for r in self.results.values())
        total_duration = sum(r.get('total_duration_seconds', 0) for r in self.results.values())
        
        return {
            'total_tests': total_tests,
            'blocked_tests': blocked_tests,
            'success_rate': (total_tests - blocked_tests) / total_tests if total_tests > 0 else 0,
            'total_listings_extracted': total_listings,
            'total_duration_seconds': total_duration,
            'avg_listings_per_test': total_listings / total_tests if total_tests > 0 else 0,
            'avg_duration_per_test': total_duration / total_tests if total_tests > 0 else 0,
            'domains_tested': list(self.results.keys())
        }

class SeleniumTestFramework(TestFramework):
    """Selenium-specific test framework"""
    
    def __init__(self, domains: List[str], proxies: List[str], max_listings: int = 30):
        super().__init__(domains, proxies, max_listings)
        self.crawler_type = "selenium"
    
    def run_parallel_tests(self) -> Dict[str, Any]:
        """Run Selenium tests in parallel using threading"""
        threads = []
        
        # Assign initial proxies to domains
        for i, domain in enumerate(self.domains):
            if i < len(self.proxy_manager.all_proxies):
                proxy = self.proxy_manager.all_proxies[i]
                self.proxy_manager.assign_proxy(proxy)
                
                thread = threading.Thread(
                    target=self._run_single_test,
                    args=(domain, proxy)
                )
                threads.append(thread)
                thread.start()
        
        # Wait for all threads to complete
        for thread in threads:
            thread.join()
        
        return self.results
    
    def _run_single_test(self, domain: str, initial_proxy: str):
        """Run single domain test (to be implemented by specific crawler)"""
        raise NotImplementedError("Must be implemented by specific crawler")

class NodriverTestFramework(TestFramework):
    """Nodriver-specific test framework"""
    
    def __init__(self, domains: List[str], proxies: List[str], max_listings: int = 30):
        super().__init__(domains, proxies, max_listings)
        self.crawler_type = "nodriver"
    
    async def run_parallel_tests(self) -> Dict[str, Any]:
        """Run nodriver tests in parallel using asyncio"""
        tasks = []
        
        # Assign initial proxies to domains
        for i, domain in enumerate(self.domains):
            if i < len(self.proxy_manager.all_proxies):
                proxy = self.proxy_manager.all_proxies[i]
                self.proxy_manager.assign_proxy(proxy)
                
                task = asyncio.create_task(
                    self._run_single_test(domain, proxy)
                )
                tasks.append(task)
        
        # Wait for all tasks to complete
        await asyncio.gather(*tasks)
        
        return self.results
    
    async def _run_single_test(self, domain: str, initial_proxy: str):
        """Run single domain test (to be implemented by specific crawler)"""
        raise NotImplementedError("Must be implemented by specific crawler")

def create_test_config() -> Dict[str, Any]:
    """Create default test configuration"""
    return {
        'domains': [
            "https://www.jeautoworks.com/",
            "https://www.myprestigecar.com/",
            "https://www.adeautonj.com/"
        ],
        'proxies': [
            'http://p100.dynaprox.com:8900',
            'http://p100.dynaprox.com:8902',
            'http://p100.dynaprox.com:8903',
            'http://p100.dynaprox.com:8904',
            'http://p100.dynaprox.com:8905',
            'http://p100.dynaprox.com:8906',
            'http://p100.dynaprox.com:8907',
            'http://p100.dynaprox.com:8908',
            'http://p100.dynaprox.com:8909',
            'http://p100.dynaprox.com:8910',
        ],
        'max_listings': 30,
        'test_timeout': 300,  # 5 minutes per domain
        'retry_attempts': 3,
        'captcha_wait_time': 10
    }
