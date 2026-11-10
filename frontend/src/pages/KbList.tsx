import { PlusOutlined } from "@ant-design/icons";
import { App, Button, Checkbox, Empty, Form, Input, Modal, Spin, Tag } from "antd";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { DEFAULT_CHUNK_CONFIG, errorText, http, Kb, LEVEL_TEXT } from "../api";
import { useAuth } from "../auth";
import ChunkConfigFields from "../components/ChunkConfigFields";

export default function KbList() {
  const { account } = useAuth();
  const { message } = App.useApp();
  const navigate = useNavigate();
  const [kbs, setKbs] = useState<Kb[] | null>(null);
  const [creating, setCreating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();
  const mayCreate = account?.is_admin || account?.can_create_kb;

  useEffect(() => {
    http
      .get<Kb[]>("/knowledge-bases")
      .then((r) => setKbs(r.data))
      .catch((e) => {
        message.error(errorText(e));
        setKbs([]);
      });
  }, [message]);

  const create = async () => {
    const values = await form.validateFields();
    setSaving(true);
    try {
      const { data } = await http.post<Kb>("/knowledge-bases", values);
      message.success("知识库已创建");
      navigate(`/kb/${data.id}`);
    } catch (e) {
      message.error(errorText(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>知识库</h1>
          <p>这里列出你有权限访问的知识库。每个知识库单独授权，别人的库你看不到。</p>
        </div>
        {mayCreate && (
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreating(true)}>
            创建知识库
          </Button>
        )}
      </div>

      {kbs === null ? (
        <Spin />
      ) : kbs.length === 0 ? (
        <Empty
          description={
            mayCreate ? "还没有知识库，创建一个开始上传文档。" : "还没有人把知识库授权给你。需要访问请联系知识库的管理者。"
          }
        />
      ) : (
        <div className="kb-grid">
          {kbs.map((kb) => (
            <Link key={kb.id} to={`/kb/${kb.id}`} className="kb-card">
              <h3>{kb.name}</h3>
              <p>{kb.description || "没有填写描述"}</p>
              <footer>
                <span className="num">{kb.document_count} 份文档</span>
                <span>
                  {kb.status === "rebuilding" && <Tag color="orange">重建索引中</Tag>}
                  {kb.needs_rebuild && kb.status === "active" && <Tag color="red">需要重建索引</Tag>}
                  {kb.is_public && <Tag>公开</Tag>}
                  <Tag color={kb.my_level === "manage" ? "cyan" : undefined} style={{ marginRight: 0 }}>
                    {LEVEL_TEXT[kb.my_level]}
                  </Tag>
                </span>
              </footer>
            </Link>
          ))}
        </div>
      )}

      <Modal
        title="创建知识库"
        open={creating}
        onCancel={() => setCreating(false)}
        onOk={create}
        okText="创建"
        confirmLoading={saving}
        destroyOnHidden
        width={560}
      >
        <Form
          form={form}
          layout="vertical"
          preserve={false}
          initialValues={{ is_public: false, chunk_config: DEFAULT_CHUNK_CONFIG }}
        >
          <Form.Item label="名称" name="name" rules={[{ required: true, whitespace: true, message: "请填写名称" }]}>
            <Input maxLength={128} placeholder="例如 调阻机常见问题" />
          </Form.Item>
          <Form.Item label="描述" name="description">
            <Input.TextArea maxLength={2000} rows={2} placeholder="放哪些资料、给谁用" />
          </Form.Item>
          <Form.Item name="is_public" valuePropName="checked" extra="公开后，平台上所有账号都能查看和检索这个知识库。">
            <Checkbox>公开</Checkbox>
          </Form.Item>
          <p className="muted" style={{ margin: "0 0 8px" }}>
            默认切分设置（上传每份文档时还可以单独改）
          </p>
          <ChunkConfigFields />
        </Form>
      </Modal>
    </div>
  );
}
