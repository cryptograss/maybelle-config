"""Command-line interface for the Blue Railroad import bot."""

import argparse
import subprocess
import sys
from pathlib import Path

from .importer import BlueRailroadImporter
from .wiki_client import MWClientWrapper, DryRunClient
from .submission import update_submission_cid, update_submission_token_id


def get_version() -> str:
    """Get the current git commit hash, or 'unknown' if not available."""
    try:
        result = subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
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


def cmd_import(args):
    """Run the import command."""
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
        verbose=args.verbose or args.dry_run,
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
    wiki_client = create_wiki_client(args)

    result = update_submission_cid(
        wiki_client=wiki_client,
        submission_id=args.id,
        ipfs_cid=args.ipfs_cid,
        verbose=args.verbose or args.dry_run,
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
    wiki_client = create_wiki_client(args)

    result = update_submission_token_id(
        wiki_client=wiki_client,
        submission_id=args.id,
        participant_wallet=args.wallet,
        token_id=args.token_id,
        verbose=args.verbose or args.dry_run,
    )

    if result.action == 'error':
        print(f"Error: {result.message}", file=sys.stderr)
        sys.exit(1)
    elif result.action == 'unchanged':
        print(f"No change needed: {result.message}")
    elif result.action == 'updated':
        print(f"Updated: Blue Railroad Submission/{args.id}")
        print(f"  Marked as minted: Token #{args.token_id} to {args.wallet}")


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
    version = get_version()
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
