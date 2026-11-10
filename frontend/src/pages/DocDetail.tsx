import { PlusOutlined } from "@ant-design/icons";
import { Alert, App, Breadcrumb, Button, Form, Input, Modal, Pagination, Result, Segmented, Space, Spin, Switch, Tag } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  canEdit,
  Chunk,
  Doc,
  errorCode,
  errorDetail,
  errorText,
  fileSize,
  http,
  Kb,
  MODE_TEXT,
} from "../api";
import ChunkConfigFields from "../components/ChunkConfigFields";
import StatusTag from "../components/StatusTag";
import UploadModal from "../components/UploadModal";

const PAGE_SIZE = 30;
type View = "pending" | "active";
interface ChunkPage {
  total: number;
  view: View;
  items: Chunk[];
}

export default function DocDetail() {
  const { docId = "" } = useParams();
  const { message, modal } = App.useApp();
  const [doc, setDoc] = useState<Doc | null>(null);
  const [kb, setKb] = useState<Kb | null>(null);
  const [error, setError] = useState("");
  const [chunks, setChunks] = useState<ChunkPage | null>(null);
  const [view, setView] = useState<View | undefined>();
  const [page, setPage] = useState(1);
  const [editing, setEditing] = useState<{ chunk?: Chunk; position?: number } | null>(null);
  const [rechunking, setRechunking] = useState(false);
  const [replacing, setReplacing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [editForm] = Form.useForm();
  const [rechunkForm] = Form.useForm();
  const timer = useRef<number>();

  const loadDoc = useCallback(async () => {
    try {
      const { data } = await http.get<Doc>(`/documents/${docId}`);
      setDoc(data);
      setKb((prev) => prev ?? null);
      if (!kb || kb.id !== data.kb_id) setKb((await http.get<Kb>(`/knowledge-bases/${data.kb_id}`)).data);
      return data;
    } catch (e) {
      setError(errorText(e));
      return null;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docId]);

  const loadChunks = useCallback(async () => {
    try {
      const { data } = await http.get<ChunkPage>(`/documents/${docId}/chunks`, {
        params: { view, page, page_size: PAGE_SIZE },
      });
      setChunks(data);
    } catch (e) {
      message.error(errorText(e));
    }
  }, [docId, view, page, message]);

  const reload = useCallback(async () => {
    await loadDoc();
    await loadChunks();
  }, [loadDoc, loadChunks]);

  useEffect(() => {
    void reload();
  }, [reload]);

  // 处理中时定时刷新；处理结束后回到默认视图
  useEffect(() => {
    window.clearTimeout(timer.current);
    if (doc && (doc.status === "parsing" || doc.status === "embedding")) {
      timer.current = window.setTimeout(async () => {
        const next = await loadDoc();
        if (next && next.status !== doc.status) {
          setView(undefined);
          setPage(1);
          await loadChunks();
        }
      }, 1500);
    }
    return () => window.clearTimeout(timer.current);
  }, [doc, loadDoc, loadChunks]);

  if (error) return <Result status="warning" title={error} extra={<Link to="/kb">回到知识库列表</Link>} />;
  if (!doc || !kb) return <Spin style={{ margin: 48 }} />;

  const editable = canEdit(kb.my_level);
  const shown: View = chunks?.view ?? "active";
  // 能改的只有两种：已入库且文档可用；或待确认的新版本
  const chunksEditable =
    editable && ((shown === "active" && doc.status === "ready") || (shown === "pending" && doc.status === "pending_confirm"));

  const act = async (run: () => Promise<unknown>, done: string) => {
    setBusy(true);
    try {
      await run();
      message.success(done);
      setView(undefined);
      await reload();
    } catch (e) {
      message.error(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const saveChunk = async () => {
    const { content } = await editForm.validateFields();
    setBusy(true);
    try {
      if (editing?.chunk) await http.patch(`/chunks/${editing.chunk.id}`, { content });
      else await http.post(`/documents/${doc.id}/chunks`, { content, position: editing?.position });
      message.success(editing?.chunk ? "切片已修改" : "切片已添加");
      setEditing(null);
      await reload();
    } catch (e) {
      message.error(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const toggleChunk = async (chunk: Chunk, enabled: boolean) => {
    try {
      await http.patch(`/chunks/${chunk.id}`, { enabled });
      message.success(enabled ? "已启用" : "已停用，不再参与检索");
      await loadChunks();
    } catch (e) {
      message.error(errorText(e));
    }
  };

  const removeChunk = (chunk: Chunk) =>
    modal.confirm({
      title: `删除第 ${chunk.seq + 1} 片？`,
      content: "删除后不能恢复。只是暂时不想让它被搜到，可以改用停用。",
      okText: "删除",
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await http.delete(`/chunks/${chunk.id}`);
          message.success("切片已删除");
          await reload();
        } catch (e) {
          message.error(errorText(e));
        }
      },
    });

  const submitRechunk = async (discardEdits = false) => {
    const values = await rechunkForm.validateFields();
    setBusy(true);
    try {
      await http.post(`/documents/${doc.id}/rechunk`, { chunk_config: values.chunk_config, discard_edits: discardEdits });
      message.success("正在按新设置重新切分");
      setRechunking(false);
      setView(undefined);
      setPage(1);
      await reload();
    } catch (e) {
      if (errorCode(e) === "EDITS_WILL_BE_LOST") {
        modal.confirm({
          title: "这份文档有手工修改过的切片",
          content: `已入库的版本里有 ${errorDetail(e).edited_chunks} 个手工修改或新增的切片，重新切分并入库后它们将丢失。`,
          okText: "丢弃手工修改并重新切分",
          okButtonProps: { danger: true },
          cancelText: "不重新切分",
          onOk: () => submitRechunk(true),
        });
      } else {
        message.error(errorText(e));
      }
    } finally {
      setBusy(false);
    }
  };

  const mode = doc.chunk_config.effective_mode ?? doc.chunk_config.mode;
  const fellBack = doc.chunk_config.mode === "heading" && doc.chunk_config.effective_mode === "length";

  return (
    <div className="page">
      <Breadcrumb
        style={{ marginBottom: 12 }}
        items={[
          { title: <Link to="/kb">知识库</Link> },
          { title: <Link to={`/kb/${kb.id}`}>{kb.name}</Link> },
          { title: doc.name },
        ]}
      />
      <div className="page-head">
        <div>
          <h1>{doc.name}</h1>
          <p>
            <StatusTag doc={doc} />
            <span style={{ marginLeft: 12 }}>
              {fileSize(doc.file_size)}，{MODE_TEXT[mode]}，共 <span className="num">{doc.chunk_count}</span> 片
            </span>
            {Object.entries(doc.tags).map(([k, v]) => (
              <Tag key={k} style={{ marginLeft: 8 }}>
                {k}：{v}
              </Tag>
            ))}
          </p>
        </div>
        {editable && (
          <Space wrap>
            {doc.status === "pending_confirm" && (
              <Button type="primary" loading={busy} onClick={() => act(() => http.post(`/documents/${doc.id}/confirm`), "已确认，正在入库")}>
                确认入库
              </Button>
            )}
            {doc.status === "failed" && (
              <Button type="primary" loading={busy} onClick={() => act(() => http.post(`/documents/${doc.id}/retry`), "已重新开始处理")}>
                重试
              </Button>
            )}
            {["pending_confirm", "failed", "ready"].includes(doc.status) && (
              <Button onClick={() => setRechunking(true)}>重新切分</Button>
            )}
            {doc.status === "ready" && <Button onClick={() => setReplacing(true)}>用新文件替换</Button>}
            {doc.replacing && ["pending_confirm", "failed"].includes(doc.status) && (
              <Button loading={busy} onClick={() => act(() => http.post(`/documents/${doc.id}/cancel-replace`), "已放弃新版本")}>
                放弃新版本
              </Button>
            )}
          </Space>
        )}
      </div>

      {doc.status === "failed" && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          message={`${doc.failed_stage === "embed" ? "入库" : "解析"}失败：${doc.error_message ?? "原因未知"}`}
          description={doc.replacing ? "已入库的版本不受影响，照常可以检索。可以重试，也可以放弃新版本。" : undefined}
        />
      )}
      {doc.status === "pending_confirm" && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message={
            doc.replacing
              ? "新版本已切分好，还没有入库。现在能搜到的仍是原来的版本。检查下面的切片，无误后确认入库。"
              : "已切分好，还没有入库，现在搜不到。检查下面的切片，无误后确认入库；切得不对可以重新切分，也可以直接修改个别切片。"
          }
        />
      )}
      {fellBack && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="这份文档里没有识别到标题，已改为按长度切分。"
        />
      )}

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", margin: "8px 0 16px" }}>
        {doc.replacing && doc.status !== "parsing" ? (
          <Segmented<View>
            value={shown}
            onChange={(v) => {
              setView(v);
              setPage(1);
            }}
            options={[
              { value: "pending", label: "新版本（未入库）" },
              { value: "active", label: "已入库的版本" },
            ]}
          />
        ) : (
          <span />
        )}
        <span className="muted num">{chunks ? `共 ${chunks.total} 片` : ""}</span>
      </div>

      {doc.status === "parsing" ? (
        <Spin tip="正在解析和切分…" style={{ margin: 48 }}>
          <div style={{ height: 80 }} />
        </Spin>
      ) : !chunks ? (
        <Spin style={{ margin: 48 }} />
      ) : (
        <>
          <div className="chunk-list">
            {chunks.items.map((chunk) => (
              <div key={chunk.id} className={`chunk${chunk.enabled ? "" : " is-disabled"}`}>
                <span className="chunk-seq">{chunk.seq + 1}</span>
                <div className="chunk-body">
                  <div className="chunk-meta">
                    <span className="grow">
                      {chunk.metadata.heading_path && <span>{chunk.metadata.heading_path}</span>}
                      {chunk.metadata.page != null && <span style={{ marginLeft: 8 }}>第 {chunk.metadata.page} 页</span>}
                      {!chunk.metadata.heading_path && chunk.metadata.page == null && <span>{chunk.char_count} 字</span>}
                    </span>
                    {chunk.origin === "manual" && <Tag color="blue">手工新增</Tag>}
                    {chunk.is_edited && <Tag color="blue">已修改</Tag>}
                    {!chunk.enabled && <Tag>已停用</Tag>}
                    {chunksEditable && (
                      <Space size={2}>
                        <Switch
                          size="small"
                          checked={chunk.enabled}
                          aria-label={`第 ${chunk.seq + 1} 片是否参与检索`}
                          onChange={(v) => toggleChunk(chunk, v)}
                        />
                        <Button
                          type="link"
                          size="small"
                          onClick={() => {
                            setEditing({ chunk });
                            editForm.setFieldsValue({ content: chunk.content });
                          }}
                        >
                          修改
                        </Button>
                        <Button type="link" size="small" danger onClick={() => removeChunk(chunk)}>
                          删除
                        </Button>
                      </Space>
                    )}
                  </div>
                  <pre className="chunk-text">{chunk.content}</pre>
                </div>
                {chunksEditable && (
                  <Button
                    type="text"
                    size="small"
                    icon={<PlusOutlined />}
                    className="muted"
                    style={{ marginTop: 6 }}
                    onClick={() => {
                      setEditing({ position: chunk.seq + 1 });
                      editForm.setFieldsValue({ content: "" });
                    }}
                  >
                    在这一片后面添加
                  </Button>
                )}
              </div>
            ))}
          </div>
          {chunks.total === 0 && <p className="muted">这个版本没有切片。</p>}
          {chunks.total > PAGE_SIZE && (
            <Pagination
              style={{ marginTop: 8, textAlign: "right" }}
              current={page}
              total={chunks.total}
              pageSize={PAGE_SIZE}
              showSizeChanger={false}
              onChange={setPage}
            />
          )}
        </>
      )}

      <Modal
        title={editing?.chunk ? `修改第 ${editing.chunk.seq + 1} 片` : "添加切片"}
        open={Boolean(editing)}
        onCancel={() => setEditing(null)}
        onOk={saveChunk}
        okText="保存"
        confirmLoading={busy}
        width={720}
      >
        <Form form={editForm} layout="vertical">
          <Form.Item
            name="content"
            rules={[{ required: true, whitespace: true, message: "请填写内容" }]}
            extra={shown === "active" ? "保存后立刻生效，下一次检索就按新内容。" : "保存后只是改了预览，确认入库后才能搜到。"}
          >
            <Input.TextArea autoSize={{ minRows: 8, maxRows: 22 }} maxLength={4000} showCount />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="重新切分"
        open={rechunking}
        onCancel={() => setRechunking(false)}
        onOk={() => submitRechunk()}
        okText="重新切分"
        confirmLoading={busy}
        destroyOnHidden
      >
        <p className="muted">
          {doc.searchable
            ? "用原文件按新设置切一遍，先给你预览。确认入库之前，现在的版本照常可以检索。"
            : "按新设置重新切一遍，之后可以再预览。"}
        </p>
        <Form form={rechunkForm} layout="vertical" preserve={false} initialValues={{ chunk_config: { ...doc.chunk_config } }}>
          <ChunkConfigFields />
        </Form>
      </Modal>

      <UploadModal
        open={replacing}
        kbId={kb.id}
        defaultConfig={kb.chunk_config}
        replacing={doc}
        onClose={() => setReplacing(false)}
        onDone={() => {
          setView(undefined);
          setPage(1);
          void reload();
        }}
      />
    </div>
  );
}
