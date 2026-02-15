"""Operations for Blue Railroad submission pages."""

import re
from typing import Optional, Tuple

import mwparserfromhell

from .models import Submission, Token
from .wiki_client import WikiClientProtocol, SaveResult


SUBMISSION_PAGE_PREFIX = 'Blue Railroad Submission/'
MAX_SUBMISSION_ID = 20  # Check submissions 1-20


def get_submission_page_title(submission_id: int) -> str:
    """Get the wiki page title for a submission."""
    return f"{SUBMISSION_PAGE_PREFIX}{submission_id}"


def update_submission_field(
    wikitext: str,
    field_name: str,
    field_value: str,
) -> Tuple[str, bool]:
    """Update or add a field in the Blue Railroad Submission template.

    Returns (updated_wikitext, was_changed).
    """
    # Pattern to match the template and capture its contents
    template_pattern = r'(\{\{Blue Railroad Submission\s*)(.*?)(\}\})'
    match = re.search(template_pattern, wikitext, re.DOTALL | re.IGNORECASE)

    if not match:
        raise ValueError("Could not find {{Blue Railroad Submission}} template in page")

    template_start = match.group(1)
    template_body = match.group(2)
    template_end = match.group(3)

    # Check if field already exists
    field_pattern = rf'\|{field_name}\s*=\s*[^\|]*'
    existing_match = re.search(field_pattern, template_body, re.IGNORECASE)

    if existing_match:
        # Update existing field
        old_value = existing_match.group(0)
        new_value = f"|{field_name}={field_value}"
        if old_value.strip() == new_value.strip():
            return wikitext, False  # No change needed
        new_body = template_body[:existing_match.start()] + new_value + template_body[existing_match.end():]
    else:
        # Add new field before the closing }}
        # Find a good place to insert (after last existing field)
        new_body = template_body.rstrip()
        if not new_body.endswith('\n'):
            new_body += '\n'
        new_body += f"|{field_name}={field_value}\n"

    new_wikitext = wikitext[:match.start()] + template_start + new_body + template_end + wikitext[match.end():]
    return new_wikitext, True


def update_submission_cid(
    wiki_client: WikiClientProtocol,
    submission_id: int,
    ipfs_cid: str,
    verbose: bool = False,
) -> SaveResult:
    """Update a submission page with the IPFS CID.

    Args:
        wiki_client: Wiki client for reading/writing pages
        submission_id: The submission number (e.g., 1 for "Blue Railroad Submission/1")
        ipfs_cid: The IPFS CID to add (e.g., "bafybeif...")
        verbose: Print progress messages

    Returns:
        SaveResult indicating what happened
    """
    page_title = get_submission_page_title(submission_id)

    if verbose:
        print(f"Updating {page_title} with IPFS CID: {ipfs_cid}")

    # Get current page content
    current_content = wiki_client.get_page_content(page_title)

    if current_content is None:
        return SaveResult(page_title, 'error', f'Page not found: {page_title}')

    try:
        updated_content, was_changed = update_submission_field(
            current_content,
            'ipfs_cid',
            ipfs_cid,
        )
    except ValueError as e:
        return SaveResult(page_title, 'error', str(e))

    if not was_changed:
        return SaveResult(page_title, 'unchanged', 'IPFS CID already set to this value')

    summary = f"Add IPFS CID: {ipfs_cid[:20]}..."
    return wiki_client.save_page(page_title, updated_content, summary)


