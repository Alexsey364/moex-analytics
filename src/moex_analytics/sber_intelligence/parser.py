"""Metadata parser; copyrighted full news text is never stored."""

import hashlib


def content_hash(title: str, url: str) -> str:
    return hashlib.sha256(f"{title}|{url}".encode()).hexdigest()


def metadata_only(title: str, url: str) -> dict:
    return {
        "title": title.strip(),
        "source_url": url,
        "content_hash": content_hash(title, url),
        "full_text_stored": False,
    }
