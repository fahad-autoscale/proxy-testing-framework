# Proxy Testing Framework

A comprehensive framework for testing residential proxy performance with car dealership websites, featuring advanced multi-domain crawling with integrated captcha detection and proxy rotation.

## 🚀 Project Status

- **✅ Nodriver Implementation**: **COMPLETE** - Production ready with multi-domain support
- **🔄 Selenium Implementation**: Work in progress - Basic functionality available
- **📋 Framework Core**: Complete with metrics and proxy management

## ✨ Key Features

### Nodriver Crawler (Complete)
- **Multi-Domain Support**: Process multiple car dealership websites simultaneously
- **Dynamic URL Extraction**: Automatic domain detection and URL construction
- **Advanced Pagination**: HTML parsing with custom URL generation
- **Fresh Browser Sessions**: New session per listing to avoid detection
- **Parallel Processing**: Configurable batch processing (up to 8 concurrent)
- **Comprehensive Data Extraction**: VIN, mileage, price, specifications with robust parsing
- **Data Export**: JSON and CSV output with detailed vehicle records

### Framework Features
- **Integrated Captcha Detection**: DataDome, Cloudflare, reCAPTCHA, hCAPTCHA, and generic blocks
- **Proxy Management**: Automatic rotation with 10 residential proxies
- **Human-like Behavior**: Random delays, mouse movements, and natural timing
- **Error Handling**: Robust cleanup and recovery mechanisms
- **Metrics Collection**: Comprehensive performance tracking

## 🛠️ Environment Setup

### System Requirements
- **Python**: 3.11+ (tested with 3.11.9)
- **OS**: Linux with X11 display support
- **Browser**: Chrome browser installed
- **Display**: X11 forwarding or virtual display

### Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/fahad-autoscale/proxy-testing-framework.git
   cd proxy-testing-framework
   ```

2. **Create virtual environment**:
   ```bash
   python3.11 -m venv env311
   source env311/bin/activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Set up display environment**:
   ```bash
   export DISPLAY=:1
   ```

## 🎯 Quick Start (Nodriver - Complete)

### Basic Usage
```bash
# Activate environment
source env311/bin/activate
export DISPLAY=:1

# Run multi-domain crawler (default: jeautoworks.com + myprestigecar.com)
python test_nodriver.py
```

### Advanced Usage

#### Single Domain
```bash
# Test only jeautoworks.com
python test_nodriver.py --domains https://www.jeautoworks.com/

# Test only myprestigecar.com
python test_nodriver.py --domains https://www.myprestigecar.com/
```

#### Processing Modes
```bash
# Parallel processing (default)
python test_nodriver.py --mode parallel --max-parallel 2

# Sequential processing
python test_nodriver.py --mode sequential
```

#### Custom Configuration
```bash
# Limit listings per domain
python test_nodriver.py --max-listings 50

# Run in headless mode
python test_nodriver.py --headless

# Custom domains
python test_nodriver.py --domains https://www.jeautoworks.com/ https://www.myprestigecar.com/ https://example.com/
```

## 📊 Supported Domains

### Currently Supported
- **✅ jeautoworks.com**: Complete support with pagination
- **✅ myprestigecar.com**: Complete support with pagination

### Configuration
The framework automatically detects and adapts to different car dealership websites. Each domain gets:
- Independent crawler instance
- Separate proxy assignment
- Domain-specific URL extraction
- Individual data export files

## 🔧 Proxy Configuration

### Residential Proxies
10 IP-authenticated residential proxies:
```
http://p100.dynaprox.com:8900
http://p100.dynaprox.com:8902
http://p100.dynaprox.com:8903
http://p100.dynaprox.com:8904
http://p100.dynaprox.com:8905
http://p100.dynaprox.com:8906
http://p100.dynaprox.com:8907
http://p100.dynaprox.com:8908
http://p100.dynaprox.com:8909
http://p100.dynaprox.com:8910
```

### Proxy Management
- **Automatic Rotation**: When captcha detected
- **Load Distribution**: Proxies distributed across domains
- **Health Monitoring**: Connection status tracking
- **Fallback Handling**: Graceful degradation

## 📈 Data Extraction

### Vehicle Information
- **Basic Details**: Year, Make, Model, Title
- **Pricing**: Current price and financing options
- **Specifications**: Engine, Transmission, Drivetrain, Color
- **Identification**: VIN number with validation
- **Mileage**: Odometer reading with formatting
- **Metadata**: Extraction timestamp, proxy used, domain source

### Output Formats
- **JSON**: Complete vehicle records with metadata
- **CSV**: Summary format for analysis
- **Console**: Real-time progress and results

## 🏗️ Architecture

### Core Components

#### 1. Multi-Domain Test Runner (`test_nodriver.py`)
- **Command-line interface** with argparse
- **Configurable processing modes** (parallel/sequential)
- **Domain management** with independent crawler instances
- **Comprehensive reporting** with success/failure tracking

#### 2. Nodriver Crawler (`nodriver_test_crawler.py`)
- **Dynamic URL extraction** with domain detection
- **Advanced pagination** using HTML parsing
- **Fresh browser sessions** per listing
- **Parallel processing** with configurable batch sizes
- **Robust data extraction** with multiple fallback patterns

