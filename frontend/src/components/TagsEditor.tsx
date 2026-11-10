// 自定义标签（键值对）的编辑器，作为表单项使用：value 是 { 名称: 值 }。
import { MinusCircleOutlined, PlusOutlined } from "@ant-design/icons";
import { Button, Input, Space } from "antd";

type Tags = Record<string, string>;

export default function TagsEditor({ value = {}, onChange }: { value?: Tags; onChange?: (v: Tags) => void }) {
  const rows = Object.entries(value);
  const emit = (next: [string, string][]) => onChange?.(Object.fromEntries(next));
  const usedNames = new Set(rows.map(([k]) => k));

  const add = () => {
    let n = rows.length + 1;
    while (usedNames.has(`标签${n}`)) n += 1;
    emit([...rows, [`标签${n}`, ""]]);
  };

  return (
    <div>
      {rows.map(([k, v], i) => (
        <Space key={i} style={{ display: "flex", marginBottom: 8 }} align="baseline">
          <Input
            aria-label="标签名称"
            placeholder="名称，如 产品线"
            value={k}
            maxLength={32}
            style={{ width: 160 }}
            onChange={(e) => emit(rows.map((r, j) => (j === i ? [e.target.value, r[1]] : r)))}
          />
          <Input
            aria-label="标签值"
            placeholder="值，如 划线机"
            value={v}
            maxLength={128}
            style={{ width: 220 }}
            onChange={(e) => emit(rows.map((r, j) => (j === i ? [r[0], e.target.value] : r)))}
          />
          <Button
            type="text"
            aria-label="删除这个标签"
            icon={<MinusCircleOutlined />}
            onClick={() => emit(rows.filter((_, j) => j !== i))}
          />
        </Space>
      ))}
      <Button type="dashed" icon={<PlusOutlined />} onClick={add} disabled={rows.length >= 20}>
        添加标签
      </Button>
    </div>
  );
}
