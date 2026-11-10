"""把各种格式的文件解析成有顺序的文本块。

块是切分的最小原料：一个段落、一个标题，或表格的一行。解析只负责提取文字和结构，
不决定怎么切分。
"""

import csv
import io
import re
from collections import Counter
from dataclasses import dataclass, field

from charset_normalizer import from_bytes

TEXT = "text"
HEADING = "heading"
ROW = "row"

SUPPORTED_TYPES = ("txt", "md", "docx", "pdf", "xlsx", "csv")
# 平均每页提取到的文字少于这个数，判定为扫描件
SCANNED_CHARS_PER_PAGE = 20


class ParseError(Exception):
    """文件无法解析。message 会原样显示给操作者。"""


@dataclass
class Block:
    text: str
    kind: str = TEXT
    level: int = 0  # 标题层级，1 最高；非标题为 0
    page: int | None = None  # 所在页码，只有 PDF 有


@dataclass
class ParseResult:
    blocks: list[Block]
    # 纯文本文件保留原文，按分隔符切分时直接在原文上切，分隔符里的换行才不会丢
    raw_text: str | None = None
    notes: list[str] = field(default_factory=list)


def parse_file(file_type: str, data: bytes) -> ParseResult:
    parser = {
        "txt": _parse_txt,
        "md": _parse_md,
        "docx": _parse_docx,
        "pdf": _parse_pdf,
        "xlsx": _parse_xlsx,
        "csv": _parse_csv,
    }.get(file_type)
    if parser is None:
        raise ParseError(f"不支持的文件类型：{file_type}")
    if not data:
        raise ParseError("文件是空的")
    result = parser(data)
    result.blocks = [b for b in result.blocks if b.text.strip()]
    if not result.blocks:
        raise ParseError("没有从文件中提取到文字")
    return result


# ---------- 纯文本 ----------


def decode_text(data: bytes) -> str:
    """识别编码后解码。优先 UTF-8，其次自动识别，最后按 GB18030 兜底。"""
    for encoding in ("utf-8-sig",):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    best = from_bytes(data).best()
    if best is not None:
        return str(best)
    return data.decode("gb18030", errors="replace")


