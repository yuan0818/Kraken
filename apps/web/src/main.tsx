import "@ant-design/v5-patch-for-react-19";
import { createRoot } from "react-dom/client";
import { App as AntApp, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { PlatformProvider } from "./state";
import { Platform } from "./app";
import "./style.css";

createRoot(document.getElementById("root")!).render(
  <ConfigProvider
    locale={zhCN}
    theme={{
      token: {
        colorPrimary: "#3761e9",
        colorText: "#243047",
        colorTextSecondary: "#7d879b",
        colorBorder: "#e1e6ef",
        borderRadius: 7,
        fontFamily:
          'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
        fontSize: 13,
        controlHeight: 36,
      },
      components: {
        Button: { primaryShadow: "0 2px 3px rgba(55,97,233,.12)" },
        Table: {
          headerBg: "#fafbfd",
          headerColor: "#7c879a",
          headerSplitColor: "transparent",
          cellPaddingBlock: 17,
        },
        Segmented: { trackBg: "#f3f5f9", itemSelectedColor: "#315bd6" },
        Drawer: { paddingLG: 24 },
      },
    }}
  >
    <AntApp>
      <PlatformProvider>
        <Platform />
      </PlatformProvider>
    </AntApp>
  </ConfigProvider>,
);