def update_submission_token_id(
    wiki_client: WikiClientProtocol,
    submission_id: int,
    participant_wallet: str,
    token_id: int,
    verbose: bool = False,
) -> SaveResult:
    """Update a submission page to record a minted token for a participant.

    This updates the status field and could potentially update participant records.
    For now, it just updates the status to 'Minted'.

    Args:
        wiki_client: Wiki client for reading/writing pages
        submission_id: The submission number
        participant_wallet: The wallet address that received the token
        token_id: The minted token ID
        verbose: Print progress messages

    Returns:
        SaveResult indicating what happened
    """
    page_title = get_submission_page_title(submission_id)

    if verbose:
        print(f"Recording mint for {page_title}: Token #{token_id} to {participant_wallet}")

    current_content = wiki_client.get_page_content(page_title)

    if current_content is None:
        return SaveResult(page_title, 'error', f'Page not found: {page_title}')

    try:
        # Update status to Minted
        updated_content, was_changed = update_submission_field(
            current_content,
            'status',
            'Minted',
        )
    except ValueError as e:
        return SaveResult(page_title, 'error', str(e))

    if not was_changed:
        return SaveResult(page_title, 'unchanged', 'Status already set to Minted')

    summary = f"Mark as minted: Token #{token_id} to {participant_wallet[:10]}..."
    return wiki_client.save_page(page_title, updated_content, summary)


def _get_template_param(template, param_name: str) -> Optional[str]:
    """Get a parameter value from a mwparserfromhell template, or None if not present."""
    if template.has(param_name):
        return str(template.get(param_name).value).strip()
    return None


def parse_submission_content(wikitext: str, submission_id: int) -> Submission:
    """Parse submission page wikitext into a Submission object using mwparserfromhell."""
    parsed = mwparserfromhell.parse(wikitext)
    templates = parsed.filter_templates()

    # Find the main submission template
    exercise = ''
    video = None
    block_height = None
    status = 'Pending'
    ipfs_cid = None
    token_ids = []
    participants = []

    for template in templates:
        template_name = str(template.name).strip().lower()

        if template_name == 'blue railroad submission':
            exercise = _get_template_param(template, 'exercise') or ''
            video = _get_template_param(template, 'video')

            block_height_str = _get_template_param(template, 'block_height')
            if block_height_str and block_height_str.isdigit():
                block_height = int(block_height_str)

            status = _get_template_param(template, 'status') or 'Pending'
            ipfs_cid = _get_template_param(template, 'ipfs_cid')

            # Parse token_ids from comma-separated string
            token_ids_str = _get_template_param(template, 'token_ids')
            if token_ids_str:
                for tid in token_ids_str.split(','):
                    tid = tid.strip()
                    if tid.isdigit():
                        token_ids.append(int(tid))

        elif template_name == 'blue railroad participant':
            wallet = _get_template_param(template, 'wallet')
            if wallet and wallet not in participants:
                participants.append(wallet)

    return Submission(
        id=submission_id,
        exercise=exercise,
        video=video,
        block_height=block_height,
        status=status,
        ipfs_cid=ipfs_cid,
        token_ids=token_ids,
        participants=participants,
    )


def fetch_submission(
    wiki_client: WikiClientProtocol,
    submission_id: int,
) -> Optional[Submission]:
    """Fetch a single submission from the wiki."""
    page_title = get_submission_page_title(submission_id)
    content = wiki_client.get_page_content(page_title)

    if content is None:
        return None

    return parse_submission_content(content, submission_id)


def fetch_all_submissions(
    wiki_client: WikiClientProtocol,
    max_id: int = MAX_SUBMISSION_ID,
    verbose: bool = False,
) -> list[Submission]:
    """Fetch all submissions from the wiki (pages 1 through max_id)."""
    submissions = []

    for i in range(1, max_id + 1):
        submission = fetch_submission(wiki_client, i)
        if submission:
            submissions.append(submission)
            if verbose:
                print(f"  Loaded submission #{i}: {submission.exercise}")

    return submissions


