# 召回接口使用说明

给接入知识库平台的应用开发者。下文的 `http://平台地址:8200` 换成实际部署的地址。

完整的接口定义（含所有字段）可以在浏览器里打开 `http://平台地址:8200/api/v1/docs` 查看。

## 平台做什么，不做什么

- **做**：校验权限，把问题转成向量，在你指定的知识库里取回相关的切片。
- **不做**：重排序、问题改写、多轮检索、生成答案。这些由应用自己决定怎么做。

拿到的是候选切片。要更高的准确率，建议多取一些（例如 20 条）再用重排序模型筛选。

## 1. 准备密钥

所有调用都要在请求头里带密钥：

```
Authorization: Bearer kb-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

密钥代表一个账号，能检索的知识库就是这个账号有权限的知识库。

| 用途 | 做法 |
|---|---|
| 应用长期使用 | 请平台管理员创建一个**服务账号**并为它创建密钥，再由各知识库的管理者把知识库授权给这个服务账号（或它所在的分组） |
| 个人写脚本、调试 | 登录管理界面，在「我的密钥」里自己创建 |

密钥只在创建时显示一次。泄露后在管理界面吊销即可，吊销立刻生效。

验证密钥是否可用：

```bash
curl http://平台地址:8200/open/v1/me -H "Authorization: Bearer $KB_KEY"
```

## 2. 查看能检索哪些知识库

```bash
curl http://平台地址:8200/open/v1/knowledge-bases -H "Authorization: Bearer $KB_KEY"
```

```json
[
  {"id": "3f2a…", "name": "调阻机常见问题", "description": "", "my_level": "read", "document_count": 12}
]
```

`id` 就是检索时要用的知识库编号。

## 3. 检索

```bash
curl -X POST http://平台地址:8200/open/v1/retrieval/search \
  -H "Authorization: Bearer $KB_KEY" -H "Content-Type: application/json" \
  -d '{"query": "划线机中转台卡料怎么处理", "kb_ids": ["3f2a…"], "top_k": 5}'
```

### 请求参数

| 参数 | 必填 | 说明 |
|---|---|---|
| `query` | 是 | 问题，最长 2000 字 |
| `kb_ids` | 是 | 要检索的知识库编号，1 到 10 个。**必须明确给出** |
| `mode` | 否 | `hybrid` 融合（默认）、`vector` 向量、`keyword` 关键字 |
| `top_k` | 否 | 返回条数，默认 10，最多 50 |
| `score_threshold` | 否 | 只在 `vector` 模式下有效，丢弃相似度低于该值的结果 |
| `filter.doc_ids` | 否 | 只在这些文档里检索 |
| `filter.tags` | 否 | 只检索标签全部相符的文档，如 `{"产品线": "划线机"}` |

### 三种检索方式怎么选

| 方式 | 特点 | 耗时参考 |
|---|---|---|
| `hybrid` | 向量和关键字各取一批再合并，两种都能命中的排在前面。一般用这个 | 约 100 毫秒 |
| `vector` | 按意思相近找，问法和原文用词不同也能找到；但对型号、报警码这类精确的词不敏感 | 约 100 毫秒 |
| `keyword` | 只返回真正包含查询词的切片，适合查型号、料号、报警码。可能一条也不返回 | 约 10 毫秒 |

关键字检索不区分大小写，型号可以只输入一部分：输入 `ydflp` 能找到含 `YDFLP-20-M8` 的内容。

### 响应

```json
{
  "mode": "hybrid",
  "score_type": "rrf",
  "took_ms": 104,
  "results": [
    {
      "chunk_id": "9c1e…",
      "kb_id": "3f2a…",
      "kb_name": "调阻机常见问题",
      "doc_id": "77ab…",
      "doc_name": "调阻机常见问题.txt",
      "seq": 0,
      "content": "## query：200W红外划线机中转台尺寸偏小导致卡料，该如何排查处理？\n answer：…",
      "score": 0.0328,
      "metadata": {"heading_path": null, "page": null, "tags": {"产品线": "划线机"}}
    }
  ]
}
```

| 字段 | 说明 |
|---|---|
| `content` | 切片原文 |
| `seq` | 切片在文档中的序号，从 0 开始 |
| `metadata.heading_path` | 所在章节的标题路径，如「3. 软件 > 3.1 使用说明」；没有则为空 |
| `metadata.page` | 所在页码，只有 PDF 有 |
| `metadata.tags` | 文档上传时填写的标签 |
| `score_type` | 这次 `score` 的含义，见下表 |

### 分数的含义

| `score_type` | 什么时候出现 | 含义 |
|---|---|---|
| `cosine` | `vector` 模式 | 余弦相似度，越接近 1 越相似。可以跨请求比较，可以设阈值 |
| `bm25` | `keyword` 模式且只检索一个知识库 | 关键字相关度，只能在这一次结果内比较大小 |
| `rrf` | `hybrid` 模式，或 `keyword` 模式检索多个知识库 | 按名次合并得到，只表示先后顺序，数值本身没有意义 |

**同时检索多个知识库时**：`vector` 模式按相似度统一排序；`hybrid` 和 `keyword` 模式按各库内的名次合并，即每个库的第 1 名排在前面，然后是各库的第 2 名，同名次按 `kb_ids` 里的先后顺序。需要更精确的跨库排序，请在应用里做重排序。

## 4. 取切片的上下文

检索到的切片内容不完整时，可以取它前后相邻的切片：

```bash
curl "http://平台地址:8200/open/v1/knowledge-bases/3f2a…/chunks/9c1e…/neighbors?before=1&after=1" \
  -H "Authorization: Bearer $KB_KEY"
