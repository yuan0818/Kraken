import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { App as AntApp } from "antd";
import { api, type Overview } from "@kraken/api-client";
export type Section =
  | "overview"
  | "cases"
  | "imports"
  | "runs"
  | "executions"
  | "devices"
  | "nodes"
  | "schedule"
  | "reports"
  | "settings"
  | "models"
  | "catalogs";
export type Route = {
  section: Section;
  id?: string;
  view?: string;
  node?: string;
  version?: string;
  query: URLSearchParams;
};
export const navTitles: Record<Section, string> = {
  overview: "总览",
  cases: "用例管理",
  imports: "导入中心",
  runs: "测试任务",
  executions: "执行记录",
  devices: "设备中心",
  nodes: "执行节点",
  schedule: "定时计划",
  reports: "测试报告",
  settings: "平台设置",
  models: "模型配置",
  catalogs: "基础数据",
};
export function parseRoute(): Route {
  const [path, search] = window.location.hash.slice(1).split("?");
  const parts = (path || "/overview").split("/").filter(Boolean);
  const query = new URLSearchParams(search);
  return {
    section: Object.hasOwn(navTitles, parts[0])
      ? (parts[0] as Section)
      : "overview",
    id: parts[1],
    view: parts[2],
    node: query.get("node") ?? undefined,
    version: query.get("version") ?? undefined,
    query,
  };
}
export function navigate(path: string) {
  window.location.hash = path;
}
type Context = {
  data?: Overview;
  error: string;
  loading: boolean;
  busy: boolean;
  updated?: Date;
  route: Route;
  refresh: () => Promise<void>;
  mutate: <T>(fn: () => Promise<T>) => Promise<T | undefined>;
  taskCase: string | null;
  setTaskCase: (v: string | null) => void;
};
const PlatformContext = createContext<Context | null>(null);
export function PlatformProvider({ children }: { children: ReactNode }) {
  const [data, setData] = useState<Overview>();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [updated, setUpdated] = useState<Date>();
  const [route, setRoute] = useState(parseRoute);
  const [taskCase, setTaskCase] = useState<string | null>(null);
  const inFlight = useRef<Promise<void> | null>(null);
  const mounted = useRef(true);
  const { message } = AntApp.useApp();
  const refresh = useCallback(() => {
    if (inFlight.current) return inFlight.current;
    const promise = (async () => {
      try {
        const result = await api.overview();
        if (mounted.current) {
          setData(result);
          setError("");
          setUpdated(new Date());
        }
      } catch (e) {
        if (mounted.current)
          setError(e instanceof Error ? e.message : "无法连接控制面");
      } finally {
        if (mounted.current) setLoading(false);
        inFlight.current = null;
      }
    })();
    inFlight.current = promise;
    return promise;
  }, []);
  useEffect(() => {
    mounted.current = true;
    void refresh();
    const timer = setInterval(() => void refresh(), 3000);
    const change = () => {
      setRoute(parseRoute());
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", change);
    return () => {
      mounted.current = false;
      clearInterval(timer);
      window.removeEventListener("hashchange", change);
    };
  }, [refresh]);
  const mutate = async <T,>(fn: () => Promise<T>) => {
    setBusy(true);
    try {
      const value = await fn();
      if (inFlight.current) await inFlight.current;
      await refresh();
      return value;
    } catch (e) {
      void message.error(e instanceof Error ? e.message : "操作失败，请重试");
      return undefined;
    } finally {
      setBusy(false);
    }
  };
  return (
    <PlatformContext.Provider
      value={{
        data,
        error,
        loading,
        busy: busy || !!error,
        updated,
        route,
        refresh,
        mutate,
        taskCase,
        setTaskCase,
      }}
    >
      {children}
    </PlatformContext.Provider>
  );
}
export function usePlatform() {
  const context = useContext(PlatformContext);
  if (!context) throw new Error("Platform provider missing");
  return context;
}
