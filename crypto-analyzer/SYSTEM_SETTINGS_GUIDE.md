# System Settings Management Guide

## Overview
This system provides a web-based interface for managing system configurations without editing code files.

## Features
- ✅ Switch between V1 and V2 batch entry strategies
- ✅ Enable/disable Big4 market trend filter
- ✅ Real-time configuration updates
- ✅ Database-backed (no file permission issues)
- ✅ User-friendly web interface

## Quick Start

### 1. Database Setup (One-time)
The system_settings table should already be created. If not, run:
```bash
python setup_system_settings.py
```

### 2. Start the Server
```bash
python app/main.py
```
The server will start on port 9020.

### 3. Access the UI
Open your browser and navigate to:
```
http://localhost:9020/system-settings
```

### 4. Test the API (Optional)
Run the live test script:
```bash
python test_system_settings_live.py
```

## Configuration Options

### Batch Entry Strategy
- **V2: K线回调策略** (kline_pullback) - Recommended
  - Waits for 15M/5M reverse K-line confirmation
  - 3-batch entry within 60-minute window
  - Better entry timing and risk management

- **V1: 价格分位数策略** (price_percentile)
  - Based on rolling window price percentiles
  - Dynamic entry within 30-minute window
  - Legacy strategy

### Big4 Filter
- **Enabled**: Only trade when BTC/ETH/BNB/SOL show favorable trend
- **Disabled**: Trade all signals regardless of major coin trends

## API Endpoints

### Get All Settings
```
GET /api/system/settings
```

### Get Single Setting
```
GET /api/system/settings/{key}
```

### Update Batch Entry Strategy
```
PUT /api/system/batch-entry-strategy
Body: {"strategy": "kline_pullback" | "price_percentile"}
```

### Update Big4 Filter
```
PUT /api/system/big4-filter
Body: {"enabled": true | false}
```

## Database Schema

Table: `system_settings`
```sql
- id (INT, PRIMARY KEY)
- setting_key (VARCHAR(100), UNIQUE)
- setting_value (TEXT)
- description (VARCHAR(255))
- updated_by (VARCHAR(100))
- created_at (TIMESTAMP)
- updated_at (TIMESTAMP)
```

## Configuration Keys
- `batch_entry_strategy`: 'kline_pullback' or 'price_percentile'
- `big4_filter_enabled`: 'true' or 'false'

## Notes
⚠️ **Important**: After changing settings in the UI, you need to **restart the service** for changes to take effect.

## Files
- `templates/system_settings.html` - Web UI
- `app/api/system_settings_api.py` - API endpoints
- `app/services/system_settings_loader.py` - Configuration loader
- `create_system_settings_table.sql` - Database schema
- `setup_system_settings.py` - Setup script
- `test_system_settings_live.py` - Live API test
- `test_settings_direct.py` - Direct database test

## Troubleshooting

### Cannot connect to server
Make sure the FastAPI server is running:
```bash
python app/main.py
```

### Settings not taking effect
Restart the trading service after changing settings in the UI.

### Database connection error
Check your database credentials in environment variables or connection settings.
