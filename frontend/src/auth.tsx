// 当前登录的账号。应用启动时读取一次，登录、改密后更新。
import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { Account, http, tokenStore } from "./api";

interface AuthState {
  account: Account | null;
  loading: boolean;
  signIn: (token: string, account: Account) => void;
  signOut: () => void;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthState>(null!);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [account, setAccount] = useState<Account | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    if (!tokenStore.get()) {
      setAccount(null);
      setLoading(false);
      return;
    }
    try {
      setAccount((await http.get<Account>("/auth/me")).data);
    } catch {
      setAccount(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const signIn = (token: string, next: Account) => {
    tokenStore.set(token);
    setAccount(next);
  };
  const signOut = () => {
    tokenStore.clear();
    setAccount(null);
  };

  return (
    <AuthContext.Provider value={{ account, loading, signIn, signOut, refresh }}>{children}</AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
