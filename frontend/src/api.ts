// 接口调用。所有请求带登录凭证；登录失效时回到登录页。
import axios, { AxiosError } from "axios";

const TOKEN_KEY = "kb_platform_token";

export const tokenStore = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (token: string) => localStorage.setItem(TOKEN_KEY, token),
  clear: () => localStorage.removeItem(TOKEN_KEY),
};

export const http = axios.create({ baseURL: "/api/v1", timeout: 120000 });

http.interceptors.request.use((config) => {
  const token = tokenStore.get();
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

http.interceptors.response.use(
  (response) => response,
  (error: AxiosError<ApiError>) => {
    const code = error.response?.data?.code;
    const onLogin = location.pathname === "/login";
    if (error.response?.status === 401 && !onLogin && code !== "LOGIN_FAILED") {
      tokenStore.clear();
      location.href = "/login";
    } else if (code === "PASSWORD_CHANGE_REQUIRED" && location.pathname !== "/change-password") {
      location.href = "/change-password";
    }
    return Promise.reject(error);
  },
);

export interface ApiError {
  code: string;
  message: string;
  detail: Record<string, unknown>;
}

/** 把接口错误转成给人看的一句话 */
export function errorText(error: unknown): string {
  const e = error as AxiosError<ApiError>;
  if (e.response?.data?.message) {
    const errors = (e.response.data.detail as { errors?: { field: string; reason: string }[] })?.errors;
    if (errors?.length) return errors.map((x) => `${x.field}：${x.reason}`).join("；");
    return e.response.data.message;
  }
  if (e.code === "ECONNABORTED") return "请求超时，请稍后重试";
  if (e.message === "Network Error") return "连接不上服务器";
  return "操作失败，请稍后重试";
}

export function errorCode(error: unknown): string | undefined {
  return (error as AxiosError<ApiError>).response?.data?.code;
}

export function errorDetail(error: unknown): Record<string, unknown> {
  return (error as AxiosError<ApiError>).response?.data?.detail ?? {};
}

// ---------- 数据结构 ----------

export type Level = "read" | "edit" | "manage";

export interface Account {
  id: string;
  username: string;
  display_name: string;
  account_type: "person" | "service";
  is_admin: boolean;
  can_create_kb: boolean;
  status: "active" | "disabled";
  must_change_password: boolean;
  last_login_at: string | null;
  created_at: string;
}

export interface ChunkConfig {
  mode: "separator" | "length" | "heading";
  separator: string;
  chunk_size: number;
  overlap: number;
  heading_level: number;
  effective_mode?: string;
}

export interface Kb {
  id: string;
  name: string;
  description: string;
  is_public: boolean;
  status: "active" | "rebuilding" | "deleting";
  embedding_model: string;
  chunk_config: ChunkConfig;
  my_level: Level;
  document_count: number;
  rebuild_error: string | null;
  needs_rebuild: boolean;
  created_at: string;
}

export type DocStatus = "parsing" | "pending_confirm" | "embedding" | "ready" | "failed";

export interface Doc {
  id: string;
  kb_id: string;
  name: string;
  file_type: string;
  file_size: number;
  status: DocStatus;
  failed_stage: string | null;
  error_message: string | null;
  auto_confirm: boolean;
  enabled: boolean;
  searchable: boolean;
  replacing: boolean;
  edited_chunks: number;
  chunk_config: ChunkConfig;
  chunk_count: number;
  tags: Record<string, string>;
  created_at: string;
  updated_at: string;
}

export interface Chunk {
  id: string;
  seq: number;
  content: string;
  char_count: number;
  enabled: boolean;
  origin: "auto" | "manual";
  is_edited: boolean;
  metadata: { heading_path?: string | null; page?: number | null; tags?: Record<string, string> };
  sync_status: string;
}

export interface Grant {
  id: string;
  subject_type: "group" | "account";
  subject_id: string;
  subject_name: string;
  level: Level;
  created_at: string;
}

export interface SearchHit {
  chunk_id: string;
  kb_id: string;
  kb_name: string;
  doc_id: string;
  doc_name: string;
  seq: number;
  content: string;
  score: number;
  metadata: { heading_path?: string | null; page?: number | null; tags?: Record<string, string> };
}

export interface SearchResult {
  mode: string;
  score_type: "cosine" | "bm25" | "rrf";
  took_ms: number;
  results: SearchHit[];
}

export interface ApiKey {
  id: string;
  name: string;
  key_prefix: string;
  expires_at: string | null;
  revoked_at: string | null;
  last_used_at: string | null;
  created_at: string;
}

export interface Group {
  id: string;
  name: string;
  description: string;
  member_count: number;
  created_at: string;
}

export interface Brief {
  id: string;
  username: string;
  display_name: string;
  account_type: string;
}

export interface Page<T> {
  total: number;
  items: T[];
}

// ---------- 显示用的文字 ----------

export const LEVEL_TEXT: Record<Level, string> = { read: "只读", edit: "编辑", manage: "管理" };
export const MODE_TEXT: Record<string, string> = { separator: "按分隔符", length: "按长度", heading: "按标题层级" };
export const STATUS_TEXT: Record<DocStatus, string> = {
  parsing: "解析中",
  pending_confirm: "待确认",
  embedding: "向量化中",
  ready: "可用",
  failed: "失败",
};
export const DEFAULT_CHUNK_CONFIG: ChunkConfig = {
  mode: "length",
  separator: "\\n\\n",
  chunk_size: 500,
  overlap: 50,
  heading_level: 2,
};

export const canEdit = (level: Level) => level === "edit" || level === "manage";
export const canManage = (level: Level) => level === "manage";

export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
