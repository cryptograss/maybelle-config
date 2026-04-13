"""Command-line interface for the Blue Railroad import bot."""

import argparse
import logging
import subprocess
import sys
from pathlib import Path

from .importer import BlueRailroadImporter
from .wiki_client import MWClientWrapper, DryRunClient
from .submission import update_submission_cid, update_submission_token_id
from .torrent_enrichment import enrich_releases


def get_version() -> str:
    """Get the current git commit hash, or 'unknown' if not available."""
    # Try from package location, then from source checkout
    search_dirs = [
        Path(__file__).parent,
        Path('/opt/blue-railroad-import-src'),
    ]
    for d in search_dirs:
        try:
            result = subprocess.run(
                ['git', 'rev-parse', '--short', 'HEAD'],
                capture_output=True,
                text=True,
                cwd=d,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            continue
    return 'unknown'


def create_wiki_client(args) -> MWClientWrapper | DryRunClient:
    """Create a wiki client based on args."""
    if args.dry_run:
        print("DRY RUN MODE - no changes will be made")
        print(f"  (reading live pages from {args.wiki_url})\n")
        return DryRunClient(wiki_url=args.wiki_url)
    else:
        if not args.username or not args.password:
            print("Error: --username and --password required unless --dry-run", file=sys.stderr)
            sys.exit(1)

        try:
            return MWClientWrapper(args.wiki_url, args.username, args.password)
        except Exception as e:
            print(f"Error connecting to wiki: {e}", file=sys.stderr)
            sys.exit(1)


def _configure_logging(args):
    """Set up logging based on --verbose / --dry-run flags."""
    level = logging.INFO if (args.verbose or args.dry_run) else logging.WARNING
    logging.basicConfig(
        level=level,
        format='%(message)s',
    )


def cmd_import(args):
    """Run the import command."""
    _configure_logging(args)

    # Validate chain data exists
    if not args.chain_data.exists():
        print(f"Error: Chain data file not found: {args.chain_data}", file=sys.stderr)
        sys.exit(1)

    wiki_client = create_wiki_client(args)

    # Run import
    importer = BlueRailroadImporter(
        wiki_client=wiki_client,
        chain_data_path=args.chain_data,
        config_page=args.config_page,
    )

    try:
        results = importer.run(generate_thumbnails=args.thumbnails)

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


def cmd_update_submission(args):
    """Run the update-submission command."""
    _configure_logging(args)
    wiki_client = create_wiki_client(args)

    result = update_submission_cid(
        wiki_client=wiki_client,
        submission_id=args.id,
        ipfs_cid=args.ipfs_cid,
    )

    if result.action == 'error':
        print(f"Error: {result.message}", file=sys.stderr)
        sys.exit(1)
    elif result.action == 'unchanged':
        print(f"No change needed: {result.message}")
    elif result.action == 'updated':
        print(f"Updated: Blue Railroad Submission/{args.id}")
        print(f"  IPFS CID: {args.ipfs_cid}")
    elif result.action == 'created':
        # Shouldn't happen for submissions, but handle it
        print(f"Created: Blue Railroad Submission/{args.id}")


def cmd_mark_minted(args):
    """Run the mark-minted command."""
    _configure_logging(args)
    wiki_client = create_wiki_client(args)

    result = update_submission_token_id(
        wiki_client=wiki_client,
        submission_id=args.id,
        participant_wallet=args.wallet,
        token_id=args.token_id,
    )

    if result.action == 'error':
        print(f"Error: {result.message}", file=sys.stderr)
        sys.exit(1)
    elif result.action == 'unchanged':
        print(f"No change needed: {result.message}")
    elif result.action == 'updated':
        print(f"Updated: Blue Railroad Submission/{args.id}")
        print(f"  Marked as minted: Token #{args.token_id} to {args.wallet}")


def cmd_convert_releases(args):
    """Convert Release pages from wikitext to release-yaml content model."""
    _configure_logging(args)
    wiki_client = create_wiki_client(args)

    from .release_page import convert_releases_to_yaml
    results = convert_releases_to_yaml(wiki_client)

    converted = [r for r in results if r.action == 'updated']
    skipped = [r for r in results if r.action == 'unchanged']
    errors = [r for r in results if r.action == 'error']

    print(f"\nConversion complete:")
    print(f"  Converted: {len(converted)}")
    print(f"  Already release-yaml: {len(skipped)}")
    print(f"  Errors: {len(errors)}")

    for r in errors:
        print(f"  ERROR: {r.page_title}: {r.message}")

    if errors:
        sys.exit(1)


def cmd_fix_bot_proposes(args):
    """Fix Release pages that still have Bot_proposes wikitext content."""
    _configure_logging(args)
    wiki_client = create_wiki_client(args)

    from .release_page import fix_bot_proposes_pages

    results = fix_bot_proposes_pages(wiki=wiki_client)

    updated = [r for r in results if r.action in ('updated', 'created')]
    errors = [r for r in results if r.action == 'error']

    print(f"\nBot_proposes fix complete:")
    print(f"  Fixed: {len(updated)}")
    print(f"  Errors: {len(errors)}")

    for r in errors:
        print(f"  ERROR: {r.page_title}: {r.message}")

    if errors:
        sys.exit(1)


def cmd_clear_torrents(args):
    """Clear BitTorrent fields from all Release pages for regeneration."""
    _configure_logging(args)
    wiki_client = create_wiki_client(args)

    from .release_page import clear_torrent_fields

    results = clear_torrent_fields(wiki=wiki_client)

    print(f"\nClear complete: {len(results)} pages cleared")

    errors = [r for r in results if r.action == 'error']
    for r in errors:
        print(f"  ERROR: {r.page_title}: {r.message}")

    if errors:
        sys.exit(1)


def cmd_enrich_ipfs(args):
    """Enrich Release pages with file_size and file_type from IPFS."""
    _configure_logging(args)
    wiki_client = create_wiki_client(args)

    from .ipfs_enrichment import enrich_release_metadata

    results = enrich_release_metadata(
        wiki=wiki_client,
        gateway_url=args.gateway_url.rstrip('/'),
    )

    updated = [r for r in results if r.action in ('updated', 'created')]
    unchanged = [r for r in results if r.action == 'unchanged']
    errors = [r for r in results if r.action == 'error']

    print(f"\nIPFS enrichment complete:")
    print(f"  Updated: {len(updated)}")
    print(f"  Unchanged: {len(unchanged)}")
    print(f"  Errors: {len(errors)}")

    for r in errors:
        print(f"  ERROR: {r.page_title}: {r.message}")

    if errors:
        sys.exit(1)


def cmd_enrich_torrents(args):
    """Enrich Release pages with BitTorrent metadata via delivery-kid."""
    _configure_logging(args)
    wiki_client = create_wiki_client(args)

    if not args.delivery_kid_api_key:
        print("Error: --delivery-kid-api-key required", file=sys.stderr)
        sys.exit(1)

    results = enrich_releases(
        wiki=wiki_client,
        delivery_kid_url=args.delivery_kid_url.rstrip('/'),
        delivery_kid_api_key=args.delivery_kid_api_key,
    )

    updated = [r for r in results if r.action == 'updated']
    created = [r for r in results if r.action == 'created']
    unchanged = [r for r in results if r.action == 'unchanged']
    errors = [r for r in results if r.action == 'error']

    print(f"\nTorrent enrichment complete:")
    print(f"  Updated: {len(updated)}")
    print(f"  Unchanged: {len(unchanged)}")
    print(f"  Errors: {len(errors)}")

    for r in errors:
        print(f"  ERROR: {r.page_title}: {r.message}")

    if errors:
        sys.exit(1)


def add_common_args(parser):
    """Add common arguments to a parser."""
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
        '--dry-run',
        action='store_true',
        help='Show what would be done without making changes',
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose output',
    )


