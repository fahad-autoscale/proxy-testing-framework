# Proxy Testing Framework

A simplified framework for testing residential proxy performance with Cars For Sale (CFS) domains, featuring Selenium and nodriver implementations with integrated captcha detection and proxy rotation.

## Features

- **Dual Crawler Support**: Both Selenium (undetected-chromedriver) and nodriver implementations
- **Integrated Captcha Detection**: Built-in detection for DataDome, Cloudflare, reCAPTCHA, hCAPTCHA, and generic blocks
- **Proxy Management**: Automatic proxy rotation when captcha blocks are detected
- **Human-like Behavior**: Random delays, mouse movements, and scrolling to avoid detection
- **Parallel Processing**: Test multiple domains simultaneously with unique proxy assignments

## Environment Setup

### Python Version
- **Required**: Python 3.11.9
- **Virtual Environment**: `env311` (included)

### Dependencies
```bash
# Activate virtual environment
source env311/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### System Requirements
- Linux environment with X11 display support
- Chrome browser installed
- DISPLAY environment variable set to `:1`

## Configuration

### Test Domains
- `https://www.jeautoworks.com`
- `https://www.myprestigecar.com` 
- `https://www.adeautonj.com`

### Proxy Configuration
10 IP-authenticated residential proxies:
- `http://p100.dynaprox.com:8900`
- `http://p100.dynaprox.com:8902` through `http://p100.dynaprox.com:8910`

## Usage

### Quick Start
```bash
cd /home/proxy-testing-framework
source env311/bin/activate
export DISPLAY=:1
python run_tests.py
```

### Individual Crawler Tests
```bash
# Test Selenium only
python -c "from selenium_test_crawler import SeleniumTestCrawler; crawler = SeleniumTestCrawler(['https://www.jeautoworks.com'], ['http://p100.dynaprox.com:8900'], max_listings=5); crawler.run_parallel_tests()"

# Test nodriver only
python -c "import asyncio; from nodriver_test_crawler import NodriverTestCrawler; asyncio.run(NodriverTestCrawler(['https://www.jeautoworks.com'], ['http://p100.dynaprox.com:8900'], max_listings=5).run_parallel_tests())"
```

## Core Components

### 1. Proxy Test Framework (`proxy_test_framework.py`)
- `ProxyManager`: Handles proxy assignment and rotation
- `CrawlMetrics`: Tracks comprehensive crawl statistics
- Base classes for Selenium and nodriver implementations

### 2. Selenium Crawler (`selenium_test_crawler.py`)
- Uses undetected-chromedriver for stealth browsing
- Threading-based parallel execution
- Integrated captcha detection
- Unique Chrome user data directories for isolation

### 3. Nodriver Crawler (`nodriver_test_crawler.py`)
- Async/await based implementation
- Advanced human-like behavior simulation
- Integrated captcha detection
- Robust error handling and proxy rotation

### 4. Test Runner (`run_tests.py`)
- Simple test orchestrator
- Runs both Selenium and nodriver tests
- Console output with results

## Captcha Detection

Each crawler includes built-in captcha detection for:
- **DataDome**: IP-based protection with geo-blocking
- **Cloudflare**: Challenge pages and bot detection
- **reCAPTCHA**: Google's captcha system
- **hCAPTCHA**: Alternative captcha service
- **Generic Blocks**: Access denied, rate limiting, etc.

Detection uses:
- Keyword matching in page content and titles
- Regex pattern matching
- HTML length analysis (short pages often indicate blocks)
- Confidence scoring with configurable thresholds

## Human-like Behavior

- **Random Delays**: 2-8 seconds between actions
- **Mouse Movements**: Simulated cursor movement
- **Scrolling**: Random page scrolling
- **Natural Timing**: Variable delays to mimic human interaction

## Test Flow

1. **Initialization**: Set up browsers with assigned proxies
2. **Homepage Check**: Detect captcha on initial page load
3. **Inventory Navigation**: Find and click "cars-for-sale" links
4. **Listing Extraction**: Extract URLs from vehicle cards
5. **Detail Page Crawling**: Navigate to individual listings
6. **Data Extraction**: Extract vehicle information from detail pages
7. **Captcha Monitoring**: Check for blocks every 3 listings
8. **Proxy Rotation**: Switch proxies when captcha detected
9. **Pagination**: Navigate through multiple pages
10. **Metrics Collection**: Track all performance data

## Output

### Console Output
- Real-time progress updates
- Detailed error reporting
- Captcha detection alerts
- Proxy rotation notifications
- Final results summary

## Architecture

```
proxy-testing-framework/
├── proxy_test_framework.py    # Core framework classes
├── selenium_test_crawler.py   # Selenium implementation with captcha detection
├── nodriver_test_crawler.py   # Nodriver implementation with captcha detection
├── run_tests.py              # Simple test runner
├── requirements.txt          # Python dependencies
└── README.md                # This file
```

## Future Additions

- **Playwright Crawler**: Additional crawler implementation
- **Enhanced Metrics**: More detailed performance analysis
- **Configuration Files**: External configuration management
- **Results Export**: JSON/CSV report generation

## Troubleshooting

### Common Issues

1. **Chrome Version Mismatch**
   - Ensure ChromeDriver matches Chrome version
   - Framework auto-detects version 139

2. **Display Issues**
   - Set `export DISPLAY=:1`
   - Ensure X11 forwarding is enabled

3. **Proxy Connection**
   - Verify proxy credentials
   - Check network connectivity

4. **Captcha Detection**
   - Review console output for detection details
   - Adjust confidence thresholds if needed

## License

This framework is designed for testing and research purposes. Ensure compliance with target website terms of service and applicable laws.