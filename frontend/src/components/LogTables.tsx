// 操作日志和检索记录的表格。kbId 给出时只看这个知识库的。
import { App, DatePicker, Input, Select, Space, Table, Tag, Typography } from "antd";
import dayjs, { Dayjs } from "dayjs";
import { useCallback, useEffect, useState } from "react";
import { errorText, http, Page } from "../api";

interface OperationLog {
  id: string;
  created_at: string;
  account_name: string;
  action: string;
  detail: Record<string, unknown>;
}

interface RetrievalLog {
  id: string;
  created_at: string;
  account_name: string;
  via_api_key: boolean;
  query: string;
  mode: string;
  top_k: number;
  result_count: number;
  took_ms: number;
  status: string;
  error_code: string | null;
}

const ACTION_TEXT: Record<string, string> = {
  "account.create": "创建账号",
  "account.update": "修改账号",
  "account.reset_password": "重置密码",
  "account.import": "批量导入账号",
  "auth.change_password": "修改密码",
  "key.create": "创建密钥",
  "key.revoke": "吊销密钥",
  "group.create": "创建分组",
  "group.update": "修改分组",
  "group.delete": "删除分组",
  "group.member_add": "添加分组成员",
  "group.member_remove": "移除分组成员",
  "kb.create": "创建知识库",
  "kb.update": "修改知识库",
  "kb.delete": "删除知识库",
  "kb.rebuild": "重建索引",
  "kb.grant": "授权",
  "kb.revoke": "取消授权",
  "doc.upload": "上传文档",
  "doc.rechunk": "重新切分",
  "doc.confirm": "确认入库",
  "doc.retry": "重试",
  "doc.replace": "替换文档",
  "doc.cancel_replace": "取消新版本",
  "doc.update": "修改文档",
  "doc.delete": "删除文档",
  "chunk.update": "修改切片",
  "chunk.create": "新增切片",
  "chunk.delete": "删除切片",
};

const ACTION_GROUPS = [
  { value: "doc", label: "文档" },
  { value: "chunk", label: "切片" },
  { value: "kb", label: "知识库与授权" },
  { value: "account", label: "账号" },
  { value: "group", label: "分组" },
  { value: "key", label: "密钥" },
];

const MODE: Record<string, string> = { vector: "向量", keyword: "关键字", hybrid: "融合" };
type Range = [Dayjs | null, Dayjs | null] | null;

function rangeParams(range: Range) {
  return {
    start: range?.[0]?.startOf("day").toISOString(),
    end: range?.[1]?.add(1, "day").startOf("day").toISOString(),
  };
}

function useLogs<T>(path: string, params: Record<string, unknown>) {
  const { message } = App.useApp();
  const [page, setPage] = useState(1);
  const [data, setData] = useState<Page<T>>({ total: 0, items: [] });
  const [loading, setLoading] = useState(true);
  const key = JSON.stringify(params);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData((await http.get<Page<T>>(path, { params: { ...JSON.parse(key), page, page_size: 20 } })).data);
    } catch (e) {
      message.error(errorText(e));
    } finally {
      setLoading(false);
    }
  }, [path, key, page, message]);

  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => setPage(1), [key]);

  const pagination = {
    current: page,
    total: data.total,
    pageSize: 20,
    showSizeChanger: false,
    showTotal: (t: number) => `共 ${t} 条`,
    onChange: setPage,
  };
  return { data, loading, pagination };
}

const time = (value: string) => <span className="num">{dayjs(value).format("MM-DD HH:mm:ss")}</span>;

