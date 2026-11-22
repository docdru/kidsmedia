"""Story packs on a WebDAV folder (SPEC §13.5a): list the pack zips in one folder and download
one of them, for the admin to import like an upload.

The folder, user and password come from the environment (`PACKS_WEBDAV_URL`,
`PACKS_WEBDAV_USER`, `PACKS_WEBDAV_PASSWORD`), never from the browser. Only that one folder
is read: no subfolders, no redirects, no other host.
"""

import ipaddress
import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import IO
from urllib.parse import quote, unquote, urlsplit
from xml.etree import ElementTree

import httpx

MAX_LISTING_BYTES = 2 * 1024 * 1024
MAX_NAME = 200
# A file name in the folder: no path parts, not hidden, ending in .zip.
NAME_RE = re.compile(r"^[^/\\\x00-\x1f.][^/\\\x00-\x1f]*\.zip$", re.IGNORECASE)
TIMEOUT = httpx.Timeout(60.0, connect=10.0)
DAV = "{DAV:}"
PROPFIND_BODY = (
    b'<?xml version="1.0" encoding="utf-8"?>'
    b'<d:propfind xmlns:d="DAV:"><d:prop>'
    b"<d:resourcetype/><d:getcontentlength/><d:getlastmodified/>"
    b"</d:prop></d:propfind>"
)


class WebDavError(Exception):
    """A problem with the WebDAV folder, as a reason code plus params (§5: codes, not
    sentences)."""

    def __init__(self, status_code: int, reason: str, **params: object) -> None:
        super().__init__(reason)
        self.status_code = status_code
        self.reason = reason
        self.params = params


@dataclass(frozen=True)
class PackFile:
    name: str
    size: int | None
    modified: datetime | None


def valid_name(name: str) -> bool:
    return len(name) <= MAX_NAME and NAME_RE.match(name) is not None


def check_url(url: str) -> str:
    """The folder URL with a trailing slash. HTTPS, or plain HTTP only to a loopback or
    private address (the password is sent with every request)."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("PACKS_WEBDAV_URL must be an http(s) URL")
    if parts.username or parts.password:
        raise ValueError("PACKS_WEBDAV_URL must not carry a login; use PACKS_WEBDAV_USER")
    if parts.query or parts.fragment:
        raise ValueError("PACKS_WEBDAV_URL must not have a query or fragment")
    if parts.scheme == "http" and not _is_local(parts.hostname):
        raise ValueError("PACKS_WEBDAV_URL must use https:// (http only for private addresses)")
    return url if url.endswith("/") else url + "/"


def _is_local(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


class PackFolder:
    """One WebDAV folder of pack zips."""

    def __init__(
        self,
        url: str,
        user: str,
        password: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.url = check_url(url)
        self._auth = httpx.BasicAuth(user, password) if user else None
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            auth=self._auth,
            timeout=TIMEOUT,
            follow_redirects=False,
            transport=self._transport,
        )

    async def list(self) -> list[PackFile]:
        """The .zip files directly in the folder, by name."""
        async with self._client() as client:
            try:
                async with client.stream(
                    "PROPFIND",
                    self.url,
                    content=PROPFIND_BODY,
                    headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
                ) as r:
                    _check_status(r)
                    raw = bytearray()
                    async for chunk in r.aiter_bytes():
                        raw += chunk
                        if len(raw) > MAX_LISTING_BYTES:
                            raise WebDavError(502, "webdav_bad_listing")
            except httpx.HTTPError as exc:
                raise WebDavError(502, "webdav_unreachable") from exc
        return parse_listing(bytes(raw), urlsplit(self.url).path)

    async def download(self, name: str, into: IO[bytes], limit: int) -> None:
        """One pack zip into `into`, at most `limit` bytes (413 `pack_too_large` beyond)."""
        if not valid_name(name):
            raise WebDavError(422, "invalid_name")
        async with self._client() as client:
            try:
                async with client.stream("GET", self.url + quote(name)) as r:
                    if r.status_code == 404:
                        raise WebDavError(404, "webdav_pack_not_found", name=name)
                    _check_status(r)
                    size = 0
                    async for chunk in r.aiter_bytes():
                        size += len(chunk)
                        if size > limit:
                            raise WebDavError(413, "pack_too_large", max_mb=limit // (1024 * 1024))
                        into.write(chunk)
            except httpx.HTTPError as exc:
                raise WebDavError(502, "webdav_unreachable") from exc
        into.seek(0)


def _check_status(r: httpx.Response) -> None:
    if r.status_code in (401, 403):
        raise WebDavError(502, "webdav_auth_failed", status=r.status_code)
    if r.status_code not in (200, 207):
        raise WebDavError(502, "webdav_error", status=r.status_code)


def parse_listing(raw: bytes, folder_path: str) -> list[PackFile]:
    """A PROPFIND multistatus: the zip files directly in the folder. Entries in other
    folders, subfolders and other files are skipped."""
    if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
        raise WebDavError(502, "webdav_bad_listing")
    try:
        root = ElementTree.fromstring(raw)  # noqa: S314 (no DOCTYPE or ENTITY, checked above)
    except ElementTree.ParseError as exc:
        raise WebDavError(502, "webdav_bad_listing") from exc
    folder = unquote(folder_path).rstrip("/") + "/"
    found: dict[str, PackFile] = {}
    for resp in root.iter(f"{DAV}response"):
        href = resp.findtext(f"{DAV}href")
        if not href:
            continue
        path = unquote(urlsplit(href.strip()).path)
        if not path.startswith(folder):
            continue
        name = path[len(folder) :]
        if not valid_name(name):
            continue  # the folder itself, a subfolder or another file
        prop = _ok_prop(resp)
        if prop is None or prop.find(f"{DAV}resourcetype/{DAV}collection") is not None:
            continue
        found[name] = PackFile(name, _size(prop), _modified(prop))
    return [found[n] for n in sorted(found, key=str.casefold)]


def _ok_prop(resp: ElementTree.Element) -> ElementTree.Element | None:
    """The `prop` of the propstat with a 200 status (the others list missing props)."""
    for propstat in resp.iter(f"{DAV}propstat"):
        status = propstat.findtext(f"{DAV}status") or ""
        if " 200 " in f"{status} ":
            return propstat.find(f"{DAV}prop")
    return None


def _size(prop: ElementTree.Element) -> int | None:
    text = (prop.findtext(f"{DAV}getcontentlength") or "").strip()
    return int(text) if text.isdigit() else None


def _modified(prop: ElementTree.Element) -> datetime | None:
    text = (prop.findtext(f"{DAV}getlastmodified") or "").strip()
    if not text:
        return None
    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
