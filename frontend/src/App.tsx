import {
  AuditOutlined,
  DatabaseOutlined,
  KeyOutlined,
  SearchOutlined,
  TeamOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { Dropdown, Layout, Menu, Spin } from "antd";
import { Navigate, Outlet, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "./auth";
import Accounts from "./pages/Accounts";
import ChangePassword from "./pages/ChangePassword";
import DocDetail from "./pages/DocDetail";
import Groups from "./pages/Groups";
import KbDetail from "./pages/KbDetail";
import KbList from "./pages/KbList";
import Login from "./pages/Login";
import Logs from "./pages/Logs";
import MyKeys from "./pages/MyKeys";
import RecallTest from "./pages/RecallTest";

function Shell() {
  const { account, signOut } = useAuth();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  if (!account) return <Navigate to="/login" replace />;
  if (account.must_change_password) return <Navigate to="/change-password" replace />;

  const items = [
    { key: "/kb", icon: <DatabaseOutlined />, label: "知识库" },
    { key: "/recall", icon: <SearchOutlined />, label: "召回测试" },
    { key: "/keys", icon: <KeyOutlined />, label: "我的密钥" },
    ...(account.is_admin
      ? [
          { type: "divider" as const },
          { key: "/accounts", icon: <UserOutlined />, label: "账号" },
          { key: "/groups", icon: <TeamOutlined />, label: "分组" },
          { key: "/logs", icon: <AuditOutlined />, label: "日志" },
        ]
      : []),
  ];
  // 文档页属于知识库这一栏
  const selected = pathname.startsWith("/doc") ? "/kb" : `/${pathname.split("/")[1]}`;

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Layout.Sider
        width={208}
        breakpoint="lg"
        collapsedWidth={0}
        zeroWidthTriggerStyle={{ top: 8, insetInlineEnd: -44, border: "1px solid #dde3e6" }}
        style={{ borderRight: "1px solid #dde3e6" }}
      >
        <div className="app-brand">
          知识库平台
          <small>文档入库与检索</small>
        </div>
        <Menu
          mode="inline"
          selectedKeys={[selected]}
          items={items}
          onClick={(e) => navigate(e.key)}
          style={{ border: "none", padding: "8px" }}
        />
      </Layout.Sider>
      <Layout>
        <Layout.Header style={{ display: "flex", justifyContent: "flex-end", borderBottom: "1px solid #dde3e6" }}>
          <Dropdown
            menu={{
              items: [
                { key: "password", label: "修改密码", onClick: () => navigate("/change-password") },
                {
                  key: "logout",
                  label: "退出登录",
                  onClick: () => {
                    signOut();
                    navigate("/login");
                  },
                },
              ],
            }}
          >
            <a onClick={(e) => e.preventDefault()} style={{ color: "inherit" }}>
              <UserOutlined /> {account.display_name}
              {account.is_admin ? "（平台管理员）" : ""}
            </a>
          </Dropdown>
        </Layout.Header>
        <Layout.Content>
          <Outlet />
        </Layout.Content>
      </Layout>
    </Layout>
  );
}

function AdminOnly() {
  const { account } = useAuth();
  return account?.is_admin ? <Outlet /> : <Navigate to="/kb" replace />;
}

export default function App() {
  const { loading } = useAuth();
  if (loading) {
    return (
      <div style={{ display: "grid", placeItems: "center", height: "100vh" }}>
        <Spin />
      </div>
    );
  }
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/change-password" element={<ChangePassword />} />
      <Route element={<Shell />}>
        <Route path="/kb" element={<KbList />} />
        <Route path="/kb/:kbId" element={<KbDetail />} />
        <Route path="/doc/:docId" element={<DocDetail />} />
        <Route path="/recall" element={<RecallTest />} />
        <Route path="/keys" element={<MyKeys />} />
        <Route element={<AdminOnly />}>
          <Route path="/accounts" element={<Accounts />} />
          <Route path="/groups" element={<Groups />} />
          <Route path="/logs" element={<Logs />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/kb" replace />} />
    </Routes>
  );
}
