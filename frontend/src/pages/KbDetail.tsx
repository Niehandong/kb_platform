import { Alert, Breadcrumb, Result, Spin, Tabs, Tag } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { canEdit, canManage, errorText, http, Kb, LEVEL_TEXT } from "../api";
import { OperationLogTable, RetrievalLogTable } from "../components/LogTables";
import DocumentsTab from "./kb/DocumentsTab";
import GrantsTab from "./kb/GrantsTab";
import SettingsTab from "./kb/SettingsTab";

export default function KbDetail() {
  const { kbId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const [kb, setKb] = useState<Kb | null>(null);
  const [error, setError] = useState("");
  const timer = useRef<number>();

  const load = useCallback(async () => {
    try {
      setKb((await http.get<Kb>(`/knowledge-bases/${kbId}`)).data);
      setError("");
    } catch (e) {
      setError(errorText(e));
    }
  }, [kbId]);

  useEffect(() => {
    void load();
  }, [load]);

  // 重建索引期间定时刷新，直到完成或失败
  useEffect(() => {
    window.clearTimeout(timer.current);
    if (kb?.status === "rebuilding" && !kb.rebuild_error) {
      timer.current = window.setTimeout(() => void load(), 2000);
    }
    return () => window.clearTimeout(timer.current);
  }, [kb, load]);

  if (error) return <Result status="warning" title={error} extra={<Link to="/kb">回到知识库列表</Link>} />;
  if (!kb) return <Spin style={{ margin: 48 }} />;

  const tabs = [
    { key: "docs", label: "文档", children: <DocumentsTab kb={kb} onChanged={load} /> },
    ...(canManage(kb.my_level) ? [{ key: "grants", label: "授权", children: <GrantsTab kb={kb} /> }] : []),
    ...(canEdit(kb.my_level)
      ? [{ key: "settings", label: "设置", children: <SettingsTab key={kb.id + kb.status} kb={kb} onChanged={load} /> }]
      : []),
    ...(canManage(kb.my_level)
      ? [
          { key: "operations", label: "操作日志", children: <OperationLogTable kbId={kb.id} /> },
          { key: "retrievals", label: "检索记录", children: <RetrievalLogTable kbId={kb.id} /> },
        ]
      : []),
  ];

  return (
    <div className="page">
      <Breadcrumb items={[{ title: <Link to="/kb">知识库</Link> }, { title: kb.name }]} style={{ marginBottom: 12 }} />
      <div className="page-head">
        <div>
          <h1>
            {kb.name} <Tag style={{ verticalAlign: "middle", fontWeight: 400 }}>{LEVEL_TEXT[kb.my_level]}</Tag>
            {kb.is_public && <Tag style={{ verticalAlign: "middle", fontWeight: 400 }}>公开</Tag>}
          </h1>
          {kb.description && <p>{kb.description}</p>}
        </div>
        <Link to={`/recall?kb=${kb.id}`}>在这个知识库里做召回测试</Link>
      </div>
      {kb.status === "rebuilding" && (
        <Alert
          type={kb.rebuild_error ? "error" : "info"}
          showIcon
          style={{ marginBottom: 16 }}
          message={
            kb.rebuild_error
              ? `重建索引失败：${kb.rebuild_error}。在「设置」里可以重新发起，完成之前这个知识库不能检索。`
              : "正在重建索引，完成之前这个知识库不能检索。"
          }
        />
      )}
      {kb.needs_rebuild && kb.status === "active" && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="平台更换过向量模型，这个知识库需要重建索引后才能做向量检索；关键字检索不受影响。"
        />
      )}
      <Tabs
        activeKey={params.get("tab") ?? "docs"}
        onChange={(tab) => setParams({ tab }, { replace: true })}
        items={tabs}
        destroyOnHidden
      />
    </div>
  );
}
