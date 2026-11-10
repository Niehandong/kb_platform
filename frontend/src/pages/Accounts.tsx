import { DownloadOutlined, PlusOutlined, UploadOutlined } from "@ant-design/icons";
import { App, Button, Checkbox, Drawer, Dropdown, Form, Input, Modal, Radio, Select, Space, Table, Tag, Typography, Upload } from "antd";
import dayjs from "dayjs";
import { useCallback, useEffect, useState } from "react";
import { Account, errorText, http, Page } from "../api";
import { useAuth } from "../auth";
import KeyManager from "../components/KeyManager";

interface ImportResult {
  created: { row: number; username: string; display_name: string; initial_password: string }[];
  failed: { row: number; username: string; reason: string }[];
}

export default function Accounts() {
  const { account: me } = useAuth();
  const { message, modal } = App.useApp();
  const [data, setData] = useState<Page<Account>>({ total: 0, items: [] });
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState({ page: 1, keyword: "", account_type: undefined as string | undefined });
  const [creating, setCreating] = useState(false);
  const [keysOf, setKeysOf] = useState<Account | null>(null);
  const [imported, setImported] = useState<ImportResult | null>(null);
  const [form] = Form.useForm();
  const type = Form.useWatch("account_type", form);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData((await http.get<Page<Account>>("/accounts", { params: { ...query, page_size: 20 } })).data);
    } catch (e) {
      message.error(errorText(e));
    } finally {
      setLoading(false);
    }
  }, [query, message]);

  useEffect(() => {
    void load();
  }, [load]);

  /** 一次性密码只显示这一次，用不能随手关掉的弹窗 */
  const showPassword = (title: string, username: string, password: string) =>
    modal.info({
      title,
      okText: "我已记下",
      content: (
        <div>
          <p>这个密码只显示这一次。请转告本人，首次登录时必须修改。</p>
          <div className="key-once">
            登录名：{username}
            <br />
            密码：<Typography.Text copyable>{password}</Typography.Text>
          </div>
        </div>
      ),
    });

  const create = async () => {
    const values = await form.validateFields();
    try {
      const { data: created } = await http.post<{ account: Account; initial_password: string | null }>("/accounts", {
        ...values,
        password: values.password || undefined,
      });
      setCreating(false);
      await load();
      if (created.initial_password) showPassword("账号已创建", created.account.username, created.initial_password);
      else {
        message.success("服务账号已创建，接着为它创建密钥");
        setKeysOf(created.account);
      }
    } catch (e) {
      message.error(errorText(e));
    }
  };

  const update = async (target: Account, body: Partial<Account>, done: string) => {
    try {
      await http.patch(`/accounts/${target.id}`, body);
      message.success(done);
      await load();
    } catch (e) {
      message.error(errorText(e));
    }
  };

  const reset = (target: Account) =>
    modal.confirm({
      title: `重置「${target.display_name}」的密码？`,
      content: "会生成一个新的一次性密码，原密码和已登录的会话立即失效。",
      okText: "重置密码",
      onOk: async () => {
        try {
          const { data: r } = await http.post<{ new_password: string }>(`/accounts/${target.id}/reset-password`);
          showPassword("密码已重置", target.username, r.new_password);
        } catch (e) {
          message.error(errorText(e));
        }
      },
    });

  const downloadTemplate = async () => {
    try {
      const { data: blob } = await http.get<Blob>("/accounts/import-template", { responseType: "blob" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "账号导入模板.xlsx";
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      message.error(errorText(e));
    }
  };

  const importFile = async (file: File) => {
    const body = new FormData();
    body.append("file", file);
    try {
      setImported((await http.post<ImportResult>("/accounts/import", body)).data);
      await load();
    } catch (e) {
      message.error(errorText(e));
    }
  };

  const copyImported = () => {
    const text = (imported?.created ?? []).map((c) => `${c.username}\t${c.display_name}\t${c.initial_password}`).join("\n");
    void navigator.clipboard?.writeText(text).then(() => message.success("已复制，可以粘贴到表格里"));
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>账号</h1>
          <p>个人账号用来登录这个界面；服务账号给应用使用，只有密钥，不能登录。账号只能停用，不能删除。</p>
        </div>
        <Space wrap>
          <Button icon={<DownloadOutlined />} onClick={downloadTemplate}>
            下载导入模板
          </Button>
          <Upload accept=".xlsx" showUploadList={false} beforeUpload={(file) => (void importFile(file), false)}>
            <Button icon={<UploadOutlined />}>批量导入</Button>
          </Upload>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreating(true)}>
            创建账号
          </Button>
        </Space>
      </div>

      <Space style={{ marginBottom: 16 }} wrap>
        <Input.Search
          allowClear
          placeholder="按登录名或显示名称查找"
          style={{ width: 260 }}
          onSearch={(keyword) => setQuery((q) => ({ ...q, page: 1, keyword }))}
        />
        <Select
          allowClear
          placeholder="全部类型"
          style={{ width: 130 }}
          options={[
            { value: "person", label: "个人账号" },
            { value: "service", label: "服务账号" },
          ]}
          onChange={(account_type) => setQuery((q) => ({ ...q, page: 1, account_type }))}
        />
      </Space>

      <Table<Account>
        rowKey="id"
        loading={loading}
        dataSource={data.items}
        pagination={{
          current: query.page,
          total: data.total,
          pageSize: 20,
          showSizeChanger: false,
          showTotal: (t) => `共 ${t} 个`,
          onChange: (page) => setQuery((q) => ({ ...q, page })),
        }}
        columns={[
          {
            title: "账号",
            render: (_, a) => (
              <div>
                {a.display_name}
                {a.id === me?.id && <span className="muted">（我）</span>}
                <div className="muted" style={{ fontSize: 12 }}>
                  {a.username}
                </div>
              </div>
            ),
          },
          {
            title: "类型",
            width: 200,
            render: (_, a) => (
              <>
                <Tag>{a.account_type === "service" ? "服务账号" : "个人账号"}</Tag>
                {a.is_admin && <Tag color="cyan">平台管理员</Tag>}
                {a.must_change_password && <Tag color="gold">未改初始密码</Tag>}
              </>
            ),
          },
          {
            title: "可创建知识库",
            width: 120,
            render: (_, a) => (
              <Checkbox
                checked={a.can_create_kb || a.is_admin}
                disabled={a.is_admin}
                aria-label={`${a.display_name} 是否可以创建知识库`}
                onChange={(e) => update(a, { can_create_kb: e.target.checked }, "已修改")}
              />
            ),
          },
          { title: "状态", width: 90, render: (_, a) => (a.status === "active" ? <Tag color="green">正常</Tag> : <Tag>已停用</Tag>) },
          {
            title: "最近登录",
            width: 150,
            render: (_, a) =>
              a.account_type === "service" ? "—" : a.last_login_at ? dayjs(a.last_login_at).format("MM-DD HH:mm") : "从未登录",
          },
          {
            title: "",
            width: 90,
            render: (_, a) => (
              <Dropdown
                menu={{
                  items: [
                    { key: "keys", label: "管理密钥", onClick: () => setKeysOf(a) },
                    ...(a.account_type === "person"
                      ? [
                          { key: "reset", label: "重置密码", onClick: () => reset(a) },
                          {
                            key: "admin",
                            label: a.is_admin ? "取消平台管理员" : "设为平台管理员",
                            disabled: a.id === me?.id,
                            onClick: () => update(a, { is_admin: !a.is_admin }, "已修改"),
                          },
                        ]
                      : []),
                    { type: "divider" as const },
                    a.status === "active"
                      ? {
                          key: "disable",
                          label: "停用",
                          danger: true,
                          disabled: a.id === me?.id,
                          onClick: () =>
                            modal.confirm({
                              title: `停用「${a.display_name}」？`,
                              content: "停用后不能登录，名下的密钥也立刻失效。之后可以重新启用。",
                              okText: "停用",
                              okButtonProps: { danger: true },
                              onOk: () => update(a, { status: "disabled" }, "账号已停用"),
                            }),
                        }
                      : { key: "enable", label: "重新启用", onClick: () => update(a, { status: "active" }, "账号已启用") },
                  ],
                }}
              >
                <Button type="link" size="small">
                  操作
                </Button>
              </Dropdown>
            ),
          },
        ]}
      />

      <Modal title="创建账号" open={creating} onCancel={() => setCreating(false)} onOk={create} okText="创建" destroyOnHidden>
        <Form
          form={form}
          layout="vertical"
          preserve={false}
          initialValues={{ account_type: "person", is_admin: false, can_create_kb: false }}
        >
          <Form.Item label="类型" name="account_type">
            <Radio.Group
              options={[
                { value: "person", label: "个人账号" },
                { value: "service", label: "服务账号（给应用用）" },
              ]}
            />
          </Form.Item>
          <Form.Item
            label="登录名"
            name="username"
            extra={type === "service" ? "应用的标识，例如 diag-assistant。" : "建议用工号。"}
            rules={[
              { required: true, message: "请填写登录名" },
              { pattern: /^[A-Za-z0-9_.@-]{2,64}$/, message: "2 到 64 位，只能用字母、数字和 _ . @ -" },
            ]}
          >
            <Input />
          </Form.Item>
          <Form.Item label="显示名称" name="display_name" rules={[{ required: true, whitespace: true, message: "请填写显示名称" }]}>
            <Input maxLength={64} placeholder={type === "service" ? "例如 诊断助手" : "姓名"} />
          </Form.Item>
          {type !== "service" && (
            <>
              <Form.Item label="初始密码" name="password" extra="不填则自动生成。无论哪种，首次登录都必须修改。">
                <Input.Password autoComplete="new-password" />
              </Form.Item>
              <Form.Item name="is_admin" valuePropName="checked" style={{ marginBottom: 4 }}>
                <Checkbox>平台管理员（可以管理所有账号和所有知识库）</Checkbox>
              </Form.Item>
            </>
          )}
          <Form.Item name="can_create_kb" valuePropName="checked" style={{ marginBottom: 0 }}>
            <Checkbox>可以创建知识库</Checkbox>
          </Form.Item>
        </Form>
      </Modal>

      <Drawer
        title={keysOf ? `「${keysOf.display_name}」的密钥` : ""}
        open={Boolean(keysOf)}
        onClose={() => setKeysOf(null)}
        width={760}
        destroyOnHidden
      >
        {keysOf && (
          <>
            {keysOf.account_type === "person" && (
              <p className="muted">个人账号的密钥代表本人，只能由本人创建。这里可以查看和吊销。</p>
            )}
            <KeyManager basePath={`/accounts/${keysOf.id}/keys`} canCreate={keysOf.account_type === "service"} />
          </>
        )}
      </Drawer>

      <Modal
        title="导入结果"
        open={Boolean(imported)}
        onCancel={() => setImported(null)}
        width={720}
        maskClosable={false}
        footer={
          <Space>
            {Boolean(imported?.created.length) && <Button onClick={copyImported}>复制账号和密码</Button>}
            <Button type="primary" onClick={() => setImported(null)}>
              我已记下
            </Button>
          </Space>
        }
      >
        {imported && (
          <>
            <p>
              成功创建 {imported.created.length} 个，{imported.failed.length} 行没有导入。
              {imported.created.length > 0 && "初始密码只显示这一次，请现在复制保存。"}
            </p>
            {imported.created.length > 0 && (
              <Table
                size="small"
                rowKey="username"
                pagination={false}
                scroll={{ y: 220 }}
                dataSource={imported.created}
                columns={[
                  { title: "登录名", dataIndex: "username" },
                  { title: "显示名称", dataIndex: "display_name" },
                  { title: "初始密码", dataIndex: "initial_password", render: (v: string) => <code>{v}</code> },
                ]}
              />
            )}
            {imported.failed.length > 0 && (
              <Table
                size="small"
                rowKey="row"
                pagination={false}
                scroll={{ y: 180 }}
                style={{ marginTop: 16 }}
                dataSource={imported.failed}
                columns={[
                  { title: "行号", dataIndex: "row", width: 70 },
                  { title: "登录名", dataIndex: "username", width: 160 },
                  { title: "没有导入的原因", dataIndex: "reason" },
                ]}
              />
            )}
          </>
        )}
      </Modal>
    </div>
  );
}
