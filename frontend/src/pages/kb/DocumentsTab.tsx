import { ReloadOutlined, UploadOutlined } from "@ant-design/icons";
import { App, Button, Dropdown, Input, Select, Space, Switch, Table, Tag } from "antd";
import dayjs from "dayjs";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { canEdit, Doc, errorText, fileSize, http, Kb, MODE_TEXT, Page, STATUS_TEXT } from "../../api";
import StatusTag from "../../components/StatusTag";
import UploadModal from "../../components/UploadModal";

const BUSY = ["parsing", "embedding"];

export default function DocumentsTab({ kb, onChanged }: { kb: Kb; onChanged: () => void }) {
  const { message, modal } = App.useApp();
  const [data, setData] = useState<Page<Doc>>({ total: 0, items: [] });
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState({ page: 1, keyword: "", status: undefined as string | undefined });
  const [uploading, setUploading] = useState(false);
  const [replacing, setReplacing] = useState<Doc | undefined>();
  const editable = canEdit(kb.my_level);
  const timer = useRef<number>();

  const load = useCallback(
    async (quiet = false) => {
      if (!quiet) setLoading(true);
      try {
        const { data: page } = await http.get<Page<Doc>>(`/knowledge-bases/${kb.id}/documents`, {
          params: { page: query.page, page_size: 20, keyword: query.keyword, status: query.status },
        });
        setData(page);
      } catch (e) {
        if (!quiet) message.error(errorText(e));
      } finally {
        setLoading(false);
      }
    },
    [kb.id, query, message],
  );

  useEffect(() => {
    void load();
  }, [load]);

  // 有文档在处理中时，每两秒刷新一次，直到全部处理完
  useEffect(() => {
    window.clearTimeout(timer.current);
    if (data.items.some((d) => BUSY.includes(d.status))) {
      timer.current = window.setTimeout(() => void load(true), 2000);
    }
    return () => window.clearTimeout(timer.current);
  }, [data, load]);

  const act = async (run: () => Promise<unknown>, done: string) => {
    try {
      await run();
      message.success(done);
      await load(true);
      onChanged();
    } catch (e) {
      message.error(errorText(e));
    }
  };

  const remove = (doc: Doc) =>
    modal.confirm({
      title: `删除「${doc.name}」？`,
      content: "文档的全部切片和原始文件会一起删除，不能恢复。",
      okText: "删除",
      okButtonProps: { danger: true },
      onOk: () => act(() => http.delete(`/documents/${doc.id}`), "文档已删除"),
    });

  return (
    <>
      <Space style={{ marginBottom: 16, width: "100%", justifyContent: "space-between" }} wrap>
        <Space wrap>
          <Input.Search
            allowClear
            placeholder="按文件名查找"
            style={{ width: 240 }}
            onSearch={(keyword) => setQuery((q) => ({ ...q, page: 1, keyword }))}
          />
          <Select
            allowClear
            placeholder="全部状态"
            style={{ width: 130 }}
            options={Object.entries(STATUS_TEXT).map(([value, label]) => ({ value, label }))}
            onChange={(status) => setQuery((q) => ({ ...q, page: 1, status }))}
          />
          <Button icon={<ReloadOutlined />} onClick={() => load()} aria-label="刷新" />
        </Space>
        {editable && (
          <Button type="primary" icon={<UploadOutlined />} onClick={() => setUploading(true)}>
            上传文档
          </Button>
        )}
      </Space>

      <Table<Doc>
        rowKey="id"
        loading={loading}
        dataSource={data.items}
        locale={{ emptyText: editable ? "还没有文档，上传第一份。" : "这个知识库里还没有文档。" }}
        pagination={{
          current: query.page,
          total: data.total,
          pageSize: 20,
          showSizeChanger: false,
          showTotal: (t) => `共 ${t} 份`,
          onChange: (page) => setQuery((q) => ({ ...q, page })),
        }}
        columns={[
          {
            title: "文件",
            render: (_, doc) => (
              <div>
                <Link to={`/doc/${doc.id}`}>{doc.name}</Link>
                <div className="muted" style={{ fontSize: 12 }}>
                  {fileSize(doc.file_size)}，{MODE_TEXT[doc.chunk_config.effective_mode ?? doc.chunk_config.mode]}
                  {Object.entries(doc.tags).map(([k, v]) => (
                    <Tag key={k} style={{ marginLeft: 6, marginRight: 0 }}>
                      {k}：{v}
                    </Tag>
                  ))}
                </div>
              </div>
            ),
          },
          { title: "状态", width: 170, render: (_, doc) => <StatusTag doc={doc} /> },
          { title: "切片", dataIndex: "chunk_count", width: 80, align: "right", className: "num" },
          {
            title: "参与检索",
            width: 100,
            render: (_, doc) => (
              <Switch
                size="small"
                checked={doc.enabled}
                disabled={!editable || BUSY.includes(doc.status)}
                aria-label={`${doc.name} 是否参与检索`}
                onChange={(enabled) =>
                  act(() => http.patch(`/documents/${doc.id}`, { enabled }), enabled ? "已启用" : "已停用，不再参与检索")
                }
              />
            ),
          },
          {
            title: "更新时间",
            width: 150,
            render: (_, doc) => <span className="num">{dayjs(doc.updated_at).format("MM-DD HH:mm")}</span>,
          },
          {
            title: "",
            width: 150,
            render: (_, doc) => (
              <Space size={4}>
                <Link to={`/doc/${doc.id}`}>{doc.status === "pending_confirm" ? "预览并确认" : "查看切片"}</Link>
                {editable && (
                  <Dropdown
                    menu={{
                      items: [
                        { key: "replace", label: "用新文件替换", disabled: doc.status !== "ready", onClick: () => setReplacing(doc) },
                        {
                          key: "retry",
                          label: "重试",
                          disabled: doc.status !== "failed",
                          onClick: () => act(() => http.post(`/documents/${doc.id}/retry`), "已重新开始处理"),
                        },
                        { type: "divider" },
                        { key: "delete", label: "删除", danger: true, disabled: BUSY.includes(doc.status), onClick: () => remove(doc) },
                      ],
                    }}
                  >
                    <Button type="link" size="small">
                      更多
                    </Button>
                  </Dropdown>
                )}
              </Space>
            ),
          },
        ]}
      />

      <UploadModal
        open={uploading || Boolean(replacing)}
        kbId={kb.id}
        defaultConfig={kb.chunk_config}
        replacing={replacing}
        onClose={() => {
          setUploading(false);
          setReplacing(undefined);
        }}
        onDone={() => {
          void load(true);
          onChanged();
        }}
      />
    </>
  );
}
