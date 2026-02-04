# Blue Railroad Import Bot

Python bot that imports Blue Railroad NFT token data from chain data into [PickiPedia](https://pickipedia.xyz).

## Overview

This bot reads chain data JSON (fetched from the Blue Railroad smart contracts on Optimism) and creates/updates wiki pages on PickiPedia for:

- Individual token pages (Blue Railroad Token 0, 1, 2, etc.)
- Leaderboard pages showing token ownership rankings

## Installation

```bash
pip install -e .
```

Or install dependencies directly:

```bash
pip install -r requirements.txt
```

## Usage

### Dry Run (no wiki changes)

```bash
python -m blue_railroad_import \
  --chain-data /path/to/chainData.json \
  --dry-run
```

### Live Import

```bash
python -m blue_railroad_import \
  --chain-data /path/to/chainData.json \
  --wiki-url https://pickipedia.xyz \
  --username "BotUsername@botpassword" \
  --password "your-bot-password"
```

### Options

- `--chain-data`: Path to chainData.json (required)
- `--wiki-url`: MediaWiki API URL (default: https://pickipedia.xyz)
- `--username`: MediaWiki bot username (required for live import)
- `--password`: MediaWiki bot password (required for live import)
- `--config-page`: Wiki page with bot configuration (default: PickiPedia:BlueRailroadConfig)
- `--dry-run`: Show what would be imported without making changes
- `-v, --verbose`: Verbose output

## Configuration

The bot reads its configuration from a wiki page (default: `PickiPedia:BlueRailroadConfig`). This allows configuring:

- **Sources**: Which chain data keys to read (e.g., `blueRailroads`, `blueRailroadV2s`)
- **Leaderboards**: Which leaderboard pages to generate, with optional filters

See the [PickiPedia:BlueRailroadConfig](https://pickipedia.xyz/wiki/PickiPedia:BlueRailroadConfig) page for the current configuration.

## V1 vs V2 Tokens

The bot handles both V1 and V2 Blue Railroad contracts:

- **V1 tokens**: Store video as IPFS URI string, date as timestamp
- **V2 tokens**: Store video as bytes32 hash (converted to CIDv0), blockheight instead of date

The bot automatically detects token version and generates appropriate wiki content.

## Development

### Running Tests

```bash
pytest tests/ -v
```

### Project Structure

```
blue_railroad_import/
├── cli.py           # Command-line interface
├── models.py        # Data models (Token, BotConfig, etc.)
├── chain_data.py    # Chain data loading and parsing
├── config_parser.py # Wiki config page parsing
├── importer.py      # Main import orchestration
├── token_page.py    # Token page content generation
├── leaderboard.py   # Leaderboard page generation
└── wiki_client.py   # MediaWiki API client
```

## License

MIT
