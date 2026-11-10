"""调用向量模型（OpenAI 兼容接口）。入库和查询都走这里，保证用的是同一个模型。"""

import logging
import time

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

_RETRY_STATUS = {429, 500, 502, 503, 504}
_MAX_WAIT_SECONDS = 30
# 查询时有人在等结果：只做几次很短的重试，仍不行就尽快报错，由调用方决定是否改用关键字检索
_QUERY_WAITS = (0.2, 0.5, 1.0)
_QUERY_TIMEOUT = 10.0


class EmbeddingError(Exception):
    """向量模型不可用或返回异常。message 会显示给操作者，不包含密钥。"""


def embed_query(text: str) -> list[float]:
    """把查询文本转成向量。与入库用同一个模型，但重试等待很短。"""
    return _embed([text], waits=_QUERY_WAITS, timeout=_QUERY_TIMEOUT)[0]


def embed_texts(texts: list[str]) -> list[list[float]]:
    """把一批文本转成向量，顺序与输入一致。用于入库：按配置分批请求，遇到限流或临时故障耐心重试。"""
    waits = tuple(
        min(2**attempt, _MAX_WAIT_SECONDS) for attempt in range(1, get_settings().EMBEDDING_MAX_RETRIES + 1)
    )
    return _embed(texts, waits=waits, timeout=120.0)


def _embed(texts: list[str], *, waits: tuple[float, ...], timeout: float) -> list[list[float]]:
    settings = get_settings()
    if not settings.EMBEDDING_URL or not settings.EMBEDDING_API_KEY:
        raise EmbeddingError("向量模型尚未配置，请在配置文件中填写 EMBEDDING_URL 和 EMBEDDING_API_KEY")
    if not texts:
        return []

    headers = {"Authorization": f"Bearer {settings.EMBEDDING_API_KEY}"}
    vectors: list[list[float]] = []
    with httpx.Client(timeout=timeout) as client:
        for start in range(0, len(texts), settings.EMBEDDING_BATCH_SIZE):
            batch = texts[start : start + settings.EMBEDDING_BATCH_SIZE]
            vectors.extend(_embed_batch(client, headers, batch, waits))
    return vectors


def _embed_batch(
    client: httpx.Client, headers: dict[str, str], batch: list[str], waits: tuple[float, ...]
) -> list[list[float]]:
    """请求一批。waits 是每次重试前等待的秒数，长度即最多重试的次数。"""
    settings = get_settings()
    payload = {"model": settings.EMBEDDING_MODEL, "input": batch}
    last_error = ""
    for attempt in range(len(waits) + 1):
        if attempt:
            wait = waits[attempt - 1]
            logger.warning("向量接口第 %s 次重试，%s 秒后进行：%s", attempt, wait, last_error)
            time.sleep(wait)
        try:
            response = client.post(settings.EMBEDDING_URL, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            last_error = f"网络错误：{type(exc).__name__}"
            continue
        if response.status_code in _RETRY_STATUS:
            last_error = f"HTTP {response.status_code}"
            continue
        if response.status_code != 200:
            raise EmbeddingError(f"向量接口返回 HTTP {response.status_code}：{response.text[:200]}")
        return _extract(response, len(batch))
    raise EmbeddingError(f"向量接口连续失败 {len(waits) + 1} 次，最后一次：{last_error}")


def _extract(response: httpx.Response, expected: int) -> list[list[float]]:
    dim = get_settings().EMBEDDING_DIM
    try:
        items = sorted(response.json()["data"], key=lambda item: item.get("index", 0))
        vectors = [item["embedding"] for item in items]
    except (ValueError, KeyError, TypeError) as exc:
        raise EmbeddingError(f"向量接口返回的内容无法识别：{response.text[:200]}") from exc
    if len(vectors) != expected:
        raise EmbeddingError(f"向量接口返回 {len(vectors)} 条，与请求的 {expected} 条不符")
    if any(len(v) != dim for v in vectors):
        raise EmbeddingError(f"向量维度是 {len(vectors[0])}，与配置的 EMBEDDING_DIM={dim} 不一致")
    return vectors
