"""Wiki client wrapper for MediaWiki API operations."""

import re
from dataclasses import dataclass, field
from typing import Optional, Protocol
import mwclient


def _parse_template_params(wikitext: str) -> dict[str, str]:
    """Extract template parameters from wikitext for diffing."""
    params = {}
    # Match |key=value patterns (handles multiline values up to next |key= or }})
    pattern = r'\|([a-z_]+)\s*=\s*([^|]*?)(?=\|[a-z_]+=|\}\}|$)'
    for match in re.finditer(pattern, wikitext, re.DOTALL | re.IGNORECASE):
        key = match.group(1).strip()
        value = match.group(2).strip()
        params[key] = value
    return params


def _diff_wikitext(old: Optional[str], new: str) -> list[str]:
    """Return list of field names that changed between old and new wikitext."""
    if old is None:
        return []  # New page, no diff

    old_params = _parse_template_params(old)
    new_params = _parse_template_params(new)

    changed = []
    all_keys = set(old_params.keys()) | set(new_params.keys())
    for key in sorted(all_keys):
        old_val = old_params.get(key)
        new_val = new_params.get(key)
        if old_val != new_val:
            changed.append(key)

    return changed


class WikiClientProtocol(Protocol):
    """Protocol for wiki client operations (for testing)."""

    def get_page_content(self, title: str) -> Optional[str]:
        """Get the current content of a page, or None if it doesn't exist."""
        ...

    def save_page(self, title: str, content: str, summary: str) -> bool:
        """Save content to a page. Returns True if saved, False if unchanged."""
        ...

    def page_exists(self, title: str) -> bool:
        """Check if a page exists."""
        ...


@dataclass
class SaveResult:
    """Result of a page save operation."""
    page_title: str
    action: str  # 'created', 'updated', 'unchanged', 'error'
    message: str = ''
    changed_fields: list[str] = field(default_factory=list)


def _parse_site_url(site_url: str) -> tuple[str, str]:
    """Parse a site URL into (host, scheme) for mwclient."""
    if site_url.startswith('https://'):
        host = site_url[8:]
        scheme = 'https'
    elif site_url.startswith('http://'):
        host = site_url[7:]
        scheme = 'http'
    else:
        host = site_url
        scheme = 'https'
    return host.rstrip('/'), scheme


class MWClientWrapper:
    """Wrapper around mwclient for wiki operations."""

    def __init__(self, site_url: str, username: str, password: str):
        host, scheme = _parse_site_url(site_url)
        self.site = mwclient.Site(host, scheme=scheme, path='/')
        self.site.login(username, password)

    def get_page_content(self, title: str) -> Optional[str]:
        """Get the current content of a page, or None if it doesn't exist."""
        page = self.site.pages[title]
        if page.exists:
            return page.text()
        return None

    def save_page(self, title: str, content: str, summary: str) -> SaveResult:
        """Save content to a page. Checks if content changed first."""
        page = self.site.pages[title]
        existed = page.exists
        current_content = page.text() if existed else None

        # Skip if content unchanged
        if current_content == content:
            return SaveResult(title, 'unchanged', 'Content identical')

        changed_fields = _diff_wikitext(current_content, content)

        try:
            page.save(content, summary=summary)
            action = 'updated' if existed else 'created'
            return SaveResult(title, action, changed_fields=changed_fields)
        except Exception as e:
            return SaveResult(title, 'error', str(e))

    def page_exists(self, title: str) -> bool:
        """Check if a page exists."""
        return self.site.pages[title].exists


class DryRunClient:
    """Client for dry-run mode that reads from a real wiki but skips writes.

    Can also be used with a dict of existing pages for testing.
    """

    def __init__(
        self,
        existing_pages: Optional[dict[str, str]] = None,
        wiki_url: Optional[str] = None,
    ):
        self.existing_pages = existing_pages or {}
        self.saved_pages: list[tuple[str, str, str]] = []
        self._page_cache: dict[str, Optional[str]] = {}

        # Anonymous read-only connection to the wiki
        self._site = None
        if wiki_url:
            host, scheme = _parse_site_url(wiki_url)
            self._site = mwclient.Site(host, scheme=scheme, path='/')

    def _read_from_wiki(self, title: str) -> Optional[str]:
        """Read a page from the wiki (anonymous, cached)."""
        if not self._site:
            return None
        if title not in self._page_cache:
            page = self._site.pages[title]
            self._page_cache[title] = page.text() if page.exists else None
        return self._page_cache[title]

    def get_page_content(self, title: str) -> Optional[str]:
        if title in self.existing_pages:
            return self.existing_pages[title]
        return self._read_from_wiki(title)

    def save_page(self, title: str, content: str, summary: str) -> SaveResult:
        self.saved_pages.append((title, content, summary))

        current = self.get_page_content(title)
        existed = current is not None

        if current == content:
            return SaveResult(title, 'unchanged', 'Content identical (dry run)')

        changed_fields = _diff_wikitext(current, content)
        action = 'updated' if existed else 'created'
        return SaveResult(title, action, f'{action} (dry run)', changed_fields=changed_fields)

    def page_exists(self, title: str) -> bool:
        if title in self.existing_pages:
            return True
        if self._site:
            return self._site.pages[title].exists
        return False
