"""把文本块切成切片。三种方式：按分隔符、按长度、按标题层级。"""

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.ingest.parsers import HEADING, ROW, Block, ParseResult

# 任何切片都不超过这个字数（Milvus 字段长度与向量模型输入长度的共同上限）
HARD_MAX_CHARS = 4000
MAX_CHUNKS_PER_DOCUMENT = 20000

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？；\n])|(?<=[.!?;])\s+")
_HAS_CONTENT = re.compile(r"[0-9A-Za-z一-鿿]")


class ChunkConfig(BaseModel):
    """切分设置。知识库保存一份默认值，上传单个文档时可以覆盖。"""

    mode: Literal["separator", "length", "heading"] = "length"
    separator: str = Field(
        default="\n\n", min_length=1, max_length=100, description="按分隔符切分时使用；可写 \\n 表示换行"
    )
    chunk_size: int = Field(default=500, ge=100, le=HARD_MAX_CHARS, description="每片的目标字数")
    overlap: int = Field(default=50, ge=0, le=1000, description="相邻两片重叠的字数")
    heading_level: int = Field(default=2, ge=1, le=6, description="按标题切分时，切到第几级标题")

    @model_validator(mode="after")
    def _check(self) -> "ChunkConfig":
        if self.overlap > self.chunk_size // 2:
            raise ValueError("重叠字数不能超过每片字数的一半")
        return self

    @property
    def real_separator(self) -> str:
        return self.separator.replace("\\n", "\n").replace("\\t", "\t")


@dataclass
class Draft:
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


class ChunkingError(Exception):
    """切分失败。message 会原样显示给操作者。"""


def chunk_document(parsed: ParseResult, config: ChunkConfig) -> tuple[list[Draft], str]:
    """切分文档，返回（切片草稿，实际使用的切分方式）。

    按标题切分但文档里没有识别到标题时，自动退回按长度切分。
    """
    mode = config.mode
    if mode == "separator":
        drafts = _by_separator(parsed, config)
    elif mode == "heading" and any(b.kind == HEADING for b in parsed.blocks):
        drafts = _by_heading(parsed.blocks, config)
    else:
        mode = "length"
        drafts = _by_length(parsed.blocks, config, [])

    drafts = [d for d in drafts if _HAS_CONTENT.search(d.content)]
    if not drafts:
        raise ChunkingError("切分后没有得到任何有内容的切片")
    if len(drafts) > MAX_CHUNKS_PER_DOCUMENT:
        raise ChunkingError(f"切片数量超过上限 {MAX_CHUNKS_PER_DOCUMENT}，请调大每片字数或拆分文件")
    return drafts, mode


def split_long(text: str, size: int, overlap: int = 0) -> list[str]:
    """把过长的文字切成不超过 size 的若干段，尽量在句子边界断开。"""
    if len(text) <= size:
        return [text]
    sentences: list[str] = []
    for sentence in (s for s in _SENTENCE_SPLIT.split(text) if s):
        # 单个句子本身就超长时只能硬切
        sentences.extend(sentence[i : i + size] for i in range(0, len(sentence), size))

    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        if current and len(current) + len(sentence) > size:
            pieces.append(current)
            tail = _tail(current, overlap)
            current = tail if len(tail) + len(sentence) <= size else ""
        current += sentence
    if current.strip():
        pieces.append(current)
    return [p.strip() for p in pieces if p.strip()]


def _tail(text: str, overlap: int, allow_mid_sentence: bool = True) -> str:
    """取文字末尾约 overlap 个字作为下一片的开头，从一行或一句的开头取起。

    末尾这段里找不到行或句的开头时：普通文字退而从中间取；表格行不取，
    因为半截的表格行没有意义。
    """
    if overlap <= 0 or not text:
        return ""
    tail = text[-overlap:]
    match = re.search(r"[。！？\n]", tail[:-1])
    if match:
        return tail[match.end() :]
    return tail if allow_mid_sentence else ""