def update_submission_token_ids(
    wiki_client: WikiClientProtocol,
    submission_id: int,
    token_ids: list[int],
    verbose: bool = False,
) -> SaveResult:
    """Update a submission page with the list of minted token IDs.

    Also sets status to 'Minted' if there are any token IDs.

    Args:
        wiki_client: Wiki client for reading/writing pages
        submission_id: The submission number
        token_ids: List of token IDs minted from this submission
        verbose: Print progress messages

    Returns:
        SaveResult indicating what happened
    """
    page_title = get_submission_page_title(submission_id)

    if verbose:
        print(f"Updating {page_title} with token IDs: {token_ids}")

    current_content = wiki_client.get_page_content(page_title)

    if current_content is None:
        return SaveResult(page_title, 'error', f'Page not found: {page_title}')

    # Sort and format token IDs
    sorted_ids = sorted(set(token_ids))
    token_ids_str = ','.join(str(tid) for tid in sorted_ids)

    try:
        # Update token_ids field
        updated_content, changed1 = update_submission_field(
            current_content,
            'token_ids',
            token_ids_str,
        )

        # Also update status to Minted if we have tokens
        changed2 = False
        if token_ids:
            updated_content, changed2 = update_submission_field(
                updated_content,
                'status',
                'Minted',
            )

    except ValueError as e:
        return SaveResult(page_title, 'error', str(e))

    if not changed1 and not changed2:
        return SaveResult(page_title, 'unchanged', 'Token IDs and status already set')

    summary = f"Update minted tokens: {token_ids_str}"
    return wiki_client.save_page(page_title, updated_content, summary)


def match_tokens_to_submissions(
    tokens: dict[str, Token],
    submissions: list[Submission],
) -> dict[int, list[int]]:
    """Match tokens to submissions based on IPFS CID.

    Returns a dict mapping submission_id -> list of token_ids.
    Multiple tokens can match the same submission (one per participant).
    """
    # Build a lookup from CID to submission
    cid_to_submission: dict[str, Submission] = {}
    for sub in submissions:
        if sub.ipfs_cid:
            cid_to_submission[sub.ipfs_cid] = sub

    # Match tokens to submissions
    submission_tokens: dict[int, list[int]] = {}

    for token_id_str, token in tokens.items():
        token_cid = token.ipfs_cid
        if not token_cid:
            continue

        # Check if this CID matches a submission
        if token_cid in cid_to_submission:
            sub = cid_to_submission[token_cid]
            if sub.id not in submission_tokens:
                submission_tokens[sub.id] = []
            submission_tokens[sub.id].append(int(token_id_str))

    return submission_tokens


def get_submission_id_for_token(
    token: Token,
    submissions: list[Submission],
) -> Optional[int]:
    """Get the submission ID that matches a token's CID.

    Returns None if no matching submission found.
    """
    token_cid = token.ipfs_cid
    if not token_cid:
        return None

    for sub in submissions:
        if sub.ipfs_cid == token_cid:
            return sub.id

    return None


def find_tokens_for_submission(
    wiki_client: WikiClientProtocol,
    submission: Submission,
) -> list[tuple[str, str, str]]:
    """Find all tokens for a submission using Semantic MediaWiki query.

    Queries the wiki's SMW API for token pages with matching IPFS CID.

    Returns list of (token_id, owner_address, owner_display) tuples.
    """
    if not submission.ipfs_cid:
        return []

    tokens = wiki_client.query_tokens_by_cid(submission.ipfs_cid)
    return [(t.token_id, t.owner_address, t.owner_display) for t in tokens]


def match_submissions_via_smw(
    wiki_client: WikiClientProtocol,
    submissions: list[Submission],
    verbose: bool = False,
) -> dict[int, list[int]]:
    """Match tokens to submissions using Semantic MediaWiki queries.

    For each submission with an IPFS CID, queries the wiki for token pages
    that have the same CID. This uses the wiki's indexed semantic data
    rather than loading all tokens into Python.

    Returns a dict mapping submission_id -> list of token_ids.
    """
    result: dict[int, list[int]] = {}

    for sub in submissions:
        if not sub.ipfs_cid:
            continue

        tokens = wiki_client.query_tokens_by_cid(sub.ipfs_cid)
        if tokens:
            token_ids = [int(t.token_id) for t in tokens]
            result[sub.id] = sorted(token_ids)
            if verbose:
                print(f"  Submission {sub.id}: found {len(tokens)} tokens via SMW")

    return result


