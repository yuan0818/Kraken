import { Form, Input, Select, type FormInstance } from "antd";
import type { Catalog } from "@kraken/api-client";
import { catalogOptions, caseStatuses, levels } from "./case-model";
import { MarkdownInput, validateCaseMarkdown } from "./case-markdown";
export function CaseFields({
  form,
  catalogs,
  existing,
}: {
  form: FormInstance;
  catalogs: Catalog[];
  existing: boolean;
}) {
  const target = Form.useWatch("target", form) ?? "android_os";
  const appId = Form.useWatch("app_id", form);
  const os = target === "android_os";
  const required = [
    { required: true, whitespace: true, message: "请填写此项" },
  ];
  return (
    <>
      <div className="form-section-title">基本信息</div>
      <div className="case-form-grid">
        <Form.Item
          name="code"
          label="用例编号"
          extra={
            existing
              ? "编号属于用例，跨版本保持不变。"
              : "留空自动生成，也可使用自己的编号。"
          }
          rules={[
            {
              pattern: /^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$/,
              message: "使用字母、数字、点、短横线或下划线，最多 80 位",
            },
          ]}
        >
          <Input
            disabled={existing}
            placeholder="例如：OS-SETTINGS-001"
            maxLength={80}
          />
        </Form.Item>
        <Form.Item
          name="name"
          label="用例名称"
          rules={existing ? [] : required}
        >
          <Input
            disabled={existing}
            placeholder="例如：Wi-Fi 开关状态验证"
            maxLength={200}
          />
        </Form.Item>
        <Form.Item name="target" label="被测对象" rules={[{ required: true }]}>
          <Select
            options={[
              { value: "android_os", label: "Android OS" },
              { value: "app", label: "App" },
            ]}
          />
        </Form.Item>
        <Form.Item name="title" label="版本说明" rules={required}>
          <Input maxLength={200} placeholder="例如：补充首次启动场景" />
        </Form.Item>
        <Form.Item name="level" label="级别" rules={[{ required: true }]}>
          <Select options={levels.map((value) => ({ value, label: value }))} />
        </Form.Item>
        <Form.Item name="status" label="状态" rules={[{ required: true }]}>
          <Select
            options={Object.entries(caseStatuses).map(([value, label]) => ({
              value,
              label,
            }))}
          />
        </Form.Item>
      </div>
      <div className="form-section-title">
        <span>归属与适用范围</span>
        <a href="#/catalogs" target="_blank" rel="noreferrer">
          管理基础数据 ↗
        </a>
      </div>
      <div className="case-form-grid">
        {!os && (
          <Form.Item
            name="app_id"
            label="App"
            rules={[{ required: true, message: "请选择 App" }]}
          >
            <Select
              placeholder="选择 App"
              showSearch
              optionFilterProp="label"
              options={catalogOptions(catalogs, "application")}
            />
          </Form.Item>
        )}
        <Form.Item
          name="module_id"
          label={os ? "系统模块" : "App 模块"}
          rules={[{ required: true, message: "请选择模块" }]}
        >
          <Select
            placeholder={
              os ? "选择系统模块" : appId ? "选择 App 模块" : "请先选择 App"
            }
            showSearch
            optionFilterProp="label"
            disabled={!os && !appId}
            options={catalogOptions(
              catalogs,
              os ? "system_module" : "app_module",
              os ? undefined : appId,
            )}
          />
        </Form.Item>
        {os ? (
          <Form.Item name="android_version_ids" label="适用 Android 版本">
            <Select
              mode="multiple"
              allowClear
              placeholder="不限 Android 版本"
              optionFilterProp="label"
              options={catalogOptions(catalogs, "android_version")}
            />
          </Form.Item>
        ) : (
          <Form.Item name="app_version_ids" label="适用 App 版本">
            <Select
              mode="multiple"
              allowClear
              placeholder={appId ? "不限 App 版本" : "请先选择 App"}
              disabled={!appId}
              optionFilterProp="label"
              options={
                appId ? catalogOptions(catalogs, "app_version", appId) : []
              }
            />
          </Form.Item>
        )}
        <Form.Item name="device_model_ids" label="适用机型">
          <Select
            mode="multiple"
            allowClear
            placeholder="不限机型"
            optionFilterProp="label"
            options={catalogOptions(catalogs, "device_model")}
          />
        </Form.Item>
        {os && (
          <Form.Item name="chip_platform_ids" label="适用芯片平台">
            <Select
              mode="multiple"
              allowClear
              placeholder="不限芯片平台"
              optionFilterProp="label"
              options={catalogOptions(catalogs, "chip_platform")}
            />
          </Form.Item>
        )}
      </div>
      <p className="form-scope-note">
        未选适用范围表示不限。同一项可匹配任一选中值，不同项需同时满足；只有已上报匹配资料的设备才会被调度。
      </p>
      <div className="form-section-title">测试内容</div>
      <Form.Item name="purpose" label="测试目的" rules={required}>
        <Input.TextArea
          rows={2}
          maxLength={4000}
          placeholder="描述本用例要验证的功能或行为"
        />
      </Form.Item>
      <Form.Item
        name="preconditions"
        label="前置条件"
        extra="每行一个条件；没有特殊条件可留空。"
      >
        <Input.TextArea
          rows={3}
          placeholder={"设备完成开机向导\n已连接测试 Wi-Fi"}
        />
      </Form.Item>
      <div className="case-form-grid">
        <Form.Item
          name="steps"
          label="步骤"
          extra="按执行顺序填写，每行或每个列表项算一步。"
          validateTrigger="onBlur"
          rules={[{ required: true, validator: validateCaseMarkdown }]}
        >
          <MarkdownInput
            label="步骤"
            placeholder={
              "1. 打开 **系统设置**\n2. 进入网络设置\n3. 切换 Wi-Fi 开关"
            }
          />
        </Form.Item>
        <Form.Item
          name="assertions"
          label="预期结果"
          extra="每行或每个列表项填写一条可观察的预期结果。"
          validateTrigger="onBlur"
          rules={[{ required: true, validator: validateCaseMarkdown }]}
        >
          <MarkdownInput
            label="预期结果"
            placeholder={"- Wi-Fi 开关显示 **已开启**\n- 状态栏显示 Wi-Fi 图标"}
          />
        </Form.Item>
      </div>
      <Form.Item
        name="required_tags"
        label="附加设备标签"
        extra="匹配具备全部标签的设备；当前模拟节点使用 simulator 标签。"
      >
        <Select
          mode="tags"
          placeholder="输入标签后回车"
          tokenSeparators={[",", "，"]}
        />
      </Form.Item>
    </>
  );
}
