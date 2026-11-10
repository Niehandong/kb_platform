// 按关键字查找账号或分组，用于授权和添加分组成员。value 形如 "account:<id>" 或 "group:<id>"。
import { Select, Spin } from "antd";
import { useEffect, useRef, useState } from "react";
import { Brief, http } from "../api";

interface Props {
  value?: string | string[];
  onChange?: (value: string | string[]) => void;
  kinds?: ("account" | "group")[];
  multiple?: boolean;
  placeholder?: string;
}

export default function SubjectSelect({ value, onChange, kinds = ["group", "account"], multiple, placeholder }: Props) {
  const [options, setOptions] = useState<{ label: string; options: { value: string; label: string }[] }[]>([]);
  const [loading, setLoading] = useState(false);
  const timer = useRef<number>();

  const search = (keyword: string) => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(async () => {
      setLoading(true);
      try {
        const { data } = await http.get<{ accounts: Brief[]; groups: { id: string; name: string }[] }>("/directory", {
          params: { keyword },
        });
        const next = [];
        if (kinds.includes("group") && data.groups.length) {
          next.push({ label: "分组", options: data.groups.map((g) => ({ value: `group:${g.id}`, label: g.name })) });
        }
        if (kinds.includes("account") && data.accounts.length) {
          next.push({
            label: "账号",
            options: data.accounts.map((a) => ({
              value: `account:${a.id}`,
              label: `${a.display_name}（${a.username}）${a.account_type === "service" ? " 服务账号" : ""}`,
            })),
          });
        }
        setOptions(next);
      } finally {
        setLoading(false);
      }
    }, 250);
  };

  useEffect(() => {
    search("");
    return () => window.clearTimeout(timer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <Select
      showSearch
      mode={multiple ? "multiple" : undefined}
      value={value}
      onChange={onChange}
      onSearch={search}
      filterOption={false}
      options={options}
      placeholder={placeholder ?? "输入名称或登录名查找"}
      notFoundContent={loading ? <Spin size="small" /> : "没有找到"}
      style={{ width: "100%" }}
    />
  );
}
