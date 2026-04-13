"""Main import orchestration."""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

from .models import BotConfig, Token, Submission
from .chain_data import load_chain_data, aggregate_tokens_from_sources, load_ens_mapping
from .config_parser import parse_config_from_wikitext, get_default_config
from .leaderboard import generate_leaderboard_content
from .token_page import generate_token_page_content, update_existing_page
from .wiki_client import WikiClientProtocol, SaveResult
from .thumbnail import generate_thumbnail, get_thumbnail_filename
from .submission import (
    fetch_all_submissions,
    match_tokens_to_submissions,
    match_tokens_by_blockheight_and_participant,
    sync_submission_cids_from_tokens,
    get_submission_id_for_token,
    update_submission_token_ids,
)
from .release_page import ensure_release_for_token, ensure_release_for_submission
from .release_draft import process_release_drafts


CONFIG_PAGE = 'PickiPedia:BlueRailroadConfig'


@dataclass
class ImportResults:
    """Results from an import run."""
    token_pages: list[SaveResult] = field(default_factory=list)
    leaderboard_pages: list[SaveResult] = field(default_factory=list)
    submission_pages: list[SaveResult] = field(default_factory=list)
    release_pages: list[SaveResult] = field(default_factory=list)
    draft_promotions: list[SaveResult] = field(default_factory=list)

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
    def release_pages_created(self) -> list[SaveResult]:
        return self._by_action(self.release_pages, 'created')

    @property
    def release_pages_updated(self) -> list[SaveResult]:
        return self._by_action(self.release_pages, 'updated')

    @property
    def release_pages_unchanged(self) -> list[SaveResult]:
        return self._by_action(self.release_pages, 'unchanged')

    @property
    def release_pages_error(self) -> list[SaveResult]:
        return self._by_action(self.release_pages, 'error')

    @property
    def submission_pages_updated(self) -> list[SaveResult]:
        return self._by_action(self.submission_pages, 'updated')

    @property
    def submission_pages_unchanged(self) -> list[SaveResult]:
        return self._by_action(self.submission_pages, 'unchanged')

    @property
    def submission_pages_error(self) -> list[SaveResult]:
        return self._by_action(self.submission_pages, 'error')

    @property
    def draft_promotions_created(self) -> list[SaveResult]:
        return self._by_action(self.draft_promotions, 'created')

    @property
    def draft_promotions_unchanged(self) -> list[SaveResult]:
        return self._by_action(self.draft_promotions, 'unchanged')

    @property
    def draft_promotions_error(self) -> list[SaveResult]:
        return self._by_action(self.draft_promotions, 'error')

    @property
    def errors(self) -> list[str]:
        return [
            f"{r.page_title}: {r.message}"
            for r in self.token_pages + self.leaderboard_pages + self.submission_pages + self.release_pages + self.draft_promotions
            if r.action == 'error'
        ]


