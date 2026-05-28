"""Fetch Zhejiang admission data into data/raw without touching the database."""

from __future__ import annotations

import csv
import random
import re
import shutil
import struct
import subprocess
import sys
import time
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

RAW_DIR = PROJECT_ROOT / "data" / "raw"
CHSI_PLAN_URL = "https://gaokao.chsi.com.cn/zsjh/"
CHSI_CUTOFF_URL = "https://gaokao.chsi.com.cn/gkfs/gdx/"
ZJZS_BASE_URL = "https://www.zjzs.net"
ZJZS_COLUMN_URL = f"{ZJZS_BASE_URL}/col/col45/index.html"
ZJZS_ART_SPORT_COLUMN_URL = f"{ZJZS_BASE_URL}/col/col47/index.html"
ZJZS_SUBJECT_REQUIREMENT_URL = f"{ZJZS_BASE_URL}/col/col173/index.html"
ZJZS_JPAGE_URL = f"{ZJZS_BASE_URL}/module/web/jpage/dataproxy.jsp"
REQUEST_INTERVAL_SECONDS = (1.0, 2.0)
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)

HISTORICAL_CUTOFF_YEARS = (2025, 2024, 2023)
PLAN_YEAR_CANDIDATES = (2026, 2025)
SUBJECT_REQUIREMENT_KEYWORD = "选考科目要求"
SUBJECT_REQUIREMENT_SOURCE_URLS = (
    ZJZS_COLUMN_URL,
    ZJZS_ART_SPORT_COLUMN_URL,
    CHSI_PLAN_URL,
    ZJZS_SUBJECT_REQUIREMENT_URL,
)
SUBJECT_REQUIREMENT_SUPPORTED_SUFFIXES = (".xlsx", ".xls", ".pdf", ".rar")
FIELD_ALIASES = {
    "school_code": ("学校代码", "院校代码", "学校代号", "院校代号", "院校编号"),
    "school_name": ("学校名称", "院校名称", "学校", "院校"),
    "major_code": (
        "专业代码",
        "专业代号",
        "专业(类)代码",
        "专业（类）代码",
        "专业编号",
    ),
    "major_name": (
        "专业名称",
        "专业(类)名称",
        "专业（类）名称",
        "专业",
        "专业类名称",
    ),
    "plan_count": ("计划数", "招生计划数", "招生人数", "计划人数"),
    "min_score": ("最低分", "分数线", "投档分", "最低投档分"),
    "min_rank": ("最低位次", "位次", "投档位次", "最低投档位次"),
}
ZJZS_SEED_ARTICLES = {
    "historical_cutoff": {
        2025: (
            f"{ZJZS_BASE_URL}/art/2025/7/21/art_45_11467.html",
            f"{ZJZS_BASE_URL}/art/2025/7/30/art_45_11488.html",
        ),
        2024: (
            f"{ZJZS_BASE_URL}/art/2024/7/21/art_45_9899.html",
            f"{ZJZS_BASE_URL}/art/2024/7/30/art_45_10143.html",
        ),
        2023: (
            f"{ZJZS_BASE_URL}/art/2023/7/19/art_45_2052.html",
            f"{ZJZS_BASE_URL}/art/2023/7/28/art_45_2335.html",
            f"{ZJZS_BASE_URL}/art/2023/7/28/art_45_2423.html",
        ),
    },
    "admission_plan": {},
}


class ManualDownloadRequired(RuntimeError):
    """Raised when public pages cannot be converted into structured CSVs."""


def clean_text(value: Any) -> str:
    """Normalize scraped text cells."""

    text = str(value).strip()
    return re.sub(r"\s+", " ", text.replace("\u3000", " "))


def record_value(record: dict[str, str], field: str) -> str:
    """Return the first non-empty value matching a canonical field."""

    for alias in FIELD_ALIASES[field]:
        value = clean_text(record.get(alias, ""))
        if value:
            return value
    return ""


def looks_like_note(value: str) -> bool:
    """Return whether a cell is a spreadsheet note instead of data."""

    text = clean_text(value)
    return text.startswith(("注：", "注:", "说明：", "说明:"))


def has_digits(value: str) -> bool:
    """Return whether a value contains any numeric content."""

    return bool(re.search(r"\d", value))


def is_usable_record(record: dict[str, str], dataset: str) -> bool:
    """Return whether a scraped row has enough real data for the dataset."""

    school_code = record_value(record, "school_code")
    school_name = record_value(record, "school_name")
    major_code = record_value(record, "major_code")
    major_name = record_value(record, "major_name")
    if any(looks_like_note(value) for value in (school_code, school_name, major_name)):
        return False
    if not (school_code or school_name) or not (major_code or major_name):
        return False
    if dataset == "historical_cutoff":
        return any(
            has_digits(record_value(record, field))
            for field in ("min_score", "min_rank", "plan_count")
        )
    if dataset == "admission_plan":
        return has_digits(record_value(record, "plan_count"))
    return True


