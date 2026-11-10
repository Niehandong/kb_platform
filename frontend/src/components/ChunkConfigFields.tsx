// 切分设置的表单项。放在任意 Form 里，字段名前缀由 name 指定。
import { Form, Input, InputNumber, Radio } from "antd";

export default function ChunkConfigFields({ name = "chunk_config" }: { name?: string }) {
  const mode = Form.useWatch([name, "mode"]);
  return (
    <>
      <Form.Item label="切分方式" name={[name, "mode"]} style={{ marginBottom: 12 }}>
        <Radio.Group
          options={[
            { value: "length", label: "按长度" },
            { value: "separator", label: "按分隔符" },
            { value: "heading", label: "按标题层级" },
          ]}
        />
      </Form.Item>
      {mode === "separator" && (
        <Form.Item
          label="分隔符"
          name={[name, "separator"]}
          extra="遇到分隔符就切开，分隔符留在后一片的开头。换行写成 \n，例如空行写成 \n\n。"
          rules={[{ required: true, message: "请填写分隔符" }]}
        >
          <Input placeholder="例如 ## query" style={{ maxWidth: 320 }} />
        </Form.Item>
      )}
      {mode === "heading" && (
        <Form.Item
          label="切到第几级标题"
          name={[name, "heading_level"]}
          extra="文档里没有识别到标题时，会自动改为按长度切分。"
        >
          <InputNumber min={1} max={6} />
        </Form.Item>
      )}
      {mode !== "separator" && (
        <>
          <Form.Item
            label="每片字数"
            name={[name, "chunk_size"]}
            extra={mode === "heading" ? "章节超过这个字数时，在章节内再切开。" : "按段落聚合到这个字数左右。"}
          >
            <InputNumber min={100} max={4000} step={50} />
          </Form.Item>
          <Form.Item label="相邻两片重叠的字数" name={[name, "overlap"]} extra="不能超过每片字数的一半。">
            <InputNumber min={0} max={1000} step={10} />
          </Form.Item>
        </>
      )}
    </>
  );
}