def _path_text(path: list[tuple[int, str]]) -> str:
    return " > ".join(title for _, title in path)


def _update_path(path: list[tuple[int, str]], block: Block) -> None:
    while path and path[-1][0] >= block.level:
        path.pop()
    path.append((block.level, block.text))


def _by_length(blocks: list[Block], config: ChunkConfig, path: list[tuple[int, str]]) -> list[Draft]:
    """按段落聚合到目标字数。path 是进入这段内容之前已经生效的标题路径。"""
    size, overlap = config.chunk_size, config.overlap
    path = list(path)
    drafts: list[Draft] = []
    parts: list[str] = []
    meta: dict[str, Any] = {}
    last_kind = ""

    def length() -> int:
        return sum(len(p) for p in parts) + max(len(parts) - 1, 0)

    def flush(carry: bool) -> None:
        nonlocal parts, meta
        if not parts:
            return
        content = "\n".join(parts).strip()
        if content:
            drafts.append(Draft(content, dict(meta)))
        tail = _tail(content, overlap, allow_mid_sentence=last_kind != ROW) if carry else ""
        parts = [tail] if tail.strip() else []
        meta = {}

    def start_meta(block: Block) -> None:
        if not meta:
            meta["heading_path"] = _path_text(path) or None
            meta["page"] = block.page

    for block in blocks:
        if block.kind == HEADING:
            # 当前片已有一定内容时，标题另起一片，避免标题落在上一片的末尾
            if length() > size // 2:
                flush(carry=False)
            _update_path(path, block)
        pieces = split_long(block.text, size, overlap) if len(block.text) > size else [block.text]
        for piece in pieces:
            if parts and length() + 1 + len(piece) > size:
                flush(carry=True)
                if parts and len(parts[0]) + 1 + len(piece) > size:
                    parts = []  # 重叠部分加上新内容就超长时，放弃重叠
            start_meta(block)
            parts.append(piece)
            last_kind = block.kind
    flush(carry=False)
    return drafts


def _by_heading(blocks: list[Block], config: ChunkConfig) -> list[Draft]:
    """按标题切开，每个章节一片；章节超过目标字数时在章节内再按长度切。"""
    sections: list[tuple[list[tuple[int, str]], list[Block]]] = []
    path: list[tuple[int, str]] = []
    current: list[Block] = []
    current_path: list[tuple[int, str]] = []

    for block in blocks:
        if block.kind == HEADING and block.level <= config.heading_level:
            # 前一个章节只有标题、还没有正文时（如「第 2 章」紧接「2.1」），不切开，
            # 让上级标题和下级章节留在同一片里
            if any(b.kind != HEADING for b in current):
                sections.append((current_path, current))
                current = []
            if not current:
                current_path = list(path)  # 进入本章节之前的路径，本章节标题由块自己带入
        if block.kind == HEADING:
            _update_path(path, block)
        current.append(block)
    if current:
        sections.append((current_path, current))

    drafts: list[Draft] = []
    for section_path, section_blocks in sections:
        drafts.extend(_by_length(section_blocks, config, section_path))
    return drafts


def _by_separator(parsed: ParseResult, config: ChunkConfig) -> list[Draft]:
    separator = config.real_separator
    text = parsed.raw_text if parsed.raw_text is not None else "\n".join(b.text for b in parsed.blocks)
    parts = text.split(separator)
    # 分隔符保留在它后面那一片的开头；纯空白的分隔符不保留
    keep = separator if separator.strip() else ""
    pieces = [parts[0]] + [keep + p for p in parts[1:]]
    drafts = []
    for piece in (p.strip() for p in pieces):
        if piece and piece != keep.strip():
            drafts.extend(
                Draft(p, {"heading_path": None, "page": None}) for p in split_long(piece, HARD_MAX_CHARS)
            )
    return drafts
