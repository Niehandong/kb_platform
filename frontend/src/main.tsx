import { App as AntApp, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import dayjs from "dayjs";
import "dayjs/locale/zh-cn";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { AuthProvider } from "./auth";
import "./styles.css";

dayjs.locale("zh-cn");

const theme = {
  token: {
    colorPrimary: "#13697d",
    colorInfo: "#13697d",
    colorTextBase: "#16242b",
    colorBgLayout: "#f3f5f6",
    colorBorderSecondary: "#dde3e6",
    borderRadius: 4,
    fontFamily: '"PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", system-ui, sans-serif',
  },
  components: {
    Layout: {
      siderBg: "#ffffff",
      headerBg: "#ffffff",
      headerHeight: 52,
      headerPadding: "0 24px",
      triggerBg: "#ffffff",
      triggerColor: "#16242b",
    },
    Menu: { itemBorderRadius: 4, itemSelectedBg: "#e6f1f3" },
    Table: { headerBg: "#f3f5f6" },
  },
};

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} theme={theme}>
      <AntApp>
        <BrowserRouter>
          <AuthProvider>
            <App />
          </AuthProvider>
        </BrowserRouter>
      </AntApp>
    </ConfigProvider>
  </React.StrictMode>,
);