#### 3. Framework Core (`proxy_test_framework.py`)
- **Proxy management** with rotation logic
- **Metrics collection** with detailed tracking
- **Base classes** for crawler implementations
- **Error handling** and recovery mechanisms

### File Structure
```
proxy-testing-framework/
├── test_nodriver.py              # 🎯 Main multi-domain test runner (COMPLETE)
├── nodriver_test_crawler.py      # 🎯 Nodriver implementation (COMPLETE)
├── proxy_test_framework.py       # Core framework classes
├── selenium_test_crawler.py      # 🔄 Selenium implementation (WIP)
├── run_tests.py                  # Simple test runner
├── requirements.txt              # Python dependencies
├── extracted_data/               # Output directory (auto-created)
│   ├── vehicles_jeautoworks_com_*.json
│   ├── vehicles_myprestigecar_com_*.json
│   └── *.csv
└── README.md                     # This file
```

## 🛡️ Anti-Detection Features

### Captcha Detection
- **DataDome**: IP-based protection with geo-blocking
- **Cloudflare**: Challenge pages and bot detection
- **reCAPTCHA**: Google's captcha system
- **hCAPTCHA**: Alternative captcha service
- **Generic Blocks**: Access denied, rate limiting, etc.

### Human-like Behavior
- **Random Delays**: 2-8 seconds between actions
- **Mouse Movements**: Simulated cursor movement
- **Scrolling**: Natural page scrolling patterns
- **Reading Time**: Variable delays to mimic human interaction
- **Browser Startup Delays**: 3-8 seconds to avoid detection

### Session Management
- **Fresh Sessions**: New browser instance per listing
- **Profile Isolation**: Unique Chrome profiles
- **Cleanup**: Proper browser termination
- **Error Recovery**: Graceful handling of connection issues

## 📊 Performance Metrics

### Tracking
- **Success Rate**: Percentage of successful extractions
- **Captcha Blocks**: Detection and rotation events
- **Proxy Usage**: Distribution and health status
- **Processing Time**: Per-listing and total duration
- **Data Quality**: Extraction completeness and accuracy

### Output
- **Real-time Console**: Progress updates and alerts
- **Detailed Logs**: Debug information and error traces
- **Summary Reports**: Domain-wise success statistics
- **Data Files**: Structured vehicle records

## 🔄 Work in Progress

### Selenium Implementation
- **Status**: Basic functionality available
- **Features**: Threading-based parallel execution
- **Limitations**: Single-domain support, basic error handling
- **Future**: Multi-domain support, enhanced data extraction

### Planned Enhancements
- **Playwright Crawler**: Additional implementation option
- **Enhanced Metrics**: More detailed performance analysis
- **Configuration Files**: External configuration management
- **Web Interface**: Dashboard for monitoring and control

## 🚨 Troubleshooting

### Common Issues

1. **Display Problems**
   ```bash
   export DISPLAY=:1
   # Or for headless mode
   python test_nodriver.py --headless
   ```

2. **Chrome Version Issues**
   - Framework auto-detects Chrome version 139
   - Ensure Chrome is installed and accessible

3. **Proxy Connection**
   - Verify proxy credentials and connectivity
   - Check network firewall settings

4. **Captcha Detection**
   - Review console output for detection details
   - Adjust confidence thresholds if needed
   - Consider using different proxies

5. **Memory Issues**
   - Reduce batch size: `--max-parallel 1`
   - Limit listings: `--max-listings 10`
   - Use sequential mode: `--mode sequential`

### Debug Mode
```bash
# Enable detailed logging
python test_nodriver.py --domains https://www.jeautoworks.com/ --max-listings 5
```

## 📝 Example Output

### Console Output
```
================================================================================
MULTI-DOMAIN NODRIVER CRAWLER
================================================================================
Domains: ['https://www.jeautoworks.com/', 'https://www.myprestigecar.com/']
Processing mode: parallel
Max parallel domains: 2
Max listings per domain: 100
Headless mode: False
Available proxies: 10

================================================================================
COMPREHENSIVE RESULTS SUMMARY
================================================================================

Domain: https://www.jeautoworks.com/
------------------------------------------------------------
  Status: SUCCESS
  Listings extracted: 38
  Captcha blocked: False
  Extracted vehicles: 38
  Sample vehicles:
    1. 2010 Dodge Grand Caravan - $2,995 - 110,000 miles - VIN: 2D4RN4DE9AR364711
    2. 2003 Subaru Outback - $2,995 - 169,000 miles - VIN: 4S3BH806037660569

Domain: https://www.myprestigecar.com/
------------------------------------------------------------
  Status: SUCCESS
  Listings extracted: 27
  Captcha blocked: False
  Extracted vehicles: 27
  Sample vehicles:
    1. 2022 Cadillac XT5 - $32,457 - 41,459 miles - VIN: 1GYKNDRS1NZ104340
    2. 2019 INFINITI Q50 - $16,746 - 116,317 miles - VIN: JN1EV7AP2KM512241

================================================================================
OVERALL SUMMARY
================================================================================
Total domains processed: 2
Successful domains: 2
Failed domains: 0
Total vehicles extracted: 65
Data files saved to: extracted_data/ directory
```

## 📄 License

This framework is designed for testing and research purposes. Ensure compliance with target website terms of service and applicable laws.

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## 📞 Support

For issues and questions:
- Create an issue on GitHub
- Check the troubleshooting section
- Review console output for error details