def match_tokens_by_blockheight_and_participant(
    tokens: dict[str, Token],
    submissions: list[Submission],
    ens_mapping: Optional[dict[str, str]] = None,
    verbose: bool = False,
) -> dict[int, list[int]]:
    """Match tokens to submissions using blockheight + participant wallet.

    This is useful when submissions don't have IPFS CIDs set yet.
    A token matches a submission if:
    - The token's blockheight equals the submission's block_height
    - The token's owner address matches a submission participant

    For participants stored as ENS names (e.g., 'justinholmes.eth'), the
    ens_mapping is used to resolve them to addresses for comparison.

    Args:
        tokens: Dict of token_id -> Token
        submissions: List of submissions to match against
        ens_mapping: Optional dict mapping ENS names to addresses
        verbose: Print progress messages

    Returns a dict mapping submission_id -> list of token_ids.
    """
    result: dict[int, list[int]] = {}
    ens_mapping = ens_mapping or {}

    # Build lookup: (blockheight, address) -> submission
    # Resolve ENS names to addresses using the mapping
    blockheight_address_map: dict[tuple[int, str], Submission] = {}
    for sub in submissions:
        if sub.block_height is None:
            continue
        for wallet in sub.participants:
            wallet_lower = wallet.lower()

            # Check if this looks like an ENS name
            if wallet_lower.endswith('.eth'):
                # Try to resolve ENS to address
                address = ens_mapping.get(wallet_lower)
                if address:
                    key = (sub.block_height, address.lower())
                    blockheight_address_map[key] = sub
                    if verbose:
                        print(f"  Resolved {wallet} -> {address}")
                elif verbose:
                    print(f"  Could not resolve ENS: {wallet}")
            else:
                # Assume it's already an address
                key = (sub.block_height, wallet_lower)
                blockheight_address_map[key] = sub

    # Match tokens
    for token_id_str, token in tokens.items():
        if token.blockheight is None:
            continue

        # Normalize owner to lowercase
        key = (token.blockheight, token.owner.lower())

        if key in blockheight_address_map:
            sub = blockheight_address_map[key]
            if sub.id not in result:
                result[sub.id] = []
            result[sub.id].append(int(token_id_str))
            if verbose:
                print(f"  Token {token_id_str} -> Submission {sub.id} (blockheight {token.blockheight})")

    # Sort token IDs for each submission
    for sub_id in result:
        result[sub_id] = sorted(result[sub_id])

    return result


def sync_submission_cids_from_tokens(
    wiki_client: WikiClientProtocol,
    tokens: dict[str, Token],
    submissions: list[Submission],
    ens_mapping: Optional[dict[str, str]] = None,
    verbose: bool = False,
) -> list[SaveResult]:
    """Sync IPFS CIDs from matched tokens to submissions.

    Uses blockheight + participant matching to find which tokens belong to
    which submissions, then updates the submission's ipfs_cid field.

    Args:
        wiki_client: Wiki client for saving pages
        tokens: Dict of token_id -> Token
        submissions: List of submissions to sync
        ens_mapping: Optional dict mapping ENS names to addresses
        verbose: Print progress messages

    Returns list of SaveResults for updated submissions.
    """
    results = []
    matches = match_tokens_by_blockheight_and_participant(
        tokens, submissions, ens_mapping=ens_mapping, verbose=verbose
    )

    for sub_id, token_ids in matches.items():
        # Find the submission
        sub = next((s for s in submissions if s.id == sub_id), None)
        if not sub:
            continue

        # Get CID from first matched token (all tokens for same submission should have same CID)
        first_token_id = str(token_ids[0])
        token = tokens.get(first_token_id)
        if not token or not token.ipfs_cid:
            continue

        # Update submission CID if not already set
        if sub.ipfs_cid != token.ipfs_cid:
            if verbose:
                print(f"  Setting CID for submission {sub_id}: {token.ipfs_cid}")
            result = update_submission_cid(wiki_client, sub_id, token.ipfs_cid, verbose=False)
            results.append(result)

    return results
