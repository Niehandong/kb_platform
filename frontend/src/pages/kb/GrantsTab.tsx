import { App, Button, Form, Modal, Popconfirm, Select, Table, Tag } from "antd";
import { useCallback, useEffect, useState } from "react";
import { errorText, Grant, http, Kb, Level, LEVEL_TEXT } from "../../api";
import SubjectSelect from "../../components/SubjectSelect";

const LEVEL_OPTIONS = [
  { value: "read", label: "只读：查看和检索" },
  { value: "edit", label: "编辑：上传、修改、删除文档和切片" },
  { value: "manage", label: "管理：还可以授权、改设置、删除知识库" },
];

export default function GrantsTab({ kb }: { kb: Kb }) {
  const { message } = App.useApp();
  const [grants, setGrants] = useState<Grant[]>([]);
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  const [form] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setGrants((await http.get<Grant[]>(`/knowledge-bases/${kb.id}/grants`)).data);
    } catch (e) {
      message.error(errorText(e));
    } finally {
      setLoading(false);
    }
  }, [kb.id, message]);

  useEffect(() => {
    void load();
  }, [load]);

  const put = async (subject_type: string, subject_id: string, level: Level, done: string) => {
    try {
      await http.put(`/knowledge-bases/${kb.id}/grants`, { subject_type, subject_id, level });
      message.success(done);
      await load();
      return true;
    } catch (e) {
      message.error(errorText(e));
      return false;
    }
  };

  const add = async () => {
    const { subject, level } = await form.validateFields();
    const [type, id] = (subject as string).split(":");
    if (await put(type, id, level, "已授权")) setAdding(false);
  };

  const revoke = async (grant: Grant) => {
    try {
      await http.delete(`/knowledge-bases/${kb.id}/grants/${grant.id}`);
      message.success("已取消授权");
      await load();
    } catch (e) {
      message.error(errorText(e));
    }
  };

  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 16, marginBottom: 16 }}>
        <p className="muted" style={{ margin: 0, maxWidth: "62ch", lineHeight: 1.6 }}>
          可以授权给分组，也可以授权给单个账号（包括应用使用的服务账号）。一个账号同时有多项授权时，按最高的一档算。
          {kb.is_public && " 这个知识库是公开的，所有账号都至少有只读权限。"}
        </p>
        <Button type="primary" onClick={() => setAdding(true)}>
          添加授权
        </Button>
      </div>
      <Table<Grant>
        rowKey="id"
        loading={loading}
        dataSource={grants}
        pagination={false}
        columns={[
          {
            title: "授权对象",
            render: (_, g) => (
              <>
                <Tag>{g.subject_type === "group" ? "分组" : "账号"}</Tag>
                {g.subject_name}
              </>
            ),
          },
          {
            title: "权限",
            width: 200,
            render: (_, g) => (
              <Select
                size="small"
                value={g.level}
                style={{ width: 110 }}
                aria-label={`${g.subject_name} 的权限`}
                options={(Object.keys(LEVEL_TEXT) as Level[]).map((value) => ({ value, label: LEVEL_TEXT[value] }))}
                onChange={(level) => put(g.subject_type, g.subject_id, level, "权限已修改")}
              />
            ),
          },
          {
            title: "",
            width: 110,
            render: (_, g) => (
              <Popconfirm title={`取消「${g.subject_name}」的授权？`} okText="取消授权" cancelText="保留" onConfirm={() => revoke(g)}>
                <Button type="link" danger size="small">
                  取消授权
                </Button>
              </Popconfirm>
            ),
          },
        ]}
      />
      <Modal title="添加授权" open={adding} onCancel={() => setAdding(false)} onOk={add} okText="授权" destroyOnHidden>
        <Form form={form} layout="vertical" preserve={false} initialValues={{ level: "read" }}>
          <Form.Item label="授权给" name="subject" rules={[{ required: true, message: "请选择分组或账号" }]}>
            <SubjectSelect />
          </Form.Item>
          <Form.Item label="权限" name="level">
            <Select options={LEVEL_OPTIONS} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
