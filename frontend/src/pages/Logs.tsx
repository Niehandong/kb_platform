import { Tabs } from "antd";
import { OperationLogTable, RetrievalLogTable } from "../components/LogTables";

export default function Logs() {
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>日志</h1>
          <p>全平台的操作日志和检索记录。检索记录保留 30 天。</p>
        </div>
      </div>
      <Tabs
        destroyOnHidden
        items={[
          { key: "operations", label: "操作日志", children: <OperationLogTable /> },
          { key: "retrievals", label: "检索记录", children: <RetrievalLogTable /> },
        ]}
      />
    </div>
  );
}
