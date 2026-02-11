"""Main import orchestration."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .models import BotConfig, Token
from .chain_data import load_chain_data, aggregate_tokens_from_sources
from .config_parser import parse_config_from_wikitext, get_default_config
from .leaderboard import generate_leaderboard_content
from .token_page import generate_token_page_content
from .wiki_client import WikiClientProtocol, SaveResult
from .thumbnail import generate_thumbnail, get_thumbnail_filename


CONFIG_PAGE = 'PickiPedia:BlueRailroadConfig'


@dataclass
class ImportResults:
    """Results from an import run."""
    token_pages: list[SaveResult] = field(default_factory=list)
    leaderboard_pages: list[SaveResult] = field(default_factory=list)

    def _by_action(self, results: list[SaveResult], action: str) -> list[SaveResult]:
        return [r for r in results if r.action == action]

    @property
    def token_pages_created(self) -> list[SaveResult]:
        return self._by_action(self.token_pages, 'created')

    @property
    def token_pages_updated(self) -> list[SaveResult]:
        return self._by_action(self.token_pages, 'updated')

    @property
    def token_pages_unchanged(self) -> list[SaveResult]:
        return self._by_action(self.token_pages, 'unchanged')

    @property
    def token_pages_error(self) -> list[SaveResult]:
        return self._by_action(self.token_pages, 'error')

    @property
    def leaderboard_pages_created(self) -> list[SaveResult]:
        return self._by_action(self.leaderboard_pages, 'created')

    @property
    def leaderboard_pages_updated(self) -> list[SaveResult]:
        return self._by_action(self.leaderboard_pages, 'updated')

    @property
    def leaderboard_pages_unchanged(self) -> list[SaveResult]:
        return self._by_action(self.leaderboard_pages, 'unchanged')

    @property
    def leaderboard_pages_error(self) -> list[SaveResult]:
        return self._by_action(self.leaderboard_pages, 'error')

    @property
    def errors(self) -> list[str]:
        return [
            f"{r.page_title}: {r.message}"
            for r in self.token_pages + self.leaderboard_pages
            if r.action == 'error'
        ]


class BlueRailroadImporter:
    """Main importer class that orchestrates the import process."""

    def __init__(
        self,
        wiki_client: WikiClientProtocol,
        chain_data_path: Path,
        config_page: str = CONFIG_PAGE,
        verbose: bool = False,
    ):
        self.wiki = wiki_client
        self.chain_data_path = chain_data_path
        self.config_page = config_page
        self.verbose = verbose

    def log(self, message: str):
        """Log a message if verbose mode is enabled."""
        if self.verbose:
            print(message)

    def load_config(self) -> BotConfig:
        """Load configuration from wiki page or use defaults."""
        self.log(f"Loading config from: {self.config_page}")

        wiki_content = self.wiki.get_page_content(self.config_page)
        if wiki_content:
            config = parse_config_from_wikitext(wiki_content)
            if config:
                self.log(f"  Found {len(config.sources)} source(s)")
                self.log(f"  Found {len(config.leaderboards)} leaderboard(s)")
                return config

        self.log("  Using default configuration")
        return get_default_config()

    def load_tokens(self, config: BotConfig) -> dict[str, Token]:
        """Load and aggregate all tokens from chain data."""
        self.log(f"Loading chain data from: {self.chain_data_path}")

        chain_data = load_chain_data(self.chain_data_path)
        tokens = aggregate_tokens_from_sources(chain_data, config.sources)

        self.log(f"  Loaded {len(tokens)} total tokens from {len(config.sources)} source(s)")
        return tokens

    def ensure_thumbnail(self, token: Token) -> bool:
        """Ensure a thumbnail exists for the token's video.

        Returns True if thumbnail exists or was successfully uploaded,
        False if thumbnail generation/upload failed or no video exists.
        """
        if not token.ipfs_cid:
            self.log(f"  No IPFS CID for token {token.token_id}, skipping thumbnail")
            return False

        filename = get_thumbnail_filename(token.token_id)

        # Check if thumbnail already exists
        if self.wiki.file_exists(filename):
            self.log(f"  Thumbnail already exists: {filename}")
            return True

        # Generate thumbnail
        self.log(f"  Generating thumbnail for token {token.token_id}...")
        thumb_path = generate_thumbnail(token.ipfs_cid, token.token_id)
        if not thumb_path:
            self.log(f"  Failed to generate thumbnail for token {token.token_id}")
            return False

        # Upload thumbnail
        description = f"Thumbnail for [[Blue Railroad Token {token.token_id}]]"
        comment = f"Upload thumbnail for Blue Railroad token #{token.token_id}"

        success = self.wiki.upload_file(thumb_path, filename, description, comment)

        # Clean up temp file
        try:
            thumb_path.unlink()
        except Exception:
            pass

        if success:
            self.log(f"  Uploaded thumbnail: {filename}")
        else:
            self.log(f"  Failed to upload thumbnail: {filename}")

        return success

    def import_token(self, token: Token, generate_thumbnails: bool = True) -> SaveResult:
        """Import a single token to the wiki."""
        # Generate thumbnail if needed
        if generate_thumbnails:
            self.ensure_thumbnail(token)

        page_title = f"Blue Railroad Token {token.token_id}"
        content = generate_token_page_content(token)

        summary = f"{'Updated' if self.wiki.page_exists(page_title) else 'Imported'} Blue Railroad token #{token.token_id} from chain data"

        return self.wiki.save_page(page_title, content, summary)

    def generate_leaderboard(
        self,
        tokens: dict[str, Token],
        config,  # LeaderboardConfig
    ) -> SaveResult:
        """Generate a leaderboard page."""
        content = generate_leaderboard_content(tokens, config)

        summary = "Updated leaderboard from chain data"
        if config.filter_song_id:
            summary += f" (song_id={config.filter_song_id})"

        return self.wiki.save_page(config.page, content, summary)

    def run(self, generate_thumbnails: bool = True) -> ImportResults:
        """Run the full import process.

        Args:
            generate_thumbnails: If True, generate and upload thumbnails for token videos
        """
        results = ImportResults()

        # Load config
        config = self.load_config()

        # Load all tokens (aggregated from all sources)
        all_tokens = self.load_tokens(config)

        # Import individual token pages
        self.log("\nImporting token pages...")
        if generate_thumbnails:
            self.log("  (thumbnail generation enabled)")
        for key, token in all_tokens.items():
            result = self.import_token(token, generate_thumbnails=generate_thumbnails)
            results.token_pages.append(result)

            if result.action == 'created':
                self.log(f"  Created: Blue Railroad Token {token.token_id}")
            elif result.action == 'updated':
                fields = ', '.join(result.changed_fields) if result.changed_fields else 'unknown'
                self.log(f"  Updated: Blue Railroad Token {token.token_id} ({fields})")
            elif result.action == 'error':
                self.log(f"  ERROR: Blue Railroad Token {token.token_id}: {result.message}")

        self.log(f"\nToken page summary:")
        self.log(f"  Created: {len(results.token_pages_created)}")
        self.log(f"  Updated: {len(results.token_pages_updated)}")
        self.log(f"  Unchanged: {len(results.token_pages_unchanged)}")
        self.log(f"  Errors: {len(results.token_pages_error)}")

        # Generate leaderboards (using ALL aggregated tokens)
        self.log(f"\nGenerating leaderboards from {len(all_tokens)} total tokens...")
        for lb_config in config.leaderboards:
            result = self.generate_leaderboard(all_tokens, lb_config)
            results.leaderboard_pages.append(result)

            if result.action == 'created':
                self.log(f"  Created: {lb_config.page}")
            elif result.action == 'updated':
                fields = ', '.join(result.changed_fields) if result.changed_fields else 'content changed'
                self.log(f"  Updated: {lb_config.page} ({fields})")
            elif result.action == 'unchanged':
                self.log(f"  Unchanged: {lb_config.page}")
            elif result.action == 'error':
                self.log(f"  ERROR: {lb_config.page}: {result.message}")

        self.log(f"\nLeaderboard page summary:")
        self.log(f"  Created: {len(results.leaderboard_pages_created)}")
        self.log(f"  Updated: {len(results.leaderboard_pages_updated)}")
        self.log(f"  Unchanged: {len(results.leaderboard_pages_unchanged)}")
        self.log(f"  Errors: {len(results.leaderboard_pages_error)}")

        return results
