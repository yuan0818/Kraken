import { useRef, useState } from "react";
import { Alert, App as AntApp, Button, Form } from "antd";
import { api, type Content } from "@kraken/api-client";
import { navigate, usePlatform } from "./state";
import { Back, NoData, PageHeading, shortId } from "./ui";
import { CaseFields } from "./case-fields";
import { markdownFromItems, markdownItems } from "./case-markdown";

type EditorValues = Omit<Content, "steps" | "assertions" | "preconditions"> & {
  name: string;
  code?: string;
  steps: string;
  assertions: string;
  preconditions?: string;
};
export function CaseEditorPage() {
  const { data, route } = usePlatform();
  if (!data) return null;
  const id = route.id === "new" ? undefined : route.id;
  const item = data.cases.find((c) => c.id === id);
  const versions = data.versions
    .filter((v) => v.case_id === id)
    .sort(
      (a, b) =>
        Date.parse(b.created_at) - Date.parse(a.created_at) ||
        b.created_at
          .replace(/Z$/, "")
          .localeCompare(a.created_at.replace(/Z$/, "")) ||
        b.id.localeCompare(a.id),
    );
  const back = route.query.get("back");
  const fromList = back && (back === "/cases" || back.startsWith("/cases?"));
  const version = route.version
    ? versions.find((v) => v.id === route.version)
    : fromList
      ? undefined
      : versions[0];
  const backHref = fromList
    ? `#${back}`
    : id
      ? `#/cases/${id}${route.version ? `?version=${encodeURIComponent(route.version)}` : ""}`
      : "#/cases";
  if ((id && !item) || (route.version && !version))
    return (
      <>
        <Back href={backHref} label="返回" />
        <NoData text={item ? "未找到指定的用例版本" : "未找到该用例"} />
      </>
    );
  const target = route.query.get("target") === "app" ? "app" : "android_os";
  return (
    <CaseEditor
      key={`${id ?? "new"}:${route.version ?? ""}:${target}`}
      item={item}
      seed={version?.content}
      sourceVersionId={version?.id}
      latestVersionId={fromList ? route.version : versions[0]?.id}
      target={target}
      backHref={backHref}
    />
  );
}

