"""Data models for Blue Railroad tokens and configuration."""

from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

# Base58 alphabet for CIDv0 encoding
BASE58_ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'


def bytes_to_base58(data: bytes) -> str:
    """Encode bytes as base58 string."""
    # Count leading zeros
    leading_zeros = 0
    for byte in data:
        if byte == 0:
            leading_zeros += 1
        else:
            break

    # Convert to integer
    num = int.from_bytes(data, 'big')

    # Convert to base58
    result = []
    while num > 0:
        num, remainder = divmod(num, 58)
        result.append(BASE58_ALPHABET[remainder])

    # Add leading '1's for each leading zero byte
    return '1' * leading_zeros + ''.join(reversed(result))


def video_hash_to_cidv0(video_hash: str) -> Optional[str]:
    """
    Convert a bytes32 video hash to a CIDv0 (Qm...) string.

    The video_hash is the raw SHA256 digest stored as hex.
    To make a CIDv0:
    1. Prepend multihash header: 0x12 (SHA256) + 0x20 (32 bytes)
    2. Base58 encode the result
    """
    if not video_hash:
        return None

    # Remove 0x prefix if present
    hex_str = video_hash[2:] if video_hash.startswith('0x') else video_hash

    # Check for empty/zero hash
    if not hex_str or hex_str == '0' * 64:
        return None

    try:
        # Convert hex to bytes
        digest = bytes.fromhex(hex_str)

        # Prepend multihash header: 0x12 (SHA256) + 0x20 (32 bytes length)
        multihash = bytes([0x12, 0x20]) + digest

        # Base58 encode
        return bytes_to_base58(multihash)
    except (ValueError, TypeError):
        return None


@dataclass
class Token:
    """A Blue Railroad token from chain data."""
    token_id: str
    source_key: str  # e.g., 'blueRailroads' or 'blueRailroadV2s'
    owner: str
    owner_display: str
    song_id: Optional[str] = None

    # V1 fields
    date: Optional[int] = None
    uri: Optional[str] = None

    # V2 fields
    blockheight: Optional[int] = None
    video_hash: Optional[str] = None

    @property
    def is_v2(self) -> bool:
        return self.blockheight is not None

    @property
    def formatted_date(self) -> Optional[str]:
        """Convert date to YYYY-MM-DD format."""
        if self.date is None:
            return None

        date_str = str(self.date)

        # YYYYMMDD format (8 digits starting with 2)
        if len(date_str) == 8 and date_str[0] == '2':
            return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

        # Unix timestamp (10+ digits)
        if len(date_str) >= 10 and date_str.isdigit():
            try:
                dt = datetime.fromtimestamp(int(date_str))
                return dt.strftime('%Y-%m-%d')
            except (ValueError, OSError):
                pass

        return None

    @property
    def ipfs_cid(self) -> Optional[str]:
        """Extract IPFS CID from uri or video_hash."""
        if self.is_v2:
            # Convert bytes32 video hash to CIDv0 (Qm...)
            return video_hash_to_cidv0(self.video_hash)
        else:
            if self.uri and self.uri.startswith('ipfs://'):
                return self.uri[7:]
            return None


@dataclass
class Source:
    """A chain data source configuration."""
    name: str
    chain_data_key: str
    network_id: str = '10'
    contract: str = ''


@dataclass
class LeaderboardConfig:
    """Configuration for a leaderboard page."""
    page: str
    title: str = ''
    description: str = ''
    filter_song_id: Optional[str] = None
    filter_owner: Optional[str] = None
    sort: str = 'count'  # 'count', 'newest', 'oldest'

    def __post_init__(self):
        if not self.title:
            self.title = self.page


@dataclass
class OwnerStats:
    """Aggregated statistics for a token owner."""
    address: str
    display_name: str
    token_count: int = 0
    token_ids: list = field(default_factory=list)
    token_versions: dict = field(default_factory=dict)  # token_id -> is_v2
    newest_date: int = 0
    oldest_date: int = 0

    def add_token(self, token_id: str, date: Optional[int], is_v2: bool = False):
        self.token_count += 1
        self.token_ids.append(token_id)
        self.token_versions[token_id] = is_v2

        if date:
            if date > self.newest_date:
                self.newest_date = date
            if self.oldest_date == 0 or date < self.oldest_date:
                self.oldest_date = date


@dataclass
class BotConfig:
    """Complete bot configuration from wiki page."""
    sources: list[Source] = field(default_factory=list)
    leaderboards: list[LeaderboardConfig] = field(default_factory=list)
