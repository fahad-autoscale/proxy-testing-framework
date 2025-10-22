#!/usr/bin/env python3
"""
Multi-domain Selenium test with configurable parallel/sequential processing
"""

import asyncio
import time
import argparse
from typing import List, Dict, Any
from selenium_test_crawler import SeleniumTestCrawler

# Configuration
CONFIG = {
    'domains': [
        "https://www.jeautoworks.com/",
        "https://www.myprestigecar.com/"
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
        'http://p100.dynaprox.com:8910'
    ],
    'max_listings': 100,  # Extract all listings on each site
    'headless': False,
    'max_parallel_domains': 2,  # Maximum domains to process in parallel
    'processing_mode': 'parallel'  # 'parallel' or 'sequential'
}

async def process_single_domain(domain: str, proxy: str, max_listings: int, headless: bool) -> Dict[str, Any]:
    """Process a single domain with its own crawler instance"""
    print(f"\n{'='*80}")
    print(f"PROCESSING DOMAIN: {domain}")
    print(f"{'='*80}")
    
    # Create a dedicated crawler for this domain
    crawler = SeleniumTestCrawler(
        domains=[domain],
        proxies=CONFIG['proxies'],
        max_listings=max_listings,
        headless=headless
    )
    
    try:
        # Run the crawler for this specific domain
        results = await crawler.run_parallel_tests()
        
        # Extract results for this domain
        domain_result = results.get(domain.replace('https://', '').replace('www.', '').replace('/', ''), {})
        
        # Add domain-specific extracted data
        if hasattr(crawler, 'extracted_data') and crawler.extracted_data:
            domain_result['extracted_vehicles'] = len(crawler.extracted_data)
            domain_result['sample_vehicles'] = []
            
            # Show first 3 vehicles as samples
            for i, vehicle in enumerate(crawler.extracted_data[:3]):
                vehicle_data = vehicle['vehicle_data']
                domain_result['sample_vehicles'].append({
                    'title': vehicle_data.get('title', 'Unknown'),
                    'price': vehicle_data.get('price', 'N/A'),
                    'mileage': vehicle_data.get('mileage', 'N/A'),
                    'vin': vehicle_data.get('vin', 'N/A')
                })
        
        return {
            'domain': domain,
            'success': True,
            'result': domain_result,
            'crawler': crawler
        }
        
    except Exception as e:
        print(f"[!] Error processing domain {domain}: {e}")
        import traceback
        traceback.print_exc()
        return {
            'domain': domain,
            'success': False,
            'error': str(e),
            'result': {}
        }

async def process_domains_parallel(domains: List[str], max_parallel: int) -> Dict[str, Any]:
    """Process multiple domains in parallel with limited concurrency"""
    print(f"\n{'='*80}")
    print(f"PARALLEL PROCESSING: {len(domains)} domains (max {max_parallel} concurrent)")
    print(f"{'='*80}")
    
    # Create semaphore to limit concurrent domains
    semaphore = asyncio.Semaphore(max_parallel)
    
    async def process_with_semaphore(domain: str, proxy: str) -> Dict[str, Any]:
        async with semaphore:
            return await process_single_domain(domain, proxy, CONFIG['max_listings'], CONFIG['headless'])
    
    # Create tasks for all domains
    tasks = []
    for i, domain in enumerate(domains):
        # Distribute proxies among domains
        proxy = CONFIG['proxies'][i % len(CONFIG['proxies'])]
        task = asyncio.create_task(process_with_semaphore(domain, proxy))
        tasks.append(task)
    
    # Wait for all tasks to complete
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Process results
    all_results = {}
    all_crawlers = []
    
    for i, result in enumerate(results):
        domain = domains[i]
        if isinstance(result, Exception):
            print(f"[!] Domain {domain} failed with exception: {result}")
            all_results[domain] = {
                'success': False,
                'error': str(result),
                'result': {}
            }
        else:
            all_results[domain] = result
            if result.get('success') and 'crawler' in result:
                all_crawlers.append(result['crawler'])
    
    return {
        'results': all_results,
        'crawlers': all_crawlers
    }

async def process_domains_sequential(domains: List[str]) -> Dict[str, Any]:
    """Process multiple domains sequentially"""
    print(f"\n{'='*80}")
    print(f"SEQUENTIAL PROCESSING: {len(domains)} domains")
    print(f"{'='*80}")
    
    all_results = {}
    all_crawlers = []
    
    for i, domain in enumerate(domains):
        # Use different proxy for each domain
        proxy = CONFIG['proxies'][i % len(CONFIG['proxies'])]
        
        result = await process_single_domain(domain, proxy, CONFIG['max_listings'], CONFIG['headless'])
        all_results[domain] = result
        
        if result.get('success') and 'crawler' in result:
            all_crawlers.append(result['crawler'])
        
        # Add delay between domains for sequential processing
        if i < len(domains) - 1:
            delay = 5.0  # 5 second delay between domains
            print(f"[DEBUG] Sequential delay: {delay}s before next domain...")
            await asyncio.sleep(delay)
    
    return {
        'results': all_results,
        'crawlers': all_crawlers
    }