function CaseEditor({
  item,
  seed,
  sourceVersionId,
  latestVersionId,
  target,
  backHref,
}: {
  item?: { id: string; name: string; code: string };
  seed?: Content;
  sourceVersionId?: string;
  latestVersionId?: string;
  target: "app" | "android_os";
  backHref: string;
}) {
  const { data, busy, mutate } = usePlatform();
  const [form] = Form.useForm<EditorValues>();
  const [saving, setSaving] = useState(false);
  const createdCase = useRef<string | undefined>(undefined);
  const expectedLatestVersion = useRef(latestVersionId ?? null);
  const sourceVersion = useRef(sourceVersionId);
  const original = useRef(seed).current;
  const { message } = AntApp.useApp();
  const [initialValues] = useState<Partial<EditorValues>>(() => ({
    name: item?.name ?? "",
    code: item?.code ?? "",
    title: seed?.title ?? (item ? "修订版本" : "首次创建"),
    target: seed?.target ?? target,
    purpose: seed?.purpose || seed?.title || "",
    preconditions: seed?.preconditions?.join("\n") ?? "",
    module_id: seed?.module_id ?? undefined,
    app_id: seed?.app_id ?? undefined,
    android_version_ids: seed?.android_version_ids ?? [],
    app_version_ids: seed?.app_version_ids ?? [],
    device_model_ids: seed?.device_model_ids ?? [],
    chip_platform_ids: seed?.chip_platform_ids ?? [],
    level: seed?.level ?? "P2",
    status: seed?.status ?? "active",
    steps: seed?.steps_markdown ?? markdownFromItems(seed?.steps ?? []),
    assertions:
      seed?.assertions_markdown ?? markdownFromItems(seed?.assertions ?? []),
    required_tags: seed?.required_tags ?? ["simulator"],
  }));
  const save = async (values: EditorValues) => {
    const submittedFrom = window.location.hash;
    const stepsChanged = !original || values.steps !== initialValues.steps;
    const assertionsChanged =
      !original || values.assertions !== initialValues.assertions;
    setSaving(true);
    const content: Content = {
      case_code: "",
      schema_version: 2,
      purpose: values.purpose?.trim() ?? "",
      preconditions: (values.preconditions ?? "")
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean),
      module_id: values.module_id,
      app_id: values.target === "app" ? values.app_id : null,
      android_version_ids:
        values.target === "android_os"
          ? (values.android_version_ids ?? [])
          : [],
      app_version_ids:
        values.target === "app" ? (values.app_version_ids ?? []) : [],
      device_model_ids: values.device_model_ids ?? [],
      chip_platform_ids:
        values.target === "android_os" ? (values.chip_platform_ids ?? []) : [],
      level: values.level,
      status: values.status,
      title: values.title.trim(),
      target: values.target,
      steps: stepsChanged ? markdownItems(values.steps) : original.steps,
      assertions: assertionsChanged
        ? markdownItems(values.assertions)
        : original.assertions,
      steps_markdown: stepsChanged ? values.steps : original.steps_markdown,
      assertions_markdown: assertionsChanged
        ? values.assertions
        : original.assertions_markdown,
      required_tags: [
        ...new Set(
          (values.required_tags ?? []).map((s) => s.trim()).filter(Boolean),
        ),
      ],
    };
    const result = await mutate(async () => {
      const id =
        item?.id ??
        createdCase.current ??
        (
          await api.createCase(
            values.name.trim(),
            values.code?.trim() || undefined,
          )
        ).id;
      createdCase.current = id;
      const version = await api.addVersion(
        id,
        content,
        expectedLatestVersion.current,
      );
      return { caseId: id, versionId: version.id };
    });
    setSaving(false);
    if (result) {
      if (window.location.hash === submittedFrom)
        navigate(`/cases/${result.caseId}?version=${result.versionId}`);
      void message.success(
        item
          ? "修改已保存为新版本，Stable 保持不变"
          : "新版本已保存，设为 Stable 后可执行",
      );
    }
  };
  return (
    <>
      <Back
        href={backHref}
        label={backHref.startsWith("#/cases/") ? "返回用例详情" : "返回用例库"}
      />
      <PageHeading
        title={item ? "编辑用例" : "新建测试用例"}
        description={
          item
            ? `${item.code} · ${item.name}`
            : "定义用例的适用范围、操作步骤和预期结果。"
        }
      />
      <section className="panel editor-page">
        {sourceVersion.current && (
          <p className="form-intro">
            编辑基于版本{" "}
            <span className="mono">{shortId(sourceVersion.current)}</span>
            {" · "}
            {initialValues.title}
          </p>
        )}
        <Form
          name="case-editor"
          layout="vertical"
          form={form}
          initialValues={initialValues}
          scrollToFirstError
          onFinish={save}
          requiredMark="optional"
          disabled={saving}
          onValuesChange={(changed) => {
            if (changed.target)
              form.setFieldsValue({
                module_id: undefined,
                app_id: undefined,
                app_version_ids: [],
                android_version_ids: [],
                chip_platform_ids: [],
              });
            else if (changed.app_id)
              form.setFieldsValue({
                module_id: undefined,
                app_version_ids: [],
              });
          }}
        >
          <CaseFields
            form={form}
            catalogs={data?.catalogs ?? []}
            existing={!!item}
          />
          <Alert
            showIcon
            type="info"
            message="保存后需明确设为 Stable"
            description="新增版本不会改变当前 Stable，也不会影响已开始的执行。"
          />
          <div className="editor-page-actions">
            <Button href={backHref} disabled={saving}>
              取消
            </Button>
            <Button
              type="primary"
              htmlType="submit"
              loading={saving}
              disabled={busy && !saving}
            >
              {item ? "保存修改" : "保存版本"}
            </Button>
          </div>
        </Form>
      </section>
    </>
  );
}
