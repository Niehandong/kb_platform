// 上传文档，或用新文件替换已有文档。
import { InboxOutlined } from "@ant-design/icons";
import { App, Checkbox, Form, Modal, Upload } from "antd";
import type { UploadFile } from "antd";
import { useState } from "react";
import { ChunkConfig, Doc, errorCode, errorDetail, errorText, http } from "../api";
import ChunkConfigFields from "./ChunkConfigFields";
import TagsEditor from "./TagsEditor";

interface Props {
  open: boolean;
  kbId: string;
  defaultConfig: ChunkConfig;
  /** 给出时表示替换这份文档 */
  replacing?: Doc;
  onClose: () => void;
  onDone: () => void;
}

const ACCEPT = ".txt,.md,.docx,.pdf,.xlsx,.csv";

export default function UploadModal({ open, kbId, defaultConfig, replacing, onClose, onDone }: Props) {
  const { message, modal } = App.useApp();
  const [form] = Form.useForm();
  const [files, setFiles] = useState<UploadFile[]>([]);
  const [busy, setBusy] = useState(false);

  const close = () => {
    setFiles([]);
    onClose();
  };

  const send = async (file: UploadFile, values: Record<string, unknown>, discardEdits: boolean) => {
    const data = new FormData();
    data.append("file", file as unknown as Blob, file.name);
    data.append("chunk_config", JSON.stringify(values.chunk_config));
    data.append("auto_confirm", String(Boolean(values.auto_confirm)));
    if (replacing) {
      data.append("discard_edits", String(discardEdits));
      await http.post(`/documents/${replacing.id}/replace`, data);
    } else {
      data.append("tags", JSON.stringify(values.tags ?? {}));
      await http.post(`/knowledge-bases/${kbId}/documents`, data);
    }
  };

  const submit = async (discardEdits = false) => {
    const values = await form.validateFields();
    if (files.length === 0) {
      message.warning("请先选择文件");
      return;
    }
    if (Object.keys(values.tags ?? {}).some((k) => !k.trim())) {
      message.warning("标签名称不能为空");
      return;
    }
    setBusy(true);
    const failed: string[] = [];
    for (const file of files) {
      try {
        await send(file, values, discardEdits);
      } catch (e) {
        if (replacing && errorCode(e) === "EDITS_WILL_BE_LOST") {
          setBusy(false);
          modal.confirm({
            title: "这份文档有手工修改过的切片",
            content: `已入库的版本里有 ${errorDetail(e).edited_chunks} 个手工修改或新增的切片，替换后它们将丢失。`,
            okText: "丢弃手工修改并替换",
            okButtonProps: { danger: true },
            cancelText: "不替换",
            onOk: () => submit(true),
          });
          return;
        }
        failed.push(`${file.name}：${errorText(e)}`);
      }
    }
    setBusy(false);
    const ok = files.length - failed.length;
    if (ok > 0) {
      message.success(replacing ? "新文件已上传，正在解析" : `已上传 ${ok} 份文档，正在解析`);
      onDone();
    }
    if (failed.length > 0) {
      modal.error({ title: `${failed.length} 份文件没有上传成功`, content: failed.map((f) => <div key={f}>{f}</div>) });
    }
    if (failed.length === 0) close();
  };

  return (
    <Modal
      title={replacing ? `替换「${replacing.name}」` : "上传文档"}
      open={open}
      onCancel={close}
      onOk={() => submit()}
      okText={replacing ? "上传并替换" : "上传"}
      confirmLoading={busy}
      destroyOnHidden
      width={600}
    >
      <Form
        form={form}
        layout="vertical"
        preserve={false}
        initialValues={{ chunk_config: replacing?.chunk_config ?? defaultConfig, auto_confirm: false, tags: {} }}
      >
        <Upload.Dragger
          multiple={!replacing}
          maxCount={replacing ? 1 : undefined}
          accept={ACCEPT}
          fileList={files}
          beforeUpload={(file, list) => {
            setFiles((prev) => (replacing ? [file] : [...prev, ...list.filter((f) => !prev.includes(f))]));
            return false;
          }}
          onRemove={(file) => setFiles((prev) => prev.filter((f) => f.uid !== file.uid))}
          style={{ marginBottom: 16 }}
        >
          <p className="ant-upload-drag-icon">
            <InboxOutlined />
          </p>
          <p className="ant-upload-text">点击选择文件，或把文件拖到这里</p>
          <p className="ant-upload-hint">支持 txt、md、docx、文字版 PDF、xlsx、csv。不支持扫描件和图片。</p>
        </Upload.Dragger>
        {replacing && (
          <p className="muted">新文件入库成功之前，现在的版本照常可以检索；中途可以取消。</p>
        )}
        <ChunkConfigFields />
        {!replacing && (
          <Form.Item label="标签" name="tags" extra="可选。检索时可以按标签筛选，例如只查某条产品线的资料。">
            <TagsEditor />
          </Form.Item>
        )}
        <Form.Item
          name="auto_confirm"
          valuePropName="checked"
          extra="不勾选时，解析完成后先让你预览切片，确认无误再入库。"
          style={{ marginBottom: 0 }}
        >
          <Checkbox>跳过确认，解析后直接入库</Checkbox>
        </Form.Item>
      </Form>
    </Modal>
  );
}
