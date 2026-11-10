import { Alert, App, Button, Empty, Form, Input, InputNumber, Radio, Select, Space, Tag } from "antd";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { errorText, http, Kb, SearchResult } from "../api";

const SCORE_TEXT: Record<string, string> = {
  cosine: "分数是余弦相似度，越接近 1 越相似，可以跨次比较。",
  bm25: "分数是关键字相关度，只能在这一次结果内比较大小。",
  rrf: "分数是按名次融合得到的，只表示先后顺序，数值本身没有意义。",
};

export default function RecallTest() {
  const { message } = App.useApp();
  const [params] = useSearchParams();
  const [kbs, setKbs] = useState<Kb[]>([]);
  const [result, setResult] = useState<SearchResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [form] = Form.useForm();

  useEffect(() => {
    http
      .get<Kb[]>("/knowledge-bases")
      .then((r) => {
        setKbs(r.data);
        const preset = params.get("kb");
        if (preset && r.data.some((k) => k.id === preset)) form.setFieldsValue({ kb_ids: [preset] });
      })
      .catch((e) => message.error(errorText(e)));
  }, [form, message, params]);

  const submit = async (values: Record<string, unknown>) => {
    setBusy(true);
    setError("");
    try {
      setResult((await http.post<SearchResult>("/recall-test", values)).data);
    } catch (e) {
      setResult(null);
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>召回测试</h1>
          <p>
            输入一个问题，看知识库会返回哪些切片。应用调用召回接口时拿到的就是这些结果；平台不做重排序，排序和取舍由应用自己决定。
          </p>
        </div>
      </div>

      <Form form={form} layout="vertical" onFinish={submit} initialValues={{ mode: "hybrid", top_k: 10 }}>
        <Form.Item label="知识库" name="kb_ids" rules={[{ required: true, message: "请至少选择一个知识库" }]}>
          <Select
            mode="multiple"
            maxCount={10}
            placeholder="选择要检索的知识库，最多 10 个"
            optionFilterProp="label"
            options={kbs.map((k) => ({ value: k.id, label: k.name, disabled: k.status !== "active" }))}
          />
        </Form.Item>
        <Form.Item label="问题" name="query" rules={[{ required: true, whitespace: true, message: "请输入问题" }]}>
          <Input.TextArea autoSize={{ minRows: 2, maxRows: 5 }} maxLength={2000} placeholder="例如 划线机中转台卡料怎么处理" />
        </Form.Item>
        <Space size={24} wrap align="start">
          <Form.Item label="检索方式" name="mode">
            <Radio.Group
              options={[
                { value: "hybrid", label: "融合" },
                { value: "vector", label: "向量" },
                { value: "keyword", label: "关键字" },
              ]}
            />
          </Form.Item>
          <Form.Item label="返回条数" name="top_k">
            <InputNumber min={1} max={50} />
          </Form.Item>
        </Space>
        <div>
          <Button type="primary" htmlType="submit" loading={busy}>
            检索
          </Button>
        </div>
      </Form>

      <div style={{ marginTop: 28 }}>
        {error && <Alert type="error" showIcon message={error} />}
        {result && (
          <>
            <p className="muted">
              返回 <span className="num">{result.results.length}</span> 条，用时 <span className="num">{result.took_ms}</span>{" "}
              毫秒。{SCORE_TEXT[result.score_type]}
            </p>
            {result.results.length === 0 && (
              <Empty description="没有找到相关的切片。关键字检索只返回包含查询词的内容，可以换成融合或向量再试。" />
            )}
            {result.results.map((hit, i) => (
              <div className="hit" key={hit.chunk_id}>
                <div className="hit-head">
                  <span className="hit-rank">{i + 1}</span>
                  <Link to={`/doc/${hit.doc_id}`}>{hit.doc_name}</Link>
                  <span>第 {hit.seq + 1} 片</span>
                  {hit.metadata.heading_path && <span>{hit.metadata.heading_path}</span>}
                  {hit.metadata.page != null && <span>第 {hit.metadata.page} 页</span>}
                  {form.getFieldValue("kb_ids")?.length > 1 && <Tag>{hit.kb_name}</Tag>}
                  <span className="hit-score">{hit.score.toFixed(4)}</span>
                </div>
                <pre className="chunk-text" style={{ padding: 0 }}>
                  {hit.content}
                </pre>
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