export function OperationLogTable({ kbId }: { kbId?: string }) {
  const [action, setAction] = useState<string>();
  const [range, setRange] = useState<Range>(null);
  const { data, loading, pagination } = useLogs<OperationLog>("/logs/operations", {
    kb_id: kbId,
    action,
    ...rangeParams(range),
  });
  return (
    <>
      <Space style={{ marginBottom: 16 }} wrap>
        <Select
          allowClear
          placeholder="全部操作"
          style={{ width: 160 }}
          options={kbId ? ACTION_GROUPS.slice(0, 3) : ACTION_GROUPS}
          onChange={setAction}
        />
        <DatePicker.RangePicker onChange={(v) => setRange(v as Range)} />
      </Space>
      <Table<OperationLog>
        rowKey="id"
        size="middle"
        loading={loading}
        dataSource={data.items}
        pagination={pagination}
        columns={[
          { title: "时间", width: 150, render: (_, x) => time(x.created_at) },
          { title: "操作人", dataIndex: "account_name", width: 180 },
          { title: "操作", width: 140, render: (_, x) => ACTION_TEXT[x.action] ?? x.action },
          {
            title: "内容",
            render: (_, x) => (
              <Typography.Paragraph
                type="secondary"
                style={{ margin: 0, fontSize: 13 }}
                ellipsis={{ rows: 2, expandable: "collapsible" }}
              >
                {describe(x.detail)}
              </Typography.Paragraph>
            ),
          },
        ]}
      />
    </>
  );
}

const DETAIL_NAMES: Record<string, string> = {
  name: "名称",
  username: "登录名",
  document: "文档",
  seq: "序号",
  before: "修改前",
  after: "修改后",
  content: "内容",
  enabled: "启用",
  subject: "对象",
  level: "权限",
  size: "大小",
  chunks: "切片数",
  documents: "文档数",
  old_name: "原文件",
  new_name: "新文件",
  stage: "步骤",
  owner: "所属账号",
  chunk_config: "切分设置",
  subject_type: "对象类型",
  account_type: "账号类型",
  is_admin: "管理员",
};

function describe(detail: Record<string, unknown>): string {
  return Object.entries(detail)
    .map(([k, v]) => `${DETAIL_NAMES[k] ?? k}：${typeof v === "object" && v !== null ? JSON.stringify(v) : String(v)}`)
    .join("；");
}

export function RetrievalLogTable({ kbId }: { kbId?: string }) {
  const [status, setStatus] = useState<string>();
  const [keyword, setKeyword] = useState("");
  const [range, setRange] = useState<Range>(null);
  const { data, loading, pagination } = useLogs<RetrievalLog>("/logs/retrievals", {
    kb_id: kbId,
    status,
    keyword,
    ...rangeParams(range),
  });
  return (
    <>
      <Space style={{ marginBottom: 16 }} wrap>
        <Input.Search allowClear placeholder="查询文本包含" style={{ width: 220 }} onSearch={setKeyword} />
        <Select
          allowClear
          placeholder="全部结果"
          style={{ width: 130 }}
          options={[
            { value: "ok", label: "成功" },
            { value: "forbidden", label: "无权限" },
            { value: "error", label: "出错" },
          ]}
          onChange={setStatus}
        />
        <DatePicker.RangePicker onChange={(v) => setRange(v as Range)} />
      </Space>
      <Table<RetrievalLog>
        rowKey="id"
        size="middle"
        loading={loading}
        dataSource={data.items}
        pagination={pagination}
        columns={[
          { title: "时间", width: 150, render: (_, x) => time(x.created_at) },
          {
            title: "调用者",
            width: 200,
            render: (_, x) => (
              <>
                {x.account_name}
                {!x.via_api_key && <Tag style={{ marginLeft: 6 }}>召回测试</Tag>}
              </>
            ),
          },
          { title: "查询文本", dataIndex: "query", ellipsis: true },
          { title: "方式", width: 80, render: (_, x) => MODE[x.mode] ?? x.mode },
          { title: "返回", width: 70, align: "right", render: (_, x) => <span className="num">{x.result_count}</span> },
          { title: "耗时", width: 90, align: "right", render: (_, x) => <span className="num">{x.took_ms} 毫秒</span> },
          {
            title: "结果",
            width: 100,
            render: (_, x) =>
              x.status === "ok" ? (
                <Tag color="green">成功</Tag>
              ) : (
                <Tag color={x.status === "forbidden" ? "orange" : "red"} title={x.error_code ?? ""}>
                  {x.status === "forbidden" ? "无权限" : "出错"}
                </Tag>
              ),
          },
        ]}
      />
    </>
  );
}
