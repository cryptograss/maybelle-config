"""Command-line interface for the Blue Railroad import bot."""

import argparse
import sys
from pathlib import Path

from .importer import BlueRailroadImporter
from .wiki_client import MWClientWrapper, DryRunClient


def main():
    parser = argparse.ArgumentParser(
        description='Import Blue Railroad tokens from chain data to PickiPedia'
    )

    parser.add_argument(
        '--chain-data',
        type=Path,
        required=True,
        help='Path to chainData.json file',
    )

    parser.add_argument(
        '--wiki-url',
        default='https://pickipedia.xyz',
        help='MediaWiki site URL (default: https://pickipedia.xyz)',
    )

    parser.add_argument(
        '--username',
        help='MediaWiki bot username',
    )

    parser.add_argument(
        '--password',
        help='MediaWiki bot password',
    )

    parser.add_argument(
        '--config-page',
        default='PickiPedia:BlueRailroadConfig',
        help='Wiki page containing bot configuration',
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be done without making changes',
    )

    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose output',
    )

    args = parser.parse_args()

    # Validate chain data exists
    if not args.chain_data.exists():
        print(f"Error: Chain data file not found: {args.chain_data}", file=sys.stderr)
        sys.exit(1)

    # Create wiki client
    if args.dry_run:
        print("DRY RUN MODE - no changes will be made")
        print(f"  (reading live pages from {args.wiki_url})\n")
        wiki_client = DryRunClient(wiki_url=args.wiki_url)
    else:
        if not args.username or not args.password:
            print("Error: --username and --password required unless --dry-run", file=sys.stderr)
            sys.exit(1)

        try:
            wiki_client = MWClientWrapper(args.wiki_url, args.username, args.password)
        except Exception as e:
            print(f"Error connecting to wiki: {e}", file=sys.stderr)
            sys.exit(1)

    # Run import
    importer = BlueRailroadImporter(
        wiki_client=wiki_client,
        chain_data_path=args.chain_data,
        config_page=args.config_page,
        verbose=args.verbose or args.dry_run,
    )

    try:
        results = importer.run()

        # Print final summary
        print("\n" + "=" * 50)
        print("IMPORT COMPLETE")
        print("=" * 50)
        print(f"Token pages:       {len(results.token_pages_created)} created, {len(results.token_pages_updated)} updated, "
              f"{len(results.token_pages_unchanged)} unchanged, {len(results.token_pages_error)} errors")
        print(f"Leaderboard pages: {len(results.leaderboard_pages_created)} created, {len(results.leaderboard_pages_updated)} updated, "
              f"{len(results.leaderboard_pages_unchanged)} unchanged, {len(results.leaderboard_pages_error)} errors")

        if results.errors:
            print("\nErrors:")
            for error in results.errors:
                print(f"  - {error}")
            sys.exit(1)

    except Exception as e:
        print(f"\nFatal error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
