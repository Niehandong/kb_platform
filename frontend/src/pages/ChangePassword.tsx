import { Alert, Button, Card, Form, Input } from "antd";
import { useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { Account, errorText, http } from "../api";
import { useAuth } from "../auth";

export default function ChangePassword() {
  const { account, signIn } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  if (!account) return <Navigate to="/login" replace />;
  const forced = account.must_change_password;

  const submit = async (values: { old_password: string; new_password: string }) => {
    setBusy(true);
    setError("");
    try {
      const { data } = await http.post<{ token: string; account: Account }>("/auth/change-password", {
        old_password: values.old_password,
        new_password: values.new_password,
      });
      signIn(data.token, data.account);
      navigate("/kb", { replace: true });
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ display: "grid", placeItems: "center", minHeight: "100vh", padding: 16 }}>
      <Card title="修改密码" style={{ width: 420, maxWidth: "100%" }}>
        {forced && (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
            message="当前密码是管理员设置的一次性密码，需要先改成你自己的密码。"
          />
        )}
        {error && <Alert type="error" message={error} showIcon style={{ marginBottom: 16 }} />}
        <Form layout="vertical" onFinish={submit} requiredMark={false}>
          <Form.Item label="当前密码" name="old_password" rules={[{ required: true, message: "请输入当前密码" }]}>
            <Input.Password autoFocus autoComplete="current-password" />
          </Form.Item>
          <Form.Item label="新密码" name="new_password" rules={[{ required: true, message: "请输入新密码" }]}>
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item
            label="再输入一次新密码"
            name="confirm"
            dependencies={["new_password"]}
            rules={[
              { required: true, message: "请再输入一次新密码" },
              ({ getFieldValue }) => ({
                validator: (_, value) =>
                  !value || value === getFieldValue("new_password")
                    ? Promise.resolve()
                    : Promise.reject(new Error("两次输入的新密码不一样")),
              }),
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={busy}>
            保存新密码
          </Button>
          {!forced && (
            <Button style={{ marginLeft: 8 }} onClick={() => navigate(-1)}>
              取消
            </Button>
          )}
        </Form>
      </Card>
    </div>
  );
}