```

返回 `before`、`chunk`、`after` 三部分，`before` 和 `after` 各最多 5 条。

也可以按编号取单个切片：`GET /open/v1/knowledge-bases/{kb_id}/chunks/{chunk_id}`。

## 5. 错误

错误响应的格式统一为：

```json
{"code": "KB_FORBIDDEN", "message": "以下知识库不存在或没有权限", "detail": {"kb_ids": ["…"]}}
```

| HTTP 状态 | `code` | 原因与处理 |
|---|---|---|
| 400 | `INVALID_PARAM` | 参数不对，`detail.errors` 里有具体字段 |
| 401 | `INVALID_KEY` | 密钥不存在、已过期、已吊销，或所属账号已停用 |
| 403 | `KB_FORBIDDEN` | `kb_ids` 里有不存在或没有权限的知识库，`detail.kb_ids` 列出是哪些。**只要有一个不行，整个请求都被拒绝**，不会只返回有权限的那部分 |
| 404 | `NOT_FOUND` | 切片不存在，或已被停用、删除 |
| 409 | `KB_REBUILDING` | 知识库正在重建索引，稍后再试 |
| 409 | `KB_NEEDS_REBUILD` | 平台更换过向量模型，这个知识库还没重建索引，不能做向量检索；可以先用 `keyword` 模式 |
| 503 | `EMBEDDING_UNAVAILABLE` | 向量模型服务暂时不可用或被限流。可以稍后重试，或改用 `keyword` 模式 |
| 503 | `VECTOR_STORE_UNAVAILABLE` | 向量库暂时不可用 |

## 6. 接入时的注意事项

- **停用和删除立刻生效**。管理者停用或删除的切片，下一次检索就不会返回，按编号也取不到。应用不要长期缓存检索结果。
- **权限变更立刻生效**。授权被取消后，原来能查的知识库会返回 `KB_FORBIDDEN`。
- **向量模型有限流**。`vector` 和 `hybrid` 模式每次请求都要调用一次向量模型，短时间内大量请求会触发限流并返回 503。批量任务请控制频率，或使用 `keyword` 模式。
- **每次调用都有记录**。平台会记录调用的账号、知识库、问题文本、耗时和返回条数，保留 30 天，知识库的管理者和平台管理员可以查看。

## Python 示例

```python
import httpx

BASE = "http://平台地址:8200/open/v1"
HEADERS = {"Authorization": "Bearer kb-…"}

def search(query: str, kb_ids: list[str], top_k: int = 10, mode: str = "hybrid") -> list[dict]:
    response = httpx.post(
        f"{BASE}/retrieval/search",
        headers=HEADERS,
        json={"query": query, "kb_ids": kb_ids, "top_k": top_k, "mode": mode},
        timeout=30,
    )
    if response.status_code != 200:
        error = response.json()
        raise RuntimeError(f"检索失败：{error['code']} {error['message']}")
    return response.json()["results"]

for hit in search("划线机中转台卡料怎么处理", ["3f2a…"], top_k=5):
    print(hit["doc_name"], hit["seq"], hit["content"][:60])
```
