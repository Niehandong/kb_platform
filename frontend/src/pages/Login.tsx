import { Alert, Button, Form, Input } from "antd";
import { useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { Account, errorText, http } from "../api";
import { useAuth } from "../auth";

export default function Login() {
  const { account, signIn } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  if (account) return <Navigate to={account.must_change_password ? "/change-password" : "/kb"} replace />;

  const submit = async (values: { username: string; password: string }) => {
    setBusy(true);
    setError("");
    try {
      const { data } = await http.post<{ token: string; account: Account }>("/auth/login", values);
      signIn(data.token, data.account);
      navigate(data.account.must_change_password ? "/change-password" : "/kb", { replace: true });
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <div className="login-side" aria-hidden="true">
        <h1>知识库平台</h1>
        <p>把说明书、规格书、常见问题切成一片一片，存好、管好，让每个应用都从这里取用。</p>
        <div className="login-ruler">
          {[0, 1, 2, 3, 4, 5, 6, 7].map((n) => (
            <span key={n} style={{ left: n * 120 }}>
              {n}
            </span>
          ))}
        </div>
      </div>
      <div className="login-form">
        <h2>登录</h2>
        {error && <Alert type="error" message={error} showIcon style={{ marginBottom: 16 }} />}
        <Form layout="vertical" onFinish={submit} requiredMark={false}>
          <Form.Item label="登录名" name="username" rules={[{ required: true, message: "请输入登录名" }]}>
            <Input autoFocus autoComplete="username" size="large" />
          </Form.Item>
          <Form.Item label="密码" name="password" rules={[{ required: true, message: "请输入密码" }]}>
            <Input.Password autoComplete="current-password" size="large" />
          </Form.Item>
          <Button type="primary" htmlType="submit" size="large" block loading={busy}>
            登录
          </Button>
        </Form>
        <p className="muted" style={{ marginTop: 24, fontSize: 13 }}>
          没有账号或忘记密码，请联系平台管理员。
        </p>
      </div>
    </div>
  );
}