def main():
    import blue_railroad_import
    version = get_version()
    blue_railroad_import.BOT_VERSION = version
    print(f"Blue Railroad Import Bot (commit: {version})")

    parser = argparse.ArgumentParser(
        description='Blue Railroad PickiPedia tools'
    )
    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # Import command (default behavior for backwards compatibility)
    import_parser = subparsers.add_parser(
        'import',
        help='Import Blue Railroad tokens from chain data to PickiPedia'
    )
    add_common_args(import_parser)
    import_parser.add_argument(
        '--chain-data',
        type=Path,
        required=True,
        help='Path to chainData.json file',
    )
    import_parser.add_argument(
        '--config-page',
        default='PickiPedia:BlueRailroadConfig',
        help='Wiki page containing bot configuration',
    )
    import_parser.add_argument(
        '--thumbnails',
        action='store_true',
        default=True,
        dest='thumbnails',
        help='Generate and upload thumbnails for token videos (default: enabled)',
    )
    import_parser.add_argument(
        '--no-thumbnails',
        action='store_false',
        dest='thumbnails',
        help='Skip thumbnail generation',
    )
    import_parser.set_defaults(func=cmd_import)

    # Update submission command
    update_parser = subparsers.add_parser(
        'update-submission',
        help='Update a submission page with IPFS CID after pinning'
    )
    add_common_args(update_parser)
    update_parser.add_argument(
        '--id',
        type=int,
        required=True,
        help='Submission ID (e.g., 1 for "Blue Railroad Submission/1")',
    )
    update_parser.add_argument(
        '--ipfs-cid',
        required=True,
        help='IPFS CID to record (e.g., bafybeif...)',
    )
    update_parser.set_defaults(func=cmd_update_submission)

    # Mark minted command
    minted_parser = subparsers.add_parser(
        'mark-minted',
        help='Mark a submission as minted with token ID'
    )
    add_common_args(minted_parser)
    minted_parser.add_argument(
        '--id',
        type=int,
        required=True,
        help='Submission ID',
    )
    minted_parser.add_argument(
        '--wallet',
        required=True,
        help='Wallet address that received the token',
    )
    minted_parser.add_argument(
        '--token-id',
        type=int,
        required=True,
        help='Minted token ID',
    )
    minted_parser.set_defaults(func=cmd_mark_minted)

    # Convert releases content model
    convert_parser = subparsers.add_parser(
        'convert-releases',
        help='Convert Release pages from wikitext to release-yaml content model'
    )
    add_common_args(convert_parser)
    convert_parser.set_defaults(func=cmd_convert_releases)

    # Fix Bot_proposes pages
    fix_parser = subparsers.add_parser(
        'fix-bot-proposes',
        help='Replace Bot_proposes wikitext with proper YAML on Release pages'
    )
    add_common_args(fix_parser)
    fix_parser.set_defaults(func=cmd_fix_bot_proposes)

    # Clear torrent fields for regeneration
    clear_parser = subparsers.add_parser(
        'clear-torrents',
        help='Clear BitTorrent metadata from Release pages (for regeneration)'
    )
    add_common_args(clear_parser)
    clear_parser.set_defaults(func=cmd_clear_torrents)

    # Enrich IPFS metadata (file size, file type)
    ipfs_parser = subparsers.add_parser(
        'enrich-ipfs',
        help='Enrich Release pages with file size and type from IPFS gateway'
    )
    add_common_args(ipfs_parser)
    ipfs_parser.add_argument(
        '--gateway-url',
        default='https://ipfs.delivery-kid.cryptograss.live',
        help='IPFS gateway URL (default: https://ipfs.delivery-kid.cryptograss.live)',
    )
    ipfs_parser.set_defaults(func=cmd_enrich_ipfs)

    # Enrich torrents command
    torrent_parser = subparsers.add_parser(
        'enrich-torrents',
        help='Enrich Release pages with BitTorrent metadata from delivery-kid'
    )
    add_common_args(torrent_parser)
    torrent_parser.add_argument(
        '--delivery-kid-url',
        default='https://delivery-kid.cryptograss.live',
        help='Delivery Kid service URL (default: https://delivery-kid.cryptograss.live)',
    )
    torrent_parser.add_argument(
        '--delivery-kid-api-key',
        help='API key for delivery-kid service',
    )
    torrent_parser.set_defaults(func=cmd_enrich_torrents)

    args = parser.parse_args()

    # Handle backwards compatibility: if no subcommand but --chain-data provided,
    # treat as import command
    if args.command is None:
        # Check if this looks like old-style invocation
        if '--chain-data' in sys.argv:
            # Re-parse with 'import' prepended
            sys.argv.insert(1, 'import')
            args = parser.parse_args()
        else:
            parser.print_help()
            sys.exit(1)

    # Run the appropriate command
    args.func(args)


if __name__ == '__main__':
    main()
