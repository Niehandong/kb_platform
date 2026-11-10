import { PlusOutlined } from "@ant-design/icons";
import { App, Button, Drawer, Form, Input, Modal, Popconfirm, Space, Table } from "antd";
import { useCallback, useEffect, useState } from "react";
import { Brief, errorText, Group, http } from "../api";
import SubjectSelect from "../components/SubjectSelect";

export default function Groups() {
  const { message, modal } = App.useApp();
  const [groups, setGroups] = useState<Group[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<Group | "new" | null>(null);
  const [membersOf, setMembersOf] = useState<Group | null>(null);
  const [members, setMembers] = useState<Brief[]>([]);
  const [adding, setAdding] = useState<string[]>([]);
  const [form] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setGroups((await http.get<Group[]>("/groups")).data);
    } catch (e) {
      message.error(errorText(e));
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    void load();
  }, [load]);

  const loadMembers = useCallback(
    async (group: Group) => {
      try {
        setMembers((await http.get<Brief[]>(`/groups/${group.id}/members`)).data);
      } catch (e) {
        message.error(errorText(e));
      }
    },
    [message],
  );

  const save = async () => {
    const values = await form.validateFields();
    try {
      if (editing === "new") await http.post("/groups", values);
      else if (editing) await http.patch(`/groups/${editing.id}`, values);
      message.success(editing === "new" ? "分组已创建" : "分组已修改");
      setEditing(null);
      await load();
    } catch (e) {
      message.error(errorText(e));
    }
  };

  const remove = (group: Group) =>
    modal.confirm({
      title: `删除分组「${group.name}」？`,
      content: `分组里的 ${group.member_count} 个成员不会被删除，但他们通过这个分组获得的知识库权限会一起取消。`,
      okText: "删除分组",
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await http.delete(`/groups/${group.id}`);
          message.success("分组已删除");
          await load();
        } catch (e) {
          message.error(errorText(e));
        }
      },
    });

  const addMembers = async () => {
    if (!membersOf || adding.length === 0) return;
    try {
      const { data } = await http.post<{ added: number; skipped: number }>(`/groups/${membersOf.id}/members`, {
        account_ids: adding.map((v) => v.split(":")[1]),
      });
      message.success(`已添加 ${data.added} 人${data.skipped ? `，${data.skipped} 人原本就在组里` : ""}`);
      setAdding([]);
      await loadMembers(membersOf);
      await load();
    } catch (e) {
      message.error(errorText(e));
    }
  };

  const removeMember = async (account: Brief) => {
    if (!membersOf) return;
    try {
      await http.delete(`/groups/${membersOf.id}/members/${account.id}`);
      message.success("已移出分组");
      await loadMembers(membersOf);
      await load();
    } catch (e) {
      message.error(errorText(e));
    }
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>分组</h1>
          <p>分组是一批账号的集合，比如一条产品线。把知识库授权给分组，组里的人就都有了权限，不用逐个授权。</p>
        </div>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => {
            setEditing("new");
            form.resetFields();
          }}
        >
          创建分组
        </Button>
      </div>

      <Table<Group>
        rowKey="id"
        loading={loading}
        dataSource={groups}
        pagination={false}
        locale={{ emptyText: "还没有分组" }}
        columns={[
          { title: "名称", dataIndex: "name", width: 220 },
          { title: "说明", dataIndex: "description", render: (v: string) => v || <span className="muted">—</span> },
          { title: "成员", dataIndex: "member_count", width: 90, align: "right", className: "num" },
          {
            title: "",
            width: 220,
            render: (_, g) => (
              <Space size={4}>
                <Button
                  type="link"
                  size="small"
                  onClick={() => {
                    setMembersOf(g);
                    setMembers([]);
                    void loadMembers(g);
                  }}
                >
                  管理成员
                </Button>
                <Button
                  type="link"
                  size="small"
                  onClick={() => {
                    setEditing(g);
                    form.setFieldsValue(g);
                  }}
                >
                  修改
                </Button>
                <Button type="link" size="small" danger onClick={() => remove(g)}>
                  删除
                </Button>
              </Space>
            ),
          },
        ]}
      />

      <Modal
        title={editing === "new" ? "创建分组" : "修改分组"}
        open={Boolean(editing)}
        onCancel={() => setEditing(null)}
        onOk={save}
        okText="保存"
      >
        <Form form={form} layout="vertical">
          <Form.Item label="名称" name="name" rules={[{ required: true, whitespace: true, message: "请填写名称" }]}>
            <Input maxLength={64} placeholder="例如 调阻机产品线" />
          </Form.Item>
          <Form.Item label="说明" name="description">
            <Input.TextArea maxLength={500} rows={2} />
          </Form.Item>
        </Form>
      </Modal>

      <Drawer
        title={membersOf ? `「${membersOf.name}」的成员` : ""}
        open={Boolean(membersOf)}
        onClose={() => setMembersOf(null)}
        width={560}
        destroyOnHidden
      >
        <Space.Compact style={{ width: "100%", marginBottom: 16 }}>
          <SubjectSelect
            kinds={["account"]}
            multiple
            value={adding}
            onChange={(v) => setAdding(v as string[])}
            placeholder="输入姓名或登录名，选择要加入的账号"
          />
          <Button type="primary" onClick={addMembers} disabled={adding.length === 0}>
            加入分组
          </Button>
        </Space.Compact>
        <Table<Brief>
          rowKey="id"
          size="small"
          dataSource={members}
          pagination={false}
          locale={{ emptyText: "这个分组还没有成员" }}
          columns={[
            {
              title: "账号",
              render: (_, a) => (
                <>
                  {a.display_name}
                  <span className="muted">（{a.username}）</span>
                  {a.account_type === "service" && <span className="muted"> 服务账号</span>}
                </>
              ),
            },
            {
              title: "",
              width: 90,
              render: (_, a) => (
                <Popconfirm title={`把「${a.display_name}」移出分组？`} okText="移出" onConfirm={() => removeMember(a)}>
                  <Button type="link" danger size="small">
                    移出
                  </Button>
                </Popconfirm>
              ),
            },
          ]}
        />
      </Drawer>
    </div>
  );
}
