# -*- coding: utf-8 -*-
"""Download and parse KML/KMZ files referenced by NetworkLinks.

A NetworkLink in KML points to an external KML/KMZ file (HTTP URL or relative
path). This module handles fetching them so their placemarks can be merged
into the main file's placemark list.
"""

import os
import tempfile
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path

from .kml_parser import KMLParser


def _normalize_url(url):
    """URL-encode unsafe characters in a URL while preserving structure.

    KMZ NetworkLinks often contain raw query strings with spaces, quotes,
    parentheses, commas — Python's urllib rejects these unless encoded.
    """
    parts = urllib.parse.urlsplit(url)
    # Re-encode path and query so spaces/quotes/etc become %20, %27, ...
    new_path = urllib.parse.quote(parts.path, safe="/%")
    new_query = urllib.parse.quote(parts.query, safe="=&%")
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, new_path, new_query, parts.fragment)
    )


# Magic bytes for ZIP files (KMZ is a renamed ZIP)
ZIP_MAGIC = b"PK\x03\x04"

# User-Agent — some servers reject default Python User-Agent
USER_AGENT = "KMZ-Tools/1.0 (ArcGIS Pro Python Toolbox)"

# Sensible defaults
DEFAULT_TIMEOUT = 30  # seconds
DEFAULT_MAX_DEPTH = 2  # how deep to follow nested NetworkLinks


def download_to_temp(url, timeout=DEFAULT_TIMEOUT, log=print):
    """Download a URL and write it to a temporary file.

    Returns the temp file path, or None if download failed.
    The caller is responsible for deleting the temp file when done.
    """
    try:
        encoded_url = _normalize_url(url)
        req = urllib.request.Request(encoded_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            content = response.read()
    except urllib.error.HTTPError as e:
        log(f"    [WARN] HTTP {e.code} {e.reason}: {url[:100]}")
        return None
    except urllib.error.URLError as e:
        log(f"    [WARN] URL error: {e.reason}")
        return None
    except Exception as e:
        log(f"    [WARN] Download failed: {e}")
        return None

    if not content:
        log("    [WARN] Empty response body")
        return None

    # Detect KMZ (ZIP) vs KML (XML) by magic bytes
    suffix = ".kmz" if content[:4] == ZIP_MAGIC else ".kml"

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix="netlink_")
    tmp.write(content)
    tmp.close()

    log(f"    Downloaded {len(content):,} bytes -> {Path(tmp.name).name}")
    return tmp.name


def follow_network_links(network_links, depth=0, max_depth=DEFAULT_MAX_DEPTH,
                         timeout=DEFAULT_TIMEOUT, log=print, _visited_urls=None):
    """Download and parse all NetworkLinks. Return list of placemarks.

    Each linked placemark's folder_path_segments is rewritten to:
        [<folders containing the NetworkLink in the parent file>,
         <NetworkLink's own name>,
         <original folder path inside the linked file>]

    This lets linked content slot into the parent file's folder hierarchy
    naturally — e.g. a NetworkLink "2026 BUOW" living in a "Bird Surveys"
    folder produces placemarks at "Bird Surveys/2026 BUOW/...".

    Recurses into nested NetworkLinks up to max_depth.

    network_links: list of dicts from KMLParser.get_network_links()
                   (must include 'folder_path_segments')
    """
    if _visited_urls is None:
        _visited_urls = set()

    if depth >= max_depth:
        log(f"    [MAX DEPTH {max_depth}] Not following further")
        return []

    indent = "  " * (depth + 1)
    all_placemarks = []

    for nl in network_links:
        link_name = nl.get("name") or "Unnamed"
        url = nl.get("href", "")
        link_folders = nl.get("folder_path_segments", []) or []

        if not url:
            log(f"{indent}[SKIP] '{link_name}' has no href")
            continue

        if not url.startswith(("http://", "https://")):
            log(f"{indent}[SKIP] '{link_name}' has non-HTTP href: {url[:80]}")
            continue

        if url in _visited_urls:
            log(f"{indent}[SKIP] '{link_name}' already visited (cycle prevention)")
            continue
        _visited_urls.add(url)

        path_label = "/".join(link_folders + [link_name]) if link_folders else link_name
        log(f"{indent}Following: {path_label}")
        tmp_path = download_to_temp(url, timeout=timeout, log=log)
        if not tmp_path:
            continue

        try:
            sub_parser = KMLParser(tmp_path)
            if not sub_parser.extract_and_parse():
                log(f"{indent}[WARN] Failed to parse '{link_name}'")
                continue

            sub_placemarks = sub_parser.get_placemarks()

            # Build the path prefix using the NetworkLink's own folder context
            # from the parent file, plus the link name itself
            prefix = link_folders + [link_name]
            for pm in sub_placemarks:
                original = pm.get("folder_path_segments", []) or []
                pm["folder_path_segments"] = prefix + original

            all_placemarks.extend(sub_placemarks)
            log(f"{indent}[OK] '{path_label}': {len(sub_placemarks)} placemarks")

            # Recurse into nested NetworkLinks; nested links inherit our prefix
            sub_links = sub_parser.get_network_links()
            if sub_links:
                # Push our prefix down so deeper links nest under us too
                for sl in sub_links:
                    sl["folder_path_segments"] = prefix + (sl.get("folder_path_segments") or [])
                log(f"{indent}'{link_name}' contains {len(sub_links)} nested NetworkLinks")
                nested = follow_network_links(
                    sub_links,
                    depth=depth + 1,
                    max_depth=max_depth,
                    timeout=timeout,
                    log=log,
                    _visited_urls=_visited_urls,
                )
                all_placemarks.extend(nested)

            sub_parser.cleanup()
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    return all_placemarks
