// 密钥的列表、创建、吊销。basePath 是 /me/keys 或 /accounts/<id>/keys。
import { App, Button, DatePicker, Form, Input, Modal, Popconfirm, Table, Tag, Typography } from "antd";
import dayjs, { Dayjs } from "dayjs";
import { useCallback, useEffect, useState } from "react";
import { ApiKey, errorText, http } from "../api";

export default function KeyManager({ basePath, canCreate = true }: { basePath: string; canCreate?: boolean }) {
  const { message } = App.useApp();
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<string | null>(null);
  const [form] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setKeys((await http.get<ApiKey[]>(basePath)).data);
    } catch (e) {
      message.error(errorText(e));
    } finally {
      setLoading(false);
    }
  }, [basePath, message]);

  useEffect(() => {
    void load();
  }, [load]);

  const create = async () => {
    const values = await form.validateFields();
    try {
      const { data } = await http.post<{ key: string }>(basePath, {
        name: values.name,
        expires_at: (values.expires_at as Dayjs | undefined)?.endOf("day").toISOString(),
      });
      setCreating(false);
      setCreated(data.key);
      await load();
    } catch (e) {
      message.error(errorText(e));
    }
  };

  const revoke = async (key: ApiKey) => {
    try {
      await http.delete(`${basePath}/${key.id}`);
      message.success("密钥已吊销");
      await load();
    } catch (e) {
      message.error(errorText(e));
    }
  };

  const state = (key: ApiKey) => {
    if (key.revoked_at) return <Tag>已吊销</Tag>;
    if (key.expires_at && dayjs(key.expires_at).isBefore(dayjs())) return <Tag color="orange">已过期</Tag>;
    return <Tag color="green">有效</Tag>;
  };

  return (
    <>
      {canCreate && (
        <Button type="primary" onClick={() => setCreating(true)} style={{ marginBottom: 16 }}>
          创建密钥
        </Button>
      )}
      <Table<ApiKey>
        rowKey="id"
        loading={loading}
        dataSource={keys}
        pagination={false}
        locale={{ emptyText: "还没有密钥" }}
        columns={[
          { title: "名称", dataIndex: "name" },
          { title: "密钥开头", render: (_, k) => <code>{k.key_prefix}…</code> },
          { title: "状态", width: 90, render: (_, k) => state(k) },
          {
            title: "过期时间",
            width: 130,
            render: (_, k) => (k.expires_at ? dayjs(k.expires_at).format("YYYY-MM-DD") : "不过期"),
          },
          {
            title: "最近使用",
            width: 150,
            render: (_, k) => (k.last_used_at ? dayjs(k.last_used_at).format("MM-DD HH:mm") : "从未使用"),
          },
          {
            title: "",
            width: 90,
            render: (_, k) =>
              !k.revoked_at && (
                <Popconfirm
                  title="吊销这把密钥？"
                  description="使用它的程序会立刻无法调用，且不能恢复。"
                  okText="吊销"
                  okButtonProps={{ danger: true }}
                  onConfirm={() => revoke(k)}
                >
                  <Button type="link" danger size="small">
                    吊销
                  </Button>
                </Popconfirm>
              ),
          },
        ]}
      />

      <Modal title="创建密钥" open={creating} onCancel={() => setCreating(false)} onOk={create} okText="创建" destroyOnHidden>
        <Form form={form} layout="vertical" preserve={false}>
          <Form.Item label="名称" name="name" rules={[{ required: true, whitespace: true, message: "请填写名称" }]}>
            <Input maxLength={64} placeholder="用在哪里，例如 诊断助手生产环境" />
          </Form.Item>
          <Form.Item label="过期时间" name="expires_at" extra="不填表示不过期。">
            <DatePicker disabledDate={(d) => d.isBefore(dayjs().startOf("day"))} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="密钥已创建"
        open={created !== null}
        closable={false}
        maskClosable={false}
        footer={
          <Button type="primary" onClick={() => setCreated(null)}>
            我已保存
          </Button>
        }
      >
        <p>这把密钥只显示这一次，关闭后无法再查看。请现在复制并保存好。</p>
        <Typography.Paragraph className="key-once" copyable={{ text: created ?? "", tooltips: ["复制", "已复制"] }}>
          {created}
        </Typography.Paragraph>
        <p className="muted" style={{ marginBottom: 0 }}>
          调用时放在请求头里：<code>Authorization: Bearer 密钥</code>
        </p>
      </Modal>
    </>
  );
}