def deduplicate_records(records: list[dict[str, str]]) -> list[dict[str, str]]:
    """Remove exact duplicate rows while preserving original order."""

    seen: set[tuple[tuple[str, str], ...]] = set()
    unique: list[dict[str, str]] = []
    for record in records:
        signature = tuple(sorted((key, clean_text(value)) for key, value in record.items()))
        if signature in seen:
            continue
        seen.add(signature)
        unique.append(record)
    return unique


def filter_records_for_dataset(
    records: list[dict[str, str]],
    dataset: str,
) -> list[dict[str, str]]:
    """Keep only data rows that belong in the target raw CSV."""

    return deduplicate_records(
        [record for record in records if is_usable_record(record, dataset)]
    )


class TableHTMLParser(HTMLParser):
    """Small fallback table parser used when BeautifulSoup is unavailable."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._current_table: list[list[str]] = []
        self._current_row: list[str] = []
        self._current_cell: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._in_table = True
            self._current_table = []
        elif self._in_table and tag == "tr":
            self._in_row = True
            self._current_row = []
        elif self._in_row and tag in {"th", "td"}:
            self._in_cell = True
            self._current_cell = []

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._current_cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"th", "td"} and self._in_cell:
            self._current_row.append(clean_text("".join(self._current_cell)))
            self._in_cell = False
        elif tag == "tr" and self._in_row:
            if any(self._current_row):
                self._current_table.append(self._current_row)
            self._in_row = False
        elif tag == "table" and self._in_table:
            if self._current_table:
                self.tables.append(self._current_table)
            self._in_table = False


def make_unique_headers(headers: list[str]) -> list[str]:
    """Make duplicate or blank headers usable as dictionary keys."""

    seen: dict[str, int] = {}
    unique: list[str] = []
    for index, header in enumerate(headers, start=1):
        name = clean_text(header) or f"column_{index}"
        seen[name] = seen.get(name, 0) + 1
        unique.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
    return unique


def table_rows_to_records(table: list[list[str]]) -> list[dict[str, str]]:
    """Convert table rows into dictionaries using the first row as headers."""

    if len(table) < 2:
        return []
    headers = make_unique_headers(table[0])
    records: list[dict[str, str]] = []
    for row in table[1:]:
        if not any(clean_text(cell) for cell in row):
            continue
        padded = row + [""] * (len(headers) - len(row))
        records.append(
            {
                headers[index]: clean_text(value)
                for index, value in enumerate(padded[: len(headers)])
            }
        )
    return records


def extract_table_records(html: str) -> list[dict[str, str]]:
    """Extract records from HTML tables."""

    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        tables: list[list[list[str]]] = []
        for table in soup.find_all("table"):
            rows: list[list[str]] = []
            for tr in table.find_all("tr"):
                cells = [
                    clean_text(cell.get_text(" ", strip=True))
                    for cell in tr.find_all(["th", "td"])
                ]
                if any(cells):
                    rows.append(cells)
            if rows:
                tables.append(rows)
    except Exception:
        parser = TableHTMLParser()
        parser.feed(html)
        tables = parser.tables

    records: list[dict[str, str]] = []
    for table in tables:
        records.extend(table_rows_to_records(table))
    return records


def extract_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """Extract links from normal HTML and Zhejiang jpage CDATA payloads."""

    links: list[tuple[str, str]] = []
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")
        for anchor in soup.find_all("a", href=True):
            title = clean_text(anchor.get("title") or anchor.get_text(" ", strip=True))
            links.append((title, urljoin(base_url, anchor["href"])))
        for meta in soup.find_all("meta", attrs={"name": "Image"}):
            content = meta.get("content")
            if content:
                links.append((attachment_name_from_url(content), urljoin(base_url, content)))
    except Exception:
        pass

    for match in re.finditer(
        r'<a\b(?P<tag>[^>]*)href="(?P<href>[^"]+)"(?P<tail>[^>]*)>'
        r"(?P<body>.*?)</a>",
        html,
        re.IGNORECASE | re.DOTALL,
    ):
        tag = f"{match.group('tag')} {match.group('tail')}"
        title_match = re.search(r'title="([^"]+)"', tag)
        body_text = re.sub(r"<[^>]+>", " ", match.group("body"))
        title = clean_text(title_match.group(1) if title_match else body_text)
        url = urljoin(base_url, match.group("href"))
        if (title, url) not in links:
            links.append((title, url))
    for match in re.finditer(
        r'<meta[^>]+name="Image"[^>]+content="([^"]+)"',
        html,
        re.IGNORECASE,
    ):
        url = urljoin(base_url, match.group(1))
        item = (attachment_name_from_url(url), url)
        if item not in links:
            links.append(item)
    return links


def attachment_name_from_url(url: str) -> str:
    """Get a readable attachment name from a download URL."""

    parsed = urlparse(url)
    query = unquote(parsed.query)
    match = re.search(r"showname=([^&]+)", query)
    if match:
        return match.group(1)
    return unquote(Path(parsed.path).name)


def attachment_suffix(url: str) -> str:
    """Return a file suffix from a URL or its attachment filename."""

    return Path(attachment_name_from_url(urlparse(url).path + "?" + urlparse(url).query)).suffix.lower() or Path(
        unquote(urlparse(url).path)
    ).suffix.lower()


def subject_requirement_candidate_links(
    html: str,
    base_url: str,
) -> list[tuple[str, str]]:
    """Find links related to Zhejiang subject requirement files or pages."""

    candidates: list[tuple[str, str]] = []
    for title, url in extract_links(html, base_url):
        combined = f"{title} {unquote(url)}"
        if SUBJECT_REQUIREMENT_KEYWORD not in combined:
            continue
        suffix = attachment_suffix(url)
        if suffix and suffix not in SUBJECT_REQUIREMENT_SUPPORTED_SUFFIXES:
            continue
        item = (title, url)
        if item not in candidates:
            candidates.append(item)
    return candidates


def subject_requirement_target_path(url: str, raw_dir: Path = RAW_DIR) -> Path | None:
    """Return the canonical data/raw path for a subject requirement attachment."""

    suffix = attachment_suffix(url)
    if suffix in {".xlsx", ".xls"}:
        return raw_dir / "subject_requirement.xlsx"
    if suffix == ".pdf":
        return raw_dir / "subject_requirement.pdf"
    return None


def extract_subject_requirement_archive(content: bytes, raw_dir: Path) -> Path | None:
    """Extract the first Excel or PDF file from an official subject requirement archive."""

    extractor = shutil.which("bsdtar") or shutil.which("tar")
    if extractor is None:
        return None
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        archive_path = tmp_path / "subject_requirement.rar"
        archive_path.write_bytes(content)
        list_result = subprocess.run(
            [extractor, "-tf", str(archive_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if list_result.returncode != 0:
            return None
        members = [line.strip() for line in list_result.stdout.splitlines() if line.strip()]
        preferred_member = next(
            (
                member
                for suffix in (".xlsx", ".xls", ".pdf")
                for member in members
                if member.lower().endswith(suffix)
            ),
            None,
        )
        if preferred_member is None:
            return None
        extract_result = subprocess.run(
            [extractor, "-xf", str(archive_path), "-C", str(tmp_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if extract_result.returncode != 0:
            return None
        extracted = tmp_path / preferred_member
        if not extracted.exists():
            matches = [
                path
                for path in tmp_path.rglob("*")
                if path.is_file()
                and path.suffix.lower() in {".xlsx", ".xls", ".pdf"}
            ]
            if not matches:
                return None
            extracted = matches[0]
        target = subject_requirement_target_path(str(extracted), raw_dir)
        if target is None:
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(extracted, target)
        return target


def save_subject_requirement_attachment(
    content: bytes,
    url: str,
    raw_dir: Path = RAW_DIR,
) -> Path | None:
    """Save a subject requirement attachment under its canonical raw filename."""

    suffix = attachment_suffix(url)
    if suffix == ".rar":
        extracted = extract_subject_requirement_archive(content, raw_dir)
        if extracted is not None:
            return extracted
        fallback = raw_dir / "subject_requirement.rar"
        fallback.parent.mkdir(parents=True, exist_ok=True)
        fallback.write_bytes(content)
        return None
    target = subject_requirement_target_path(url, raw_dir)
    if target is None:
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


def write_records_csv(records: list[dict[str, str]], path: str | Path) -> None:
    """Write records to a UTF-8 CSV."""

    if not records:
        raise ValueError("cannot write an empty records list")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    headers: list[str] = []
    for record in records:
        for key in record:
            if key not in headers:
                headers.append(key)
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(records)


def read_csv_bytes(content: bytes) -> list[dict[str, str]]:
    """Read CSV bytes using common encodings."""

    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = content.decode(encoding)
            return [dict(row) for row in csv.DictReader(text.splitlines())]
        except UnicodeDecodeError:
            continue
    return []


def u16(data: bytes, offset: int) -> int:
    """Read an unsigned little-endian 16-bit integer."""

    return struct.unpack_from("<H", data, offset)[0]


def u32(data: bytes, offset: int) -> int:
    """Read an unsigned little-endian 32-bit integer."""

    return struct.unpack_from("<I", data, offset)[0]


def i32(data: bytes, offset: int) -> int:
    """Read a signed little-endian 32-bit integer."""

    return struct.unpack_from("<i", data, offset)[0]


def sector_data(content: bytes, sector_id: int, sector_size: int) -> bytes:
    """Return one OLE compound-file sector."""

    start = 512 + sector_id * sector_size
    return content[start : start + sector_size]


def collect_chain(start: int, fat: list[int]) -> list[int]:
    """Collect a FAT chain from an OLE compound file."""

    end_of_chain = 0xFFFFFFFE
    sectors: list[int] = []
    current = start
    visited: set[int] = set()
    while current not in (end_of_chain, 0xFFFFFFFF) and current < len(fat):
        if current in visited:
            break
        visited.add(current)
        sectors.append(current)
        current = fat[current]
    return sectors


def read_cfb_stream(content: bytes, stream_names: tuple[str, ...]) -> bytes:
    """Extract a stream from an OLE compound-file based .xls document."""

    if not content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise ValueError("not an OLE compound file")

    sector_size = 1 << u16(content, 30)
    mini_sector_size = 1 << u16(content, 32)
    first_dir_sector = u32(content, 48)
    mini_cutoff = u32(content, 56)
    first_mini_fat_sector = u32(content, 60)
    num_mini_fat_sectors = u32(content, 64)

    difat = [u32(content, 76 + index * 4) for index in range(109)]
    fat_sector_ids = [sector for sector in difat if sector < 0xFFFFFFF0]
    fat: list[int] = []
    for sector_id in fat_sector_ids:
        sector = sector_data(content, sector_id, sector_size)
        fat.extend(u32(sector, offset) for offset in range(0, sector_size, 4))

    directory_stream = b"".join(
        sector_data(content, sector_id, sector_size)
        for sector_id in collect_chain(first_dir_sector, fat)
    )

    entries: dict[str, tuple[int, int, int]] = {}
    root_entry: tuple[int, int, int] | None = None
    for offset in range(0, len(directory_stream), 128):
        entry = directory_stream[offset : offset + 128]
        if len(entry) < 128:
            continue
        name_length = u16(entry, 64)
        if name_length < 2:
            continue
        name = entry[: name_length - 2].decode("utf-16le", errors="ignore")
        entry_type = entry[66]
        start_sector = u32(entry, 116)
        size = u32(entry, 120)
        entries[name] = (entry_type, start_sector, size)
        if entry_type == 5:
            root_entry = (entry_type, start_sector, size)

    for name in stream_names:
        if name not in entries:
            continue
        _entry_type, start_sector, size = entries[name]
        if size >= mini_cutoff:
            data = b"".join(
                sector_data(content, sector_id, sector_size)
                for sector_id in collect_chain(start_sector, fat)
            )
            return data[:size]

        if root_entry is None:
            raise ValueError("missing mini stream root entry")
        _root_type, root_start, root_size = root_entry
        root_stream = b"".join(
            sector_data(content, sector_id, sector_size)
            for sector_id in collect_chain(root_start, fat)
        )[:root_size]
        mini_fat = []
        for sector_id in collect_chain(first_mini_fat_sector, fat)[:num_mini_fat_sectors]:
            sector = sector_data(content, sector_id, sector_size)
            mini_fat.extend(u32(sector, offset) for offset in range(0, sector_size, 4))
        chunks = []
        for mini_sector_id in collect_chain(start_sector, mini_fat):
            start = mini_sector_id * mini_sector_size
            chunks.append(root_stream[start : start + mini_sector_size])
        return b"".join(chunks)[:size]

    raise ValueError(f"missing stream: {stream_names}")


def iter_biff_records(workbook: bytes) -> list[tuple[int, bytes, int]]:
    """Return BIFF records as (opcode, payload, offset) tuples."""

    records: list[tuple[int, bytes, int]] = []
    offset = 0
    while offset + 4 <= len(workbook):
        opcode = u16(workbook, offset)
        length = u16(workbook, offset + 2)
        payload_start = offset + 4
        payload = workbook[payload_start : payload_start + length]
        records.append((opcode, payload, offset))
        offset = payload_start + length
    return records


def decode_rk(raw: int) -> float:
    """Decode an Excel RK number."""

    multiplied = raw & 0x01
    is_integer = raw & 0x02
    if is_integer:
        value = i32(struct.pack("<I", raw & 0xFFFFFFFC), 0) >> 2
    else:
        packed = struct.pack("<II", 0, raw & 0xFFFFFFFC)
        value = struct.unpack("<d", packed)[0]
    if multiplied:
        value /= 100
    return value


def format_cell(value: Any) -> str:
    """Format parsed Excel cell values as stable strings."""

    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return clean_text(value)


def decode_unicode(data: bytes, offset: int) -> tuple[str, int]:
    """Decode a BIFF8 Unicode string that does not cross Continue records."""

    if offset + 3 > len(data):
        return "", len(data)
    length = u16(data, offset)
    flags = data[offset + 2]
    offset += 3
    rich_runs = u16(data, offset) if flags & 0x08 and offset + 2 <= len(data) else 0
    if flags & 0x08:
        offset += 2
    ext_size = u32(data, offset) if flags & 0x04 and offset + 4 <= len(data) else 0
    if flags & 0x04:
        offset += 4
    char_width = 2 if flags & 0x01 else 1
    byte_length = length * char_width
    raw = data[offset : offset + byte_length]
    text = raw.decode("utf-16le" if char_width == 2 else "latin1", errors="ignore")
    offset += byte_length + rich_runs * 4 + ext_size
    return clean_text(text), offset


class SSTStream:
    """Reader for BIFF SST payloads with Continue-record text segments."""

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.chunk_index = 0
        self.offset = 0

    def available(self) -> int:
        """Return remaining bytes in the current chunk."""

        if self.chunk_index >= len(self.chunks):
            return 0
        return len(self.chunks[self.chunk_index]) - self.offset

    def advance_chunk(self) -> None:
        """Move to the next BIFF payload chunk."""

        self.chunk_index += 1
        self.offset = 0

    def read(self, size: int) -> bytes:
        """Read raw bytes across chunks."""

        parts: list[bytes] = []
        remaining = size
        while remaining > 0 and self.chunk_index < len(self.chunks):
            if self.available() == 0:
                self.advance_chunk()
                continue
            take = min(remaining, self.available())
            chunk = self.chunks[self.chunk_index]
            parts.append(chunk[self.offset : self.offset + take])
            self.offset += take
            remaining -= take
        return b"".join(parts)

    def read_u8(self) -> int:
        """Read an unsigned byte."""

        data = self.read(1)
        return data[0] if data else 0

    def read_u16(self) -> int:
        """Read an unsigned little-endian 16-bit integer."""

        data = self.read(2)
        return struct.unpack("<H", data.ljust(2, b"\x00"))[0]

    def read_u32(self) -> int:
        """Read an unsigned little-endian 32-bit integer."""

        data = self.read(4)
        return struct.unpack("<I", data.ljust(4, b"\x00"))[0]

    def read_chars(self, char_count: int, flags: int) -> str:
        """Read string characters, consuming continuation option flags."""

        remaining = char_count
        current_flags = flags
        text_parts: list[str] = []
        while remaining > 0 and self.chunk_index < len(self.chunks):
            width = 2 if current_flags & 0x01 else 1
            if self.available() < width:
                self.advance_chunk()
                if self.chunk_index < len(self.chunks):
                    current_flags = self.read_u8()
                continue
            chars_available = self.available() // width
            take_chars = min(remaining, chars_available)
            raw = self.read(take_chars * width)
            text_parts.append(
                raw.decode("utf-16le" if width == 2 else "latin1", errors="ignore")
            )
            remaining -= take_chars
            if remaining > 0:
                self.advance_chunk()
                if self.chunk_index < len(self.chunks):
                    current_flags = self.read_u8()
        return clean_text("".join(text_parts))


def parse_sst(records: list[tuple[int, bytes, int]], start_index: int) -> list[str]:
    """Parse an SST record and adjacent Continue payloads."""

    payload = records[start_index][1]
    chunks = [payload]
    index = start_index + 1
    while index < len(records) and records[index][0] == 0x003C:
        chunks.append(records[index][1])
        index += 1
    if len(payload) < 8:
        return []
    unique_count = u32(payload, 4)
    stream = SSTStream([payload[8:]] + chunks[1:])
    values: list[str] = []
    for _ in range(unique_count):
        if stream.chunk_index >= len(stream.chunks):
            break
        char_count = stream.read_u16()
        flags = stream.read_u8()
        rich_runs = stream.read_u16() if flags & 0x08 else 0
        ext_size = stream.read_u32() if flags & 0x04 else 0
        text = stream.read_chars(char_count, flags)
        if rich_runs:
            stream.read(rich_runs * 4)
        if ext_size:
            stream.read(ext_size)
        values.append(text)
    return values


def parse_biff_rows(content: bytes) -> list[list[str]]:
    """Parse a BIFF8 .xls file into row lists using a small stdlib parser."""

    workbook = read_cfb_stream(content, ("Workbook", "Book"))
    records = iter_biff_records(workbook)
    sst: list[str] = []
    first_worksheet_offset: int | None = None
    for index, (opcode, payload, offset) in enumerate(records):
        if opcode == 0x00FC:
            sst = parse_sst(records, index)
        elif opcode == 0x0085 and first_worksheet_offset is None and len(payload) >= 8:
            sheet_type = payload[5]
            if sheet_type == 0:
                first_worksheet_offset = u32(payload, 0)

    start_index = 0
    if first_worksheet_offset is not None:
        for index, (_opcode, _payload, offset) in enumerate(records):
            if offset >= first_worksheet_offset:
                start_index = index
                break

    cells: dict[tuple[int, int], str] = {}
    for opcode, payload, _offset in records[start_index:]:
        if opcode == 0x000A:
            break
        if opcode == 0x00FD and len(payload) >= 10:
            row, col = u16(payload, 0), u16(payload, 2)
            sst_index = u32(payload, 6)
            cells[(row, col)] = sst[sst_index] if sst_index < len(sst) else ""
        elif opcode == 0x0203 and len(payload) >= 14:
            row, col = u16(payload, 0), u16(payload, 2)
            cells[(row, col)] = format_cell(struct.unpack_from("<d", payload, 6)[0])
        elif opcode == 0x027E and len(payload) >= 10:
            row, col = u16(payload, 0), u16(payload, 2)
            cells[(row, col)] = format_cell(decode_rk(u32(payload, 6)))
        elif opcode == 0x00BD and len(payload) >= 6:
            row, first_col = u16(payload, 0), u16(payload, 2)
            last_col = u16(payload, len(payload) - 2)
            offset = 4
            for col in range(first_col, last_col + 1):
                if offset + 6 > len(payload) - 2:
                    break
                cells[(row, col)] = format_cell(decode_rk(u32(payload, offset + 2)))
                offset += 6
        elif opcode == 0x0204 and len(payload) >= 8:
            row, col = u16(payload, 0), u16(payload, 2)
            text, _ = decode_unicode(payload, 6)
            cells[(row, col)] = text

    if not cells:
        return []
    max_row = max(row for row, _col in cells)
    max_col = max(col for _row, col in cells)
    return [
        [cells.get((row, col), "") for col in range(max_col + 1)]
        for row in range(max_row + 1)
    ]


def find_header_row(rows: list[list[str]]) -> int | None:
    """Find the most likely header row in scraped spreadsheet rows."""

    header_keywords = ("学校", "院校", "专业", "计划", "分数", "位次")
    for index, row in enumerate(rows[:30]):
        hits = sum(1 for cell in row if any(keyword in cell for keyword in header_keywords))
        if hits >= 2:
            return index
    return 0 if rows else None


def spreadsheet_bytes_to_records(content: bytes) -> list[dict[str, str]]:
    """Convert .xls/.xlsx-like bytes to records."""

    rows = parse_biff_rows(content)
    header_index = find_header_row(rows)
    if header_index is None:
        return []
    headers = make_unique_headers(rows[header_index])
    records: list[dict[str, str]] = []
    for row in rows[header_index + 1 :]:
        if not any(clean_text(cell) for cell in row):
            continue
        padded = row + [""] * (len(headers) - len(row))
        records.append(
            {
                headers[index]: clean_text(value)
                for index, value in enumerate(padded[: len(headers)])
            }
        )
    return fill_down_merged_columns(records)


def fill_down_merged_columns(records: list[dict[str, str]]) -> list[dict[str, str]]:
    """Fill values omitted by merged cells in official spreadsheets."""

    fill_keywords = (
        "学校代码",
        "学校代号",
        "院校代码",
        "院校代号",
        "学校名称",
        "院校名称",
    )
    last_values: dict[str, str] = {}
    for record in records:
        for key, value in list(record.items()):
            if not any(keyword in key for keyword in fill_keywords):
                continue
            if value:
                last_values[key] = value
            elif key in last_values:
                record[key] = last_values[key]
    return records


def make_session() -> Any:
    """Create a requests session with a browser-like User-Agent."""

    import requests

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def wait_between_requests() -> None:
    """Sleep 1-2 seconds between public website requests."""

    time.sleep(random.uniform(*REQUEST_INTERVAL_SECONDS))


def fetch_text(session: Any, url: str, **kwargs: Any) -> str:
    """Fetch text and raise a manual-download hint on anti-crawler responses."""

    response = session.get(url, timeout=30, **kwargs)
    if response.status_code in {401, 403, 412, 429}:
        raise ManualDownloadRequired(f"访问被拦截：{url}")
    response.raise_for_status()
    response.encoding = response.apparent_encoding or response.encoding
    wait_between_requests()
    return response.text


def post_text(session: Any, url: str, data: dict[str, Any]) -> str:
    """POST form data and return response text."""

    response = session.post(url, data=data, timeout=30)
    if response.status_code in {401, 403, 412, 429}:
        raise ManualDownloadRequired(f"访问被拦截：{url}")
    response.raise_for_status()
    response.encoding = response.apparent_encoding or response.encoding
    wait_between_requests()
    return response.text


def fetch_bytes(session: Any, url: str, **kwargs: Any) -> bytes:
    """Fetch binary attachment content."""

    response = session.get(url, timeout=60, **kwargs)
    if response.status_code in {401, 403, 412, 429}:
        raise ManualDownloadRequired(f"访问被拦截：{url}")
    response.raise_for_status()
    wait_between_requests()
    return response.content


def download_attachment_records(session: Any, url: str) -> list[dict[str, str]]:
    """Download an attachment URL and convert it into records."""

    content = fetch_bytes(session, url)
    name = attachment_name_from_url(url).lower()
    if name.endswith(".csv"):
        return read_csv_bytes(content)
    if name.endswith((".xls", ".xlsx")):
        return spreadsheet_bytes_to_records(content)
    if name.endswith((".htm", ".html")) or content[:32].lstrip().startswith(b"<"):
        return extract_table_records(content.decode("utf-8", errors="ignore"))
    return []


def chsi_params(year: int) -> list[dict[str, Any]]:
    """Candidate query parameters for CHSI public pages."""

    return [
        {"year": year, "sf": "浙江", "type": "普通类"},
        {"nf": year, "ssdm": "33", "kl": "普通类"},
        {"year": year, "province": "浙江", "category": "普通类"},
    ]


def download_from_chsi(session: Any, dataset: str, year: int, target_path: Path) -> bool:
    """Try to download structured HTML tables from CHSI."""

    url = CHSI_PLAN_URL if dataset == "admission_plan" else CHSI_CUTOFF_URL
    for params in chsi_params(year):
        try:
            html = fetch_text(session, url, params=params)
        except ManualDownloadRequired as exc:
            print(f"阳光高考 {dataset} {year}: {exc}")
            return False
        except Exception as exc:
            print(f"阳光高考 {dataset} {year}: {exc}")
            return False
        records = filter_records_for_dataset(extract_table_records(html), dataset)
        if records:
            write_records_csv(records, target_path)
            return True
    return False


def article_matches(title: str, dataset: str, year: int) -> bool:
    """Return whether a Zhejiang article title matches the desired dataset."""

    if str(year) not in title:
        return False
    if "单独考试" in title:
        return False
    if dataset == "historical_cutoff":
        return "普通类" in title and "平行投档" in title
    return "招生计划" in title and ("普通高校" in title or "普通类" in title)


def discover_zjzs_articles(
    session: Any,
    dataset: str,
    year: int,
    max_pages: int = 5,
) -> list[str]:
    """Discover matching Zhejiang exam institute article URLs."""

    discovered: list[str] = []
    for url in ZJZS_SEED_ARTICLES.get(dataset, {}).get(year, ()):
        if url not in discovered:
            discovered.append(url)

    try:
        html = fetch_text(session, ZJZS_COLUMN_URL)
        for title, url in extract_links(html, ZJZS_BASE_URL):
            if article_matches(title, dataset, year) and url not in discovered:
                discovered.append(url)
    except Exception as exc:
        print(f"浙江考试院栏目页读取失败：{exc}")

    post_data = {
        "col": 1,
        "webid": 1,
        "path": "/",
        "columnid": 45,
        "sourceContentType": 1,
        "unitid": 100,
        "webname": "浙江省教育考试院官网",
        "permissiontype": 0,
    }
    for page in range(1, max_pages + 1):
        try:
            html = post_text(session, ZJZS_JPAGE_URL, {**post_data, "page": page})
        except Exception as exc:
            print(f"浙江考试院分页第 {page} 页读取失败：{exc}")
            continue
        for title, url in extract_links(html, ZJZS_BASE_URL):
            if article_matches(title, dataset, year) and url not in discovered:
                discovered.append(url)
    return discovered


def download_from_zjzs(session: Any, dataset: str, year: int, target_path: Path) -> bool:
    """Try to download data from Zhejiang exam institute article attachments."""

    records: list[dict[str, str]] = []
    for article_url in discover_zjzs_articles(session, dataset, year):
        try:
            html = fetch_text(session, article_url)
        except ManualDownloadRequired as exc:
            print(f"浙江考试院 {dataset} {year}: {exc}")
            continue
        except Exception as exc:
            print(f"浙江考试院 {dataset} {year}: {exc}")
            continue
        records.extend(extract_table_records(html))
        for title, link in extract_links(html, article_url):
            lower_name = f"{title} {link}".lower()
            if ".pdf" in lower_name:
                continue
            if not any(suffix in lower_name for suffix in (".csv", ".xls", ".xlsx", ".html")):
                continue
            try:
                records.extend(download_attachment_records(session, link))
            except Exception as exc:
                print(f"附件转换失败：{attachment_name_from_url(link)} ({exc})")
                continue

    records = filter_records_for_dataset(records, dataset)
    if not records:
        return False
    write_records_csv(records, target_path)
    return True


def existing_subject_requirement_file(raw_dir: Path = RAW_DIR) -> Path | None:
    """Return an already downloaded subject requirement file if present."""

    for name in ("subject_requirement.xlsx", "subject_requirement.pdf"):
        path = raw_dir / name
        if path.exists():
            return path
    return None


def download_subject_requirement(session: Any, raw_dir: Path = RAW_DIR) -> Path | None:
    """Download Zhejiang subject requirement Excel or PDF data when discoverable."""

    existing = existing_subject_requirement_file(raw_dir)
    if existing is not None:
        return existing

    for source_url in SUBJECT_REQUIREMENT_SOURCE_URLS:
        try:
            html = fetch_text(session, source_url)
        except ManualDownloadRequired as exc:
            print(f"选考科目要求 {source_url}: {exc}")
            continue
        except Exception as exc:
            print(f"选考科目要求 {source_url}: {exc}")
            continue
        for title, link in subject_requirement_candidate_links(html, source_url):
            suffix = attachment_suffix(link)
            if suffix in SUBJECT_REQUIREMENT_SUPPORTED_SUFFIXES:
                try:
                    content = fetch_bytes(session, link)
                    saved = save_subject_requirement_attachment(content, link, raw_dir)
                except Exception as exc:
                    print(f"选考科目要求附件下载失败：{title or link} ({exc})")
                    continue
                if saved is not None:
                    return saved
                continue
            try:
                article_html = fetch_text(session, link)
            except Exception as exc:
                print(f"选考科目要求页面读取失败：{title or link} ({exc})")
                continue
            for attachment_title, attachment_url in subject_requirement_candidate_links(
                article_html,
                link,
            ):
                try:
                    content = fetch_bytes(session, attachment_url)
                    saved = save_subject_requirement_attachment(
                        content,
                        attachment_url,
                        raw_dir,
                    )
                except Exception as exc:
                    print(f"选考科目要求附件下载失败：{attachment_title or attachment_url} ({exc})")
                    continue
                if saved is not None:
                    return saved
    return None


def raw_csv_path(dataset: str, year: int, raw_dir: Path = RAW_DIR) -> Path:
    """Return the canonical raw CSV path for a dataset/year pair."""

    return raw_dir / f"{dataset}_{year}.csv"


def choose_admission_plan_file(raw_dir: Path = RAW_DIR) -> tuple[Path, int]:
    """Choose 2026 plan data when present, otherwise fall back to 2025."""

    for year in PLAN_YEAR_CANDIDATES:
        path = raw_csv_path("admission_plan", year, raw_dir)
        if path.exists():
            return path, year
    return raw_csv_path("admission_plan", 2025, raw_dir), 2025


def manual_download_message(missing: list[str]) -> str:
    """Build a clear manual-download instruction message."""

    files = "、".join(missing)
    return (
        f"以下文件未能自动下载或转换：{files}\n"
        "可手动从以下入口下载后转换为 CSV，并放入 data/raw/：\n"
        f"- 阳光高考投档线：{CHSI_CUTOFF_URL}\n"
        f"- 浙江省教育考试院统一高考栏目：{ZJZS_COLUMN_URL}\n"
        f"- 浙江省教育考试院选考科目要求：{ZJZS_SUBJECT_REQUIREMENT_URL}\n"
        "文件名使用 historical_cutoff_2024.csv、subject_requirement.pdf "
        "或 subject_requirement.xlsx 等格式。"
    )


def download_missing_files(raw_dir: Path = RAW_DIR) -> list[str]:
    """Download missing raw CSVs and return unresolved filenames."""

    raw_dir.mkdir(parents=True, exist_ok=True)
    session = make_session()
    missing: list[str] = []

    subject_requirement_path = download_subject_requirement(session, raw_dir)
    if subject_requirement_path is not None:
        print(f"已保存 {subject_requirement_path}")
    else:
        missing.append("subject_requirement.xlsx 或 subject_requirement.pdf")

    for year in HISTORICAL_CUTOFF_YEARS:
        path = raw_csv_path("historical_cutoff", year, raw_dir)
        if path.exists():
            continue
        downloaded = (
            download_from_chsi(session, "historical_cutoff", year, path)
            or download_from_zjzs(session, "historical_cutoff", year, path)
        )
        if downloaded:
            print(f"已保存 {path}")
        else:
            missing.append(path.name)

    return missing


def count_csv_rows(path: Path) -> int:
    """Return the number of data rows in a CSV file."""

    with path.open(encoding="utf-8-sig", newline="") as handle:
        return max(sum(1 for _row in csv.reader(handle)) - 1, 0)


def raw_file_row_counts(raw_dir: Path = RAW_DIR) -> dict[str, int]:
    """Return row counts for CSV files under data/raw."""

    if not raw_dir.exists():
        return {}
    return {
        path.name: count_csv_rows(path)
        for path in sorted(raw_dir.glob("*.csv"))
    }


def print_raw_file_row_counts(raw_dir: Path = RAW_DIR) -> None:
    """Print row counts for raw CSV files."""

    print("=== data/raw 文件行数 ===")
    counts = raw_file_row_counts(raw_dir)
    if not counts:
        print("无 CSV 文件")
        return
    for name, count in counts.items():
        print(f"{name}: {count} 条")
    subject_file = existing_subject_requirement_file(raw_dir)
    if subject_file is not None:
        print(f"{subject_file.name}: 已下载")


def main() -> None:
    """Fetch missing raw CSV files and print local row counts."""

    missing = download_missing_files(RAW_DIR)
    if missing:
        print(manual_download_message(missing))
    print_raw_file_row_counts(RAW_DIR)


if __name__ == "__main__":
    main()