def _clean(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")


def _parse_txt(data: bytes) -> ParseResult:
    text = _clean(decode_text(data))
    return ParseResult([Block(line.strip()) for line in text.split("\n")], raw_text=text)


_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")


def _parse_md(data: bytes) -> ParseResult:
    text = _clean(decode_text(data))
    blocks: list[Block] = []
    paragraph: list[str] = []
    in_code = False

    def flush() -> None:
        if paragraph:
            blocks.append(Block("\n".join(paragraph)))
            paragraph.clear()

    for line in text.split("\n"):
        if line.strip().startswith("```"):
            in_code = not in_code
            paragraph.append(line)
            continue
        match = None if in_code else _MD_HEADING.match(line)
        if match:
            flush()
            blocks.append(Block(match.group(2), HEADING, level=len(match.group(1))))
        elif not line.strip() and not in_code:
            flush()
        else:
            paragraph.append(line.rstrip())
    flush()
    return ParseResult(blocks, raw_text=text)


# ---------- 表格转文字 ----------


def _dedupe(cells: list[str]) -> list[str]:
    return [c.strip().replace("\n", " ") for c in cells]


def table_to_blocks(rows: list[list[str]], page: int | None = None) -> list[Block]:
    """把表格的每一行转成一段能独立看懂的文字。

    - 两列的表按「名称：内容」处理。
    - 三列及以上的表以第一行为表头，每行写成「列名：值；列名：值」。
    - 整行只有一个非空格的行视为分组标题（如「光学特性」），加在其后各行的前面。
    """
    rows = [_dedupe(r) for r in rows]
    rows = [r for r in rows if any(r)]
    if not rows:
        return []
    width = max(len(r) for r in rows)
    blocks: list[Block] = []

    if width <= 2:
        for row in rows:
            cells = [c for c in row if c]
            if len(cells) == 2:
                blocks.append(Block(f"{cells[0].rstrip('：:')}：{cells[1]}", ROW, page=page))
            elif cells:
                blocks.append(Block(cells[0], ROW, page=page))
        return blocks

    header, group = rows[0], ""
    for row in rows[1:]:
        filled = [c for c in row if c]
        # 分组标题行：整行只有一个内容。可能是合并单元格（去重后只剩一格，或每格重复同一内容），
        # 也可能只填了第一格
        only_first = bool(row[0]) and not any(row[1:])
        merged = len(set(filled)) == 1 and (len(row) == 1 or len(filled) == len(row) > 1)
        if filled and (merged or only_first):
            group = filled[0]
            continue
        parts = []
        for index, value in enumerate(row):
            if not value:
                continue
            name = header[index] if index < len(header) else ""
            parts.append(f"{name}：{value}" if name else value)
        if parts:
            text = "；".join(parts)
            blocks.append(Block(f"{group}｜{text}" if group else text, ROW, page=page))
    if not blocks:
        # 只有表头一行
        blocks.append(Block("；".join(c for c in header if c), ROW, page=page))
    return blocks


# ---------- docx ----------

_DOCX_HEADING = re.compile(r"^(?:heading|标题)\s*(\d)", re.IGNORECASE)


def _parse_docx(data: bytes) -> ParseResult:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        document = Document(io.BytesIO(data))
    except Exception as exc:
        raise ParseError("无法打开文件，请确认是 docx 格式（不支持旧的 doc 格式）") from exc

    blocks: list[Block] = []
    for element in document.element.body.iterchildren():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag == "p":
            paragraph = Paragraph(element, document)
            text = paragraph.text.strip()
            if not text:
                continue
            style = paragraph.style.name if paragraph.style is not None else ""
            match = _DOCX_HEADING.match(style or "")
            if match:
                blocks.append(Block(text, HEADING, level=int(match.group(1))))
            elif (style or "").lower() == "title":
                blocks.append(Block(text, HEADING, level=1))
            else:
                blocks.append(Block(text))
        elif tag == "tbl":
            rows = []
            for row in Table(element, document).rows:
                cells, previous = [], None
                for cell in row.cells:
                    # 合并单元格会重复返回同一个格子，只取一次
                    if cell._tc is previous:
                        continue
                    previous = cell._tc
                    cells.append(cell.text)
                rows.append(cells)
            blocks.extend(table_to_blocks(rows))
    return ParseResult(blocks)


# ---------- PDF ----------

_PAGE_NUMBER = re.compile(r"^\s*(?:第\s*)?(?:\d{1,4}|[IVXLivxl]{1,7})(?:\s*页)?(?:\s*/\s*\d{1,4})?\s*$")
_TOC_LEADER = re.compile(r"[.·…]{4,}\s*[\dIVXivx]+\s*$|[.·…]{8,}")
# 「1.4 标题」「2.3.1 标题」
_NUMBERED_HEADING = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){1,3})\s+(\S.{0,48})$")
# 「2 标题」「第三章 标题」：一级标题要求编号后紧跟汉字，避免把「3 TxD」这样的表格行当成标题
_TOP_HEADING = re.compile(r"^(?:(\d{1,2})[\s、.]+|第[一二三四五六七八九十\d]+[章节部分]\s*)([一-鿿].{0,38})$")
_SENTENCE_END = "。！？；：.!?;:"
_LIST_START = re.compile(r"^(?:[●•◆■□▪·\-–—*]|\d{1,2}[)）.、]|[（(]\d{1,2}[)）]|[a-zA-Z][)）.])\s*")


class _HeadingDetector:
    """识别 PDF 中的编号标题。

    一级标题（「3 标题」）和表格行、操作步骤（「3 串行时钟」「1 选择模式」）长得一样，
    所以只接受编号恰好接在上一章之后的那一个：已经到了 2.6，下一个一级标题只能是 3。
    """

    def __init__(self) -> None:
        self.chapter = 0
        self.has_subheading = False  # 当前这一章是否已经出现过「章.节」形式的小标题

    def level(self, line: str) -> int:
        if line[-1] in "。，；,;":
            return 0
        match = _NUMBERED_HEADING.match(line)
        if match:
            chapter = int(match.group(1).split(".")[0])
            # 章号只会不变或加一，其余的（如版本号 2.14.11）不是标题
            if chapter not in (self.chapter, self.chapter + 1):
                return 0
            self.chapter = chapter
            self.has_subheading = True
            return match.group(1).count(".") + 1
        match = _TOP_HEADING.match(line)
        # 一级标题是短语，不会带逗号；带逗号的多半是表格行或正文
        if not match or len(line) > 30 or re.search(r"[，,]", line):
            return 0
        if match.group(1) is not None:
            number = int(match.group(1))
            # 编号与当前章相同、而当前章还没有出现小标题时也接受：
            # 前面那个可能是恰好同号的步骤，真正的章标题在后面
            if number == self.chapter + 1 or (number == self.chapter and not self.has_subheading):
                self.chapter = number
                self.has_subheading = False
                return 1
            return 0
        return 1