class BlueRailroadImporter:
    """Main importer class that orchestrates the import process."""

    def __init__(
        self,
        wiki_client: WikiClientProtocol,
        chain_data_path: Path,
        config_page: str = CONFIG_PAGE,
    ):
        self.wiki = wiki_client
        self.chain_data_path = chain_data_path
        self.config_page = config_page

    def load_config(self) -> BotConfig:
        """Load configuration from wiki page or use defaults."""
        logger.info("Loading config from: %s", self.config_page)

        wiki_content = self.wiki.get_page_content(self.config_page)
        if wiki_content:
            config = parse_config_from_wikitext(wiki_content)
            if config:
                logger.info("  Found %s source(s)", len(config.sources))
                logger.info("  Found %s leaderboard(s)", len(config.leaderboards))
                return config

        logger.info("  Using default configuration")
        return get_default_config()

    def load_chain_data(self) -> dict:
        """Load raw chain data from file."""
        logger.info("Loading chain data from: %s", self.chain_data_path)
        return load_chain_data(self.chain_data_path)

    def load_tokens(self, chain_data: dict, config: BotConfig) -> dict[str, Token]:
        """Aggregate all tokens from chain data."""
        tokens = aggregate_tokens_from_sources(chain_data, config.sources)
        logger.info("  Loaded %s total tokens from %s source(s)", len(tokens), len(config.sources))
        return tokens

    def get_ens_mapping(self, chain_data: dict) -> dict[str, str]:
        """Extract ENS name -> address mapping from chain data."""
        ens_mapping = load_ens_mapping(chain_data)
        logger.info("  Loaded %s ENS -> address mappings", len(ens_mapping))
        return ens_mapping

    def load_submissions(self) -> list[Submission]:
        """Load all submissions from the wiki."""
        logger.info("Loading submissions from wiki...")
        submissions = fetch_all_submissions(self.wiki)
        logger.info("  Loaded %s submission(s)", len(submissions))
        return submissions

    def ensure_thumbnail(self, token: Token) -> bool:
        """Ensure a thumbnail exists for the token's video.

        Returns True if thumbnail exists or was successfully uploaded,
        False if thumbnail generation/upload failed or no video exists.

        Thumbnails are named by IPFS CID, so multiple tokens sharing
        the same video will share the same thumbnail file.
        """
        if not token.ipfs_cid:
            logger.info("  No IPFS CID for token %s, skipping thumbnail", token.token_id)
            return False

        filename = get_thumbnail_filename(token.ipfs_cid)

        # Check if thumbnail already exists (may have been uploaded for another token)
        if self.wiki.file_exists(filename):
            logger.info("  Thumbnail already exists: %s", filename)
            return True

        # Generate thumbnail
        logger.info("  Generating thumbnail for video %s...", token.ipfs_cid)
        thumb_path = generate_thumbnail(token.ipfs_cid)
        if not thumb_path:
            logger.info("  Failed to generate thumbnail for video %s", token.ipfs_cid)
            return False

        # Upload thumbnail
        description = f"Thumbnail for Blue Railroad video (IPFS: {token.ipfs_cid})"
        comment = f"Upload thumbnail for Blue Railroad video {token.ipfs_cid}"

        success = self.wiki.upload_file(thumb_path, filename, description, comment)

        # Clean up temp file
        try:
            thumb_path.unlink()
        except Exception:
            pass

        if success:
            logger.info("  Uploaded thumbnail: %s", filename)
        else:
            logger.info("  Failed to upload thumbnail: %s", filename)

        return success

    def import_token(
        self,
        token: Token,
        generate_thumbnails: bool = True,
        submission_id: Optional[int] = None,
    ) -> SaveResult:
        """Import a single token to the wiki."""
        # Generate thumbnail if needed
        if generate_thumbnails:
            self.ensure_thumbnail(token)

        page_title = f"Blue Railroad Token {token.token_id}"
        existing_content = self.wiki.get_page_content(page_title)

        if existing_content is None:
            # New page - create with full template
            content = generate_token_page_content(token, submission_id)
            summary = f"Imported Blue Railroad token #{token.token_id} from chain data"
            return self.wiki.save_page(page_title, content, summary)

        # Existing page - only update template if owner, maybelle status, or submission changed
        result = update_existing_page(existing_content, token, submission_id)

        if result is None:
            # No update needed
            return SaveResult(page_title, 'unchanged', 'No changes')

        updated_content, reason = result
        summary = f"Updated Blue Railroad token #{token.token_id}: {reason}"
        return self.wiki.save_page(page_title, updated_content, summary)

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

        # Load chain data and extract tokens + ENS mapping
        chain_data = self.load_chain_data()
        all_tokens = self.load_tokens(chain_data, config)
        ens_mapping = self.get_ens_mapping(chain_data)

        # Load all submissions
        all_submissions = self.load_submissions()

        # First, sync CIDs from tokens to submissions using blockheight+participant matching
        # This populates ipfs_cid on submissions that don't have it yet
        cid_sync_results = sync_submission_cids_from_tokens(
            self.wiki, all_tokens, all_submissions,
            ens_mapping=ens_mapping,
        )
        for result in cid_sync_results:
            results.submission_pages.append(result)
            if result.action in ('created', 'updated'):
                logger.info("  Synced CID to submission: %s", result.page_title)

        # Reload submissions if any CIDs were synced (to get updated data)
        if cid_sync_results:
            all_submissions = self.load_submissions()

        # Match tokens to submissions - try CID matching first, fall back to blockheight+participant
        token_to_submission = match_tokens_to_submissions(all_tokens, all_submissions)

        # If CID matching found nothing, try blockheight+participant matching
        if not token_to_submission:
            token_to_submission = match_tokens_by_blockheight_and_participant(
                all_tokens, all_submissions,
                ens_mapping=ens_mapping,
            )

        # Build reverse lookup: token_id -> submission_id
        token_submission_map: dict[str, int] = {}
        for sub_id, token_ids in token_to_submission.items():
            for tid in token_ids:
                token_submission_map[str(tid)] = sub_id

        logger.info("  Matched %s token(s) to %s submission(s)", len(token_submission_map), len(token_to_submission))

        # Import individual token pages
        logger.info("\nImporting token pages...")
        if generate_thumbnails:
            logger.info("  (thumbnail generation enabled)")
        for key, token in all_tokens.items():
            submission_id = token_submission_map.get(key)
            result = self.import_token(
                token,
                generate_thumbnails=generate_thumbnails,
                submission_id=submission_id,
            )
            results.token_pages.append(result)

            if result.action == 'created':
                logger.info("  Created: Blue Railroad Token %s", token.token_id)
            elif result.action == 'updated':
                fields = ', '.join(result.changed_fields) if result.changed_fields else 'unknown'
                logger.info("  Updated: Blue Railroad Token %s (%s)", token.token_id, fields)
            elif result.action == 'error':
                logger.info("  ERROR: Blue Railroad Token %s: %s", token.token_id, result.message)

        logger.info("\nToken page summary:")
        logger.info("  Created: %s", len(results.token_pages_created))
        logger.info("  Updated: %s", len(results.token_pages_updated))
        logger.info("  Unchanged: %s", len(results.token_pages_unchanged))
        logger.info("  Errors: %s", len(results.token_pages_error))

        # Update submission pages with token IDs
        logger.info("\nUpdating submission pages with token links...")
        for sub_id, token_ids in token_to_submission.items():
            result = update_submission_token_ids(
                self.wiki,
                sub_id,
                token_ids,
            )
            results.submission_pages.append(result)

            if result.action == 'updated':
                logger.info("  Updated: Blue Railroad Submission/%s (tokens: %s)", sub_id, token_ids)
            elif result.action == 'error':
                logger.info("  ERROR: Blue Railroad Submission/%s: %s", sub_id, result.message)

        logger.info("\nSubmission page summary:")
        logger.info("  Updated: %s", len(results.submission_pages_updated))
        logger.info("  Unchanged: %s", len(results.submission_pages_unchanged))
        logger.info("  Errors: %s", len(results.submission_pages_error))

        # Ensure Release pages exist for tokens with IPFS CIDs.
        # Group tokens by CID first so we can list all token IDs in the title.
        logger.info("\nEnsuring Release pages for token videos...")
        cid_tokens: dict[str, list] = {}
        cid_submission: dict[str, int | None] = {}
        for key, token in all_tokens.items():
            if not token.ipfs_cid:
                continue
            cid_tokens.setdefault(token.ipfs_cid, []).append(token)
            if key in token_submission_map and token.ipfs_cid not in cid_submission:
                cid_submission[token.ipfs_cid] = token_submission_map[key]

        for cid, tokens_for_cid in cid_tokens.items():
            # Use the first token for song_id, but collect all token IDs
            first_token = tokens_for_cid[0]
            all_token_ids = sorted(int(t.token_id) for t in tokens_for_cid)
            submission_id = cid_submission.get(cid)
            result = ensure_release_for_token(
                self.wiki, first_token,
                submission_id=submission_id,
                all_token_ids=all_token_ids,
            )
            if result:
                results.release_pages.append(result)
                if result.action == 'created':
                    logger.info("  Created: %s", result.page_title)
                elif result.action == 'updated':
                    logger.info("  Enriched: %s", result.page_title)
                elif result.action == 'error':
                    logger.info("  ERROR: %s: %s", result.page_title, result.message)

        # Ensure Release pages for submissions with CIDs not already covered by tokens
        for sub in all_submissions:
            if not sub.has_cid or sub.ipfs_cid in seen_cids:
                continue
            seen_cids.add(sub.ipfs_cid)
            result = ensure_release_for_submission(
                self.wiki, sub,
            )
            if result:
                results.release_pages.append(result)
                if result.action == 'created':
                    logger.info("  Created: %s", result.page_title)
                elif result.action == 'updated':
                    logger.info("  Enriched: %s", result.page_title)
                elif result.action == 'error':
                    logger.info("  ERROR: %s: %s", result.page_title, result.message)

        logger.info("\nRelease page summary:")
        logger.info("  Created: %s", len(results.release_pages_created))
        logger.info("  Updated: %s", len(results.release_pages_updated))
        logger.info("  Unchanged: %s", len(results.release_pages_unchanged))
        logger.info("  Errors: %s", len(results.release_pages_error))

        # Promote completed ReleaseDrafts to Release pages
        logger.info("\nProcessing ReleaseDraft pages...")
        draft_results = process_release_drafts(self.wiki)
        results.draft_promotions.extend(draft_results)

        logger.info("\nDraft promotion summary:")
        logger.info("  Created: %s", len(results.draft_promotions_created))
        logger.info("  Already exist: %s", len(results.draft_promotions_unchanged))
        logger.info("  Errors: %s", len(results.draft_promotions_error))

        # Generate leaderboards (using ALL aggregated tokens)
        logger.info("\nGenerating leaderboards from %s total tokens...", len(all_tokens))
        for lb_config in config.leaderboards:
            result = self.generate_leaderboard(all_tokens, lb_config)
            results.leaderboard_pages.append(result)

            if result.action == 'created':
                logger.info("  Created: %s", lb_config.page)
            elif result.action == 'updated':
                fields = ', '.join(result.changed_fields) if result.changed_fields else 'content changed'
                logger.info("  Updated: %s (%s)", lb_config.page, fields)
            elif result.action == 'unchanged':
                logger.info("  Unchanged: %s", lb_config.page)
            elif result.action == 'error':
                logger.info("  ERROR: %s: %s", lb_config.page, result.message)

        logger.info("\nLeaderboard page summary:")
        logger.info("  Created: %s", len(results.leaderboard_pages_created))
        logger.info("  Updated: %s", len(results.leaderboard_pages_updated))
        logger.info("  Unchanged: %s", len(results.leaderboard_pages_unchanged))
        logger.info("  Errors: %s", len(results.leaderboard_pages_error))

        return results
