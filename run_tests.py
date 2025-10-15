#!/usr/bin/env python3
"""
Simple test runner for the proxy testing framework
"""

import asyncio
import threading
import os
from selenium_test_crawler import SeleniumTestCrawler
from nodriver_test_crawler import NodriverTestCrawler

# Test domains
DOMAINS = [
    "https://www.jeautoworks.com",
    "https://www.myprestigecar.com", 
    "https://www.adeautonj.com"
]

# Test proxies
PROXIES = [
    "http://p100.dynaprox.com:8900",
    "http://p100.dynaprox.com:8902",
    "http://p100.dynaprox.com:8903",
    "http://p100.dynaprox.com:8904",
    "http://p100.dynaprox.com:8905",
    "http://p100.dynaprox.com:8906",
    "http://p100.dynaprox.com:8907",
    "http://p100.dynaprox.com:8908",
    "http://p100.dynaprox.com:8909",
    "http://p100.dynaprox.com:8910"
]

def run_selenium_tests():
    """Run Selenium tests"""
    print("=" * 60)
    print("RUNNING SELENIUM TESTS")
    print("=" * 60)
    
    crawler = SeleniumTestCrawler(DOMAINS, PROXIES, max_listings=10, headless=False)
    results = crawler.run_parallel_tests()
    
    print("\n" + "=" * 60)
    print("SELENIUM TEST RESULTS")
    print("=" * 60)
    for domain, result in results.items():
        print(f"Domain: {domain}")
        print(f"  Listings: {result['listings_extracted']}")
        print(f"  Captcha blocked: {result['captcha_blocked']}")
        print(f"  Duration: {result['total_duration_seconds']:.2f}s")
        print()

async def run_nodriver_tests():
    """Run nodriver tests"""
    print("=" * 60)
    print("RUNNING NODRIVER TESTS")
    print("=" * 60)
    
    crawler = NodriverTestCrawler(DOMAINS, PROXIES, max_listings=10, headless=False)
    results = await crawler.run_parallel_tests()
    
    print("\n" + "=" * 60)
    print("NODRIVER TEST RESULTS")
    print("=" * 60)
    for domain, result in results.items():
        print(f"Domain: {domain}")
        print(f"  Listings: {result['listings_extracted']}")
        print(f"  Captcha blocked: {result['captcha_blocked']}")
        print(f"  Duration: {result['total_duration_seconds']:.2f}s")
        print()

def main():
    """Main test runner"""
    # Set display environment
    os.environ['DISPLAY'] = ':1'
    
    print("PROXY TESTING FRAMEWORK")
    print("=" * 60)
    print("Testing with domains:", DOMAINS)
    print("Testing with proxies:", len(PROXIES), "proxies")
    print()
    
    # Run Selenium tests
    selenium_thread = threading.Thread(target=run_selenium_tests)
    selenium_thread.start()
    selenium_thread.join()
    
    # Run nodriver tests
    asyncio.run(run_nodriver_tests())
    
    print("=" * 60)
    print("ALL TESTS COMPLETED")
    print("=" * 60)

if __name__ == "__main__":
    main()