def _strip_page_furniture(pages: list[list[str]]) -> list[list[str]]:
    """去掉页眉、页脚和页码：在多数页的页首或页尾重复出现的行。"""

    def signature(line: str) -> str:
        return re.sub(r"\d+", "#", line.strip())

    edge = 2
    counter: Counter[str] = Counter()
    for lines in pages:
        for line in {signature(x) for x in lines[:edge] + lines[-edge:]}:
            counter[line] += 1
    threshold = max(3, len(pages) // 2)
    repeated = {s for s, n in counter.items() if n >= threshold}

    cleaned = []
    for lines in pages:
        kept = []
        for index, line in enumerate(lines):
            at_edge = index < edge or index >= len(lines) - edge
            if at_edge and (signature(line) in repeated or _PAGE_NUMBER.match(line)):
                continue
            # 目录行（标题……页码）对检索没有价值，只会产生噪声
            if _TOC_LEADER.search(line):
                continue
            kept.append(line)
        cleaned.append(kept)
    return cleaned


def _join(previous: str, line: str) -> str:
    # 中文折行直接相接，英文或数字折行补一个空格
    if (
        previous
        and previous[-1].isascii()
        and previous[-1].isalnum()
        and line[0].isascii()
        and line[0].isalnum()
    ):
        return f"{previous} {line}"
    return previous + line


def _parse_pdf(data: bytes) -> ParseResult:
    import pdfplumber

    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = [
                [line.strip() for line in (page.extract_text() or "").split("\n") if line.strip()]
                for page in pdf.pages
            ]
    except Exception as exc:
        raise ParseError("无法打开文件，请确认是未加密的 PDF") from exc

    total = sum(len(line) for lines in pages for line in lines)
    if not pages or total / len(pages) < SCANNED_CHARS_PER_PAGE:
        raise ParseError("这份 PDF 是扫描件（页面是图片，提取不到文字），目前不支持")

    pages = _strip_page_furniture(pages)
    widths = sorted(len(line) for lines in pages for line in lines)
    full_width = widths[int(len(widths) * 0.9)] if widths else 0

    blocks: list[Block] = []
    current: Block | None = None
    previous_line = ""
    detector = _HeadingDetector()
    for page_number, lines in enumerate(pages, start=1):
        for line in lines:
            level = detector.level(line)
            if level:
                blocks.append(Block(line, HEADING, level=level, page=page_number))
                current, previous_line = None, ""
                continue
            starts_new = (
                current is None
                or previous_line[-1] in _SENTENCE_END
                or _LIST_START.match(line) is not None
                or len(previous_line) < full_width * 0.6
            )
            if starts_new:
                current = Block(line, page=page_number)
                blocks.append(current)
            else:
                assert current is not None
                current.text = _join(current.text, line)
            previous_line = line
    return ParseResult(blocks)


# ---------- 表格文件 ----------


def _sheet_to_blocks(rows: list[list[str]]) -> list[Block]:
    rows = [[c.strip() for c in r] for r in rows]
    rows = [r for r in rows if any(r)]
    if not rows:
        return []
    header = rows[0]
    blocks = []
    for row in rows[1:]:
        parts = [
            f"{header[i]}：{value}" if i < len(header) and header[i] else value
            for i, value in enumerate(row)
            if value
        ]
        if parts:
            blocks.append(Block("；".join(parts), ROW))
    return blocks or [Block("；".join(c for c in header if c), ROW)]


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _parse_xlsx(data: bytes) -> ParseResult:
    from openpyxl import load_workbook

    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise ParseError("无法打开文件，请确认是 xlsx 格式（不支持旧的 xls 格式）") from exc
    blocks: list[Block] = []
    sheets = workbook.worksheets
    for sheet in sheets:
        rows = [[_cell_text(v) for v in row] for row in sheet.iter_rows(values_only=True)]
        sheet_blocks = _sheet_to_blocks(rows)
        if sheet_blocks and len(sheets) > 1:
            blocks.append(Block(sheet.title, HEADING, level=1))
        blocks.extend(sheet_blocks)
    return ParseResult(blocks)


def _parse_csv(data: bytes) -> ParseResult:
    text = _clean(decode_text(data))
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    return ParseResult(_sheet_to_blocks(rows))
