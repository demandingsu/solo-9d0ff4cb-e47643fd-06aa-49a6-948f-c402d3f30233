"""技术标匿名核查：抓取目录分页与详情页，下载 ZIP 附件，检查 DOCX 身份痕迹。

核查流程：
1. 从技术标目录网址出发，抓取同源分页（nav.pagination a）与投标详情链接
   （ul.bid-list a）；
2. 逐个读取详情页，收集 ZIP 附件链接（ul.attachments a 中 .zip 链接）；
3. 下载每份 ZIP，逐个解压其中的条目（每份 ZIP 累计解压超过 20 MiB 即中止
   该附件），对 DOCX 检查：文件名、docProps/core.xml 的 creator 与
   lastModifiedBy 是否包含任一投标企业名称。

与公告抓取不同：单点失败不中断整体核查——页面读取失败、附件损坏、解压超限
都记为“问题网址”，该标段结论为“无法核定”，但已发现的命中仍然展示。
只有全部附件查完且无任何命中时，结论才是“可送审”。
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from urllib.parse import urldefrag, urljoin, urlparse
from xml.etree import ElementTree as ET

import httpx
from bs4 import BeautifulSoup

from scraper import same_origin

MAX_LIST_PAGES = 50
MAX_DETAIL_PAGES = 500
MAX_UNPACK_BYTES = 20 * 1024 * 1024  # 每份 ZIP 累计解压上限 20 MiB
MAX_UNPACK_MIB = MAX_UNPACK_BYTES // (1024 * 1024)
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024  # 单份 ZIP 下载上限（防御性）
USER_AGENT = "bid-identity-checker/1.0"

CORE_PROPS_PATH = "docProps/core.xml"
CP_NS = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC_NS = "http://purl.org/dc/elements/1.1/"

FIELD_FILENAME = "文件名"
FIELD_CREATOR = "creator"
FIELD_LAST_MODIFIED_BY = "lastModifiedBy"


@dataclass
class Hit:
    """一处身份痕迹命中。"""

    company: str  # 命中的企业名称
    file: str  # ZIP 内 DOCX 路径
    field: str  # 文件名 / creator / lastModifiedBy
    value: str  # 字段实际内容
    zip_url: str  # 附件网址
    page_url: str  # 附件所在详情页网址


@dataclass
class Problem:
    """一处无法核定的问题网址。"""

    url: str
    reason: str
    page_url: str | None = None  # 附件问题对应的详情页（页面问题为 None）


@dataclass
class CheckResult:
    hits: list[Hit] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    zip_count: int = 0
    docx_count: int = 0
    list_pages: list[str] = field(default_factory=list)
    detail_pages: list[str] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if self.problems:
            return "无法核定"
        if self.hits:
            return "发现身份痕迹"
        return "可送审"


def parse_companies(text: str) -> list[str]:
    """解析企业名称输入：按行或逗号/顿号/分号分隔，去空白、去重（保持顺序）。"""
    parts = re.split(r"[\n\r,，、;；]+", text)
    seen: set[str] = set()
    companies: list[str] = []
    for part in (p.strip() for p in parts):
        if part and part not in seen:
            seen.add(part)
            companies.append(part)
    return companies


def _normalize(href: str, base: str) -> str:
    url, _frag = urldefrag(urljoin(base, href))
    return url


def _is_zip(url: str) -> bool:
    return urlparse(url).path.lower().endswith(".zip")


def _match(companies: list[str], field_name: str, value: str, entry_name: str, zip_url: str, page_url: str) -> list[Hit]:
    return [
        Hit(company=c, file=entry_name, field=field_name, value=value, zip_url=zip_url, page_url=page_url)
        for c in companies
        if c in value
    ]


def _read_entry(zf: zipfile.ZipFile, info: zipfile.ZipInfo, total: int) -> tuple[bytes | None, int]:
    """解压一个条目并累计字节数。返回 (内容, 新累计)；超过上限返回 (None, total)。

    分块读取，避免解压炸弹一次性占满内存。
    """
    chunks: list[bytes] = []
    with zf.open(info, "r") as fh:
        while True:
            chunk = fh.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UNPACK_BYTES:
                return None, total
            chunks.append(chunk)
    return b"".join(chunks), total


def _read_core_props(docx_bytes: bytes) -> tuple[str, str] | None:
    """读取 DOCX 的 (creator, lastModifiedBy)。

    DOCX 损坏或 core.xml 无法解析返回 None；缺少 core.xml 视为属性为空。
    """
    try:
        with zipfile.ZipFile(io.BytesIO(docx_bytes)) as dz:
            try:
                raw = dz.read(CORE_PROPS_PATH)
            except KeyError:
                return "", ""
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError):
        return None
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None
    creator = root.findtext(f"{{{DC_NS}}}creator") or ""
    last_modified_by = root.findtext(f"{{{CP_NS}}}lastModifiedBy") or ""
    return creator.strip(), last_modified_by.strip()


def check_zip(data: bytes, companies: list[str], zip_url: str, page_url: str = "") -> tuple[list[Hit], list[Problem], int]:
    """检查单份 ZIP 内容。返回 (命中列表, 问题列表, 已检查 DOCX 数)。

    每份 ZIP 累计解压超过 20 MiB、ZIP 或其中 DOCX 损坏时记为问题（无法核定），
    但在中止前已发现的命中仍然保留。
    """
    hits: list[Hit] = []
    problems: list[Problem] = []
    docx_count = 0
    page = page_url or None

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError) as exc:
        problems.append(Problem(zip_url, f"附件损坏：不是有效的 ZIP 文件（{exc}）", page))
        return hits, problems, docx_count

    total = 0
    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            try:
                entry, total = _read_entry(zf, info, total)
            except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError) as exc:
                problems.append(Problem(zip_url, f"附件损坏：无法解压 {info.filename}（{exc}）", page))
                return hits, problems, docx_count
            if entry is None:
                problems.append(
                    Problem(
                        zip_url,
                        f"累计解压超过 {MAX_UNPACK_MIB} MiB（解压到 {info.filename} 时超出上限），该附件未查完",
                        page,
                    )
                )
                return hits, problems, docx_count

            entry_name = info.filename
            basename = entry_name.replace("\\", "/").rsplit("/", 1)[-1]
            if not basename.lower().endswith(".docx"):
                continue
            docx_count += 1

            hits.extend(_match(companies, FIELD_FILENAME, basename, entry_name, zip_url, page_url or ""))

            props = _read_core_props(entry)
            if props is None:
                problems.append(Problem(zip_url, f"附件损坏：DOCX 无法读取（{entry_name}）", page))
                return hits, problems, docx_count
            creator, last_modified_by = props
            if creator:
                hits.extend(_match(companies, FIELD_CREATOR, creator, entry_name, zip_url, page_url or ""))
            if last_modified_by:
                hits.extend(_match(companies, FIELD_LAST_MODIFIED_BY, last_modified_by, entry_name, zip_url, page_url or ""))

    return hits, problems, docx_count


def _fetch_soup(client: httpx.Client, url: str, result: CheckResult) -> BeautifulSoup | None:
    """读取一个页面；失败记入问题网址并返回 None（不中断整体核查）。"""
    try:
        resp = client.get(url)
    except httpx.HTTPError as exc:
        result.problems.append(Problem(url, f"页面读取失败：{exc}"))
        return None
    if resp.status_code >= 400:
        result.problems.append(Problem(url, f"页面读取失败：HTTP {resp.status_code}"))
        return None
    return BeautifulSoup(resp.text, "html.parser")


def _download(client: httpx.Client, url: str, page_url: str, result: CheckResult) -> bytes | None:
    """下载 ZIP 附件（限制大小）；失败记入问题网址并返回 None。"""
    try:
        with client.stream("GET", url) as resp:
            if resp.status_code >= 400:
                result.problems.append(Problem(url, f"附件下载失败：HTTP {resp.status_code}", page_url or None))
                return None
            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_bytes(65536):
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    result.problems.append(
                        Problem(url, f"附件下载超过 {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MiB 上限，未检查", page_url or None)
                    )
                    return None
                chunks.append(chunk)
    except httpx.HTTPError as exc:
        result.problems.append(Problem(url, f"附件下载失败：{exc}", page_url or None))
        return None
    return b"".join(chunks)


def run_check(start_url: str, companies: list[str]) -> CheckResult:
    """从技术标目录网址出发执行完整核查。任何单点失败都记入问题并继续。"""
    result = CheckResult()
    parsed = urlparse(start_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        result.problems.append(Problem(start_url, "起始网址必须是 http(s) 网址"))
        return result

    visited_lists: set[str] = set()
    queue = [start_url]
    detail_urls: list[str] = []
    seen_details: set[str] = set()
    zip_jobs: list[tuple[str, str]] = []  # (zip_url, page_url)
    seen_zips: set[str] = set()

    with httpx.Client(
        timeout=httpx.Timeout(15.0),
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        # 1) 遍历同源分页，收集投标详情页链接
        while queue:
            url = queue.pop(0)
            if url in visited_lists:
                continue
            if len(visited_lists) >= MAX_LIST_PAGES:
                result.problems.append(Problem(url, f"分页数量超过上限 {MAX_LIST_PAGES}"))
                break
            visited_lists.add(url)
            soup = _fetch_soup(client, url, result)
            if soup is None:
                continue

            items = soup.select("ul.bid-list a[href]")
            nav_links = soup.select("nav.pagination a[href]")
            if not items and not nav_links:
                result.problems.append(Problem(url, "页面结构不符：未找到投标列表（ul.bid-list）或分页导航（nav.pagination）"))
                continue

            result.list_pages.append(url)
            for a in items:
                href = _normalize(a["href"], url)
                if same_origin(href, start_url) and href not in seen_details:
                    seen_details.add(href)
                    detail_urls.append(href)
            for a in nav_links:
                href = _normalize(a["href"], url)
                if same_origin(href, start_url) and href not in visited_lists and href not in queue:
                    queue.append(href)

        # 2) 逐个读取详情页，收集同源 ZIP 附件链接
        for durl in detail_urls:
            if len(result.detail_pages) >= MAX_DETAIL_PAGES:
                result.problems.append(Problem(durl, f"详情页数量超过上限 {MAX_DETAIL_PAGES}"))
                break
            soup = _fetch_soup(client, durl, result)
            if soup is None:
                continue
            result.detail_pages.append(durl)
            for a in soup.select("ul.attachments a[href]"):
                href = _normalize(a["href"], durl)
                if not _is_zip(href) or not same_origin(href, start_url):
                    continue
                if href not in seen_zips:
                    seen_zips.add(href)
                    zip_jobs.append((href, durl))

        # 3) 下载并检查每份 ZIP
        for zip_url, page_url in zip_jobs:
            data = _download(client, zip_url, page_url, result)
            if data is None:
                continue
            result.zip_count += 1
            hits, problems, docx_count = check_zip(data, companies, zip_url, page_url)
            result.hits.extend(hits)
            result.problems.extend(problems)
            result.docx_count += docx_count

    return result