def print_summary(all_results: Dict[str, Any], all_crawlers: List):
    """Print comprehensive summary of all domain processing results"""
    print(f"\n{'='*80}")
    print("COMPREHENSIVE RESULTS SUMMARY")
    print(f"{'='*80}")
    
    total_vehicles = 0
    successful_domains = 0
    failed_domains = 0
    
    for domain, result in all_results.items():
        print(f"\nDomain: {domain}")
        print("-" * 60)
        
        if result.get('success'):
            successful_domains += 1
            domain_result = result.get('result', {})
            
            print(f"  Status: SUCCESS")
            print(f"  Listings extracted: {domain_result.get('listings_extracted', 0)}")
            print(f"  Captcha blocked: {domain_result.get('captcha_blocked', False)}")
            print(f"  Captcha type: {domain_result.get('captcha_type', 'none')}")
            print(f"  Errors: {domain_result.get('errors', [])}")
            
            # Show extracted vehicles summary
            extracted_count = domain_result.get('extracted_vehicles', 0)
            if extracted_count > 0:
                total_vehicles += extracted_count
                print(f"  Extracted vehicles: {extracted_count}")
                
                # Show sample vehicles
                sample_vehicles = domain_result.get('sample_vehicles', [])
                if sample_vehicles:
                    print("  Sample vehicles:")
                    for i, vehicle in enumerate(sample_vehicles):
                        print(f"    {i+1}. {vehicle.get('title', 'Unknown')} - {vehicle.get('price', 'N/A')} - {vehicle.get('mileage', 'N/A')} miles - VIN: {vehicle.get('vin', 'N/A')}")
                    if extracted_count > len(sample_vehicles):
                        print(f"    ... and {extracted_count - len(sample_vehicles)} more vehicles")
            else:
                print(f"  Extracted vehicles: 0")
        else:
            failed_domains += 1
            print(f"  Status: FAILED")
            print(f"  Error: {result.get('error', 'Unknown error')}")
    
    # Overall summary
    print(f"\n{'='*80}")
    print("OVERALL SUMMARY")
    print(f"{'='*80}")
    print(f"Total domains processed: {len(all_results)}")
    print(f"Successful domains: {successful_domains}")
    print(f"Failed domains: {failed_domains}")
    print(f"Total vehicles extracted: {total_vehicles}")
    print(f"Data files saved to: extracted_data/ directory")
    
    # Show crawler-specific data
    for i, crawler in enumerate(all_crawlers):
        if hasattr(crawler, 'extracted_data') and crawler.extracted_data:
            domain_name = list(all_results.keys())[i]
            print(f"\nCrawler {i+1} ({domain_name}): {len(crawler.extracted_data)} vehicles extracted")

async def main():
    """Main function with configurable domain processing"""
    # Set DISPLAY environment variable for Chrome
    import os
    os.environ['DISPLAY'] = ':1'
    print(f"[+] Set DISPLAY environment variable to :1")
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Multi-domain Selenium crawler test')
    parser.add_argument('--domains', nargs='+', default=CONFIG['domains'], 
                       help='List of domains to crawl')
    parser.add_argument('--mode', choices=['parallel', 'sequential'], default=CONFIG['processing_mode'],
                       help='Processing mode: parallel or sequential')
    parser.add_argument('--max-parallel', type=int, default=CONFIG['max_parallel_domains'],
                       help='Maximum domains to process in parallel')
    parser.add_argument('--max-listings', type=int, default=CONFIG['max_listings'],
                       help='Maximum listings to extract per domain')
    parser.add_argument('--headless', action='store_true', default=CONFIG['headless'],
                       help='Run in headless mode')
    
    args = parser.parse_args()
    
    # Update config with command line arguments
    CONFIG['domains'] = args.domains
    CONFIG['processing_mode'] = args.mode
    CONFIG['max_parallel_domains'] = args.max_parallel
    CONFIG['max_listings'] = args.max_listings
    CONFIG['headless'] = args.headless
    
    print(f"\n{'='*80}")
    print("MULTI-DOMAIN SELENIUM CRAWLER")
    print(f"{'='*80}")
    print(f"Domains: {CONFIG['domains']}")
    print(f"Processing mode: {CONFIG['processing_mode']}")
    print(f"Max parallel domains: {CONFIG['max_parallel_domains']}")
    print(f"Max listings per domain: {CONFIG['max_listings']}")
    print(f"Headless mode: {CONFIG['headless']}")
    print(f"Available proxies: {len(CONFIG['proxies'])}")
    
    # Process domains based on mode
    if CONFIG['processing_mode'] == 'parallel':
        results = await process_domains_parallel(CONFIG['domains'], CONFIG['max_parallel_domains'])
    else:
        results = await process_domains_sequential(CONFIG['domains'])
    
    # Print comprehensive summary
    print_summary(results['results'], results['crawlers'])

if __name__ == "__main__":
    asyncio.run(main())
