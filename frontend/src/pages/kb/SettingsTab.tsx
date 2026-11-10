import { Alert, App, Button, Checkbox, Divider, Form, Input, Space } from "antd";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { canManage, errorText, http, Kb } from "../../api";
import ChunkConfigFields from "../../components/ChunkConfigFields";

export default function SettingsTab({ kb, onChanged }: { kb: Kb; onChanged: () => void }) {
  const { message, modal } = App.useApp();
  const navigate = useNavigate();
  const [saving, setSaving] = useState(false);
  const manage = canManage(kb.my_level);

  const save = async (values: Record<string, unknown>) => {
    setSaving(true);
    try {
      // 只有管理权限能改名称、描述和是否公开；编辑权限只提交切分设置
      const body = manage ? values : { chunk_config: values.chunk_config };
      await http.patch(`/knowledge-bases/${kb.id}`, body);
      message.success("设置已保存");
      onChanged();
    } catch (e) {
      message.error(errorText(e));
    } finally {
      setSaving(false);
    }
  };

  const rebuild = () =>
    modal.confirm({
      title: "重建索引？",
      content: "按数据库里的切片重新生成这个知识库的检索数据，并改用当前配置的向量模型。重建期间这个知识库不能检索，文档多时需要几分钟。",
      okText: "开始重建",
      onOk: async () => {
        try {
          await http.post(`/knowledge-bases/${kb.id}/rebuild`);
          message.success("已开始重建索引");
          onChanged();
        } catch (e) {
          message.error(errorText(e));
        }
      },
    });

  const remove = () =>
    modal.confirm({
      title: `删除知识库「${kb.name}」？`,
      content: `里面的 ${kb.document_count} 份文档、全部切片和原始文件都会删除，不能恢复。`,
      okText: "删除知识库",
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await http.delete(`/knowledge-bases/${kb.id}`);
          message.success("知识库已删除");
          navigate("/kb");
        } catch (e) {
          message.error(errorText(e));
        }
      },
    });

  return (
    <div style={{ maxWidth: 640 }}>
      <Form
        layout="vertical"
        onFinish={save}
        initialValues={{
          name: kb.name,
          description: kb.description,
          is_public: kb.is_public,
          chunk_config: { ...kb.chunk_config },
        }}
      >
        <Form.Item label="名称" name="name" rules={[{ required: true, whitespace: true, message: "请填写名称" }]}>
          <Input maxLength={128} disabled={!manage} />
        </Form.Item>
        <Form.Item label="描述" name="description">
          <Input.TextArea maxLength={2000} rows={2} disabled={!manage} />
        </Form.Item>
        <Form.Item name="is_public" valuePropName="checked" extra="公开后，平台上所有账号都能查看和检索这个知识库。">
          <Checkbox disabled={!manage}>公开</Checkbox>
        </Form.Item>
        <Divider orientation="left" plain>
          默认切分设置
        </Divider>
        <p className="muted">只影响以后上传的文档；已有文档要改切分方式，请到文档页重新切分。</p>
        <ChunkConfigFields />
        <Button type="primary" htmlType="submit" loading={saving}>
          保存设置
        </Button>
      </Form>

      {manage && (
        <>
          <Divider orientation="left" plain>
            检索数据
          </Divider>
          {kb.needs_rebuild && (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 12 }}
              message={`这个知识库入库时用的向量模型是 ${kb.embedding_model}，与平台当前配置不同。重建索引之后才能做向量检索。`}
            />
          )}
          {kb.rebuild_error && (
            <Alert type="error" showIcon style={{ marginBottom: 12 }} message={`上一次重建索引失败：${kb.rebuild_error}`} />
          )}
          <Space direction="vertical">
            <span className="muted">向量模型：{kb.embedding_model}</span>
            <Button onClick={rebuild} disabled={kb.status === "rebuilding" && !kb.rebuild_error}>
              {kb.status === "rebuilding" && !kb.rebuild_error ? "正在重建索引…" : "重建索引"}
            </Button>
          </Space>
          <Divider orientation="left" plain>
            删除
          </Divider>
          <Button danger onClick={remove}>
            删除这个知识库
          </Button>
        </>
      )}
    </div>
  );
}
