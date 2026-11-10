import { LoadingOutlined } from "@ant-design/icons";
import { Tag, Tooltip } from "antd";
import { Doc, STATUS_TEXT } from "../api";

const COLOR: Record<string, string | undefined> = {
  parsing: "processing",
  embedding: "processing",
  pending_confirm: "gold",
  ready: "green",
  failed: "red",
};

export default function StatusTag({ doc }: { doc: Doc }) {
  const busy = doc.status === "parsing" || doc.status === "embedding";
  const tag = (
    <Tag color={COLOR[doc.status]} icon={busy ? <LoadingOutlined /> : undefined} style={{ marginRight: 0 }}>
      {STATUS_TEXT[doc.status]}
    </Tag>
  );
  return (
    <span>
      {doc.status === "failed" && doc.error_message ? <Tooltip title={doc.error_message}>{tag}</Tooltip> : tag}
      {doc.replacing && (
        <Tooltip title="已有入库的版本可以检索，同时正在准备新版本">
          <Tag style={{ marginLeft: 6, marginRight: 0 }}>新版本</Tag>
        </Tooltip>
      )}
      {!doc.enabled && <Tag style={{ marginLeft: 6, marginRight: 0 }}>已停用</Tag>}
    </span>
  );
}
