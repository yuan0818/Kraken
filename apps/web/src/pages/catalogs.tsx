import { useEffect, useState } from "react";
import {
  App as AntApp,
  Button,
  Form,
  Input,
  Modal,
  Select,
  Switch,
  Table,
  Tabs,
} from "antd";
import { api, type Catalog } from "@kraken/api-client";
import { usePlatform } from "../state";
import { Icon, PageHeading, Status } from "../ui";
import { catalogTitles, type CatalogKind } from "../case-model";
export function CatalogsPage() {
  const { data, busy, mutate } = usePlatform();
  const [kind, setKind] = useState<CatalogKind>("system_module");
  const [search, setSearch] = useState("");
  const [parent, setParent] = useState<string>();
  const [editor, setEditor] = useState<Catalog | "new" | null>(null);
  const [form] = Form.useForm();
  const { message, modal } = AntApp.useApp();
  const scoped = kind === "app_module" || kind === "app_version";
  const entries = data?.catalogs ?? [];
  const apps = entries.filter((c) => c.kind === "application");
  useEffect(() => {
    if (!editor) return;
    form.resetFields();
    form.setFieldsValue(
      editor === "new"
        ? { name: "", description: "", enabled: true, parent_id: parent }
        : editor,
    );
  }, [editor, form, parent]);
  const save = async (v: {
    name: string;
    description?: string;
    enabled: boolean;
    parent_id?: string;
  }) => {
    const common = {
      name: v.name.trim(),
      description: v.description ?? "",
      enabled: v.enabled,
    };
    const result = await mutate(() =>
      editor === "new"
        ? api.createCatalog({
            ...common,
            kind,
            parent_id: scoped ? v.parent_id : null,
          })
        : api.updateCatalog((editor as Catalog).id, common),
    );
    if (result) {
      setEditor(null);
      void message.success("基础数据已保存");
    }
  };
  const remove = async (id: string) => {
    const result = await mutate(() => api.deleteCatalog(id));
    if (result) void message.success("已删除");
  };
  const items = entries.filter(
    (c) =>
      c.kind === kind &&
      (!parent || !scoped || c.parent_id === parent) &&
      `${c.name} ${c.description}`.toLowerCase().includes(search.toLowerCase()),
  );
  return (
    <>
      <PageHeading
        eyebrow="CASE REFERENCE DATA"
        title="基础数据"
        description="维护用例的模块与适用范围，App 模块和版本归属到具体 App。"
        actions={
          <Button
            type="primary"
            icon={<Icon name="plus" />}
            disabled={busy}
            onClick={() => setEditor("new")}
          >
            新增{catalogTitles[kind]}
          </Button>
        }
      />
      <section className="panel list-panel">
        <Tabs
          className="catalog-tabs"
          activeKey={kind}
          onChange={(value) => {
            setKind(value as CatalogKind);
            setSearch("");
            setParent(undefined);
          }}
          items={(Object.entries(catalogTitles) as [CatalogKind, string][]).map(
            ([key, label]) => ({ key, label }),
          )}
        />
        <div className="filter-bar">
          <Input
            aria-label="搜索基础数据"
            className="search-input"
            prefix={<Icon name="search" size={17} />}
            placeholder={`搜索${catalogTitles[kind]}名称或说明`}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            allowClear
          />
          {scoped && (
            <Select
              aria-label="筛选所属 App"
              placeholder="全部 App"
              value={parent}
              onChange={setParent}
              allowClear
              options={apps.map((a) => ({ label: a.name, value: a.id }))}
            />
          )}
          <span className="filter-summary">{items.length} 条记录</span>
        </div>
        <Table<Catalog>
          className="platform-table"
          rowKey="id"
          dataSource={items}
          pagination={{ pageSize: 10, hideOnSinglePage: true }}
          scroll={{ x: 750 }}
          locale={{ emptyText: `暂无${catalogTitles[kind]}，点击右上角新增` }}
          columns={[
            {
              title: "名称",
              dataIndex: "name",
              render: (name) => (
                <strong className="catalog-name">{name}</strong>
              ),
            },
            ...(scoped
              ? [
                  {
                    title: "所属 App",
                    render: (_: unknown, c: Catalog) =>
                      apps.find((a) => a.id === c.parent_id)?.name ?? "—",
                  },
                ]
              : []),
            {
              title: "说明",
              dataIndex: "description",
              render: (description) => (
                <span className="muted">{description || "—"}</span>
              ),
            },
            {
              title: "状态",
              width: 90,
              render: (_, c) => (
                <Status
                  state={c.enabled ? "available" : "draft"}
                  label={c.enabled ? "启用" : "停用"}
                />
              ),
            },
            { title: "版本引用", width: 95, dataIndex: "reference_count" },
            {
              title: "操作",
              width: 160,
              render: (_, c) => (
                <div className="table-actions">
                  <Button
                    type="link"
                    size="small"
                    disabled={busy}
                    onClick={() => setEditor(c)}
                  >
                    编辑
                  </Button>
                  <Button
                    type="link"
                    size="small"
                    disabled={busy}
                    onClick={() =>
                      void mutate(() =>
                        api.updateCatalog(c.id, {
                          name: c.name,
                          description: c.description,
                          enabled: !c.enabled,
                        }),
                      )
                    }
                  >
                    {c.enabled ? "停用" : "启用"}
                  </Button>
                  <Button
                    type="link"
                    danger
                    size="small"
                    disabled={busy || c.reference_count > 0}
                    onClick={() =>
                      modal.confirm({
                        centered: true,
                        title: `删除「${c.name}」？`,
                        content: "已被用例版本或下级数据引用时不能删除。",
                        onOk: () => remove(c.id),
                        okText: "删除",
                        cancelText: "取消",
                        okButtonProps: { danger: true },
                      })
                    }
                  >
                    删除
                  </Button>
                </div>
              ),
            },
          ]}
        />
        <div className="panel-footnote">
          <Icon name="layers" size={15} />
          修改名称不会改写已保存的用例版本和执行快照。停用后不能新增引用，已有版本仍保留。
        </div>
      </section>
      <Modal
        open={editor !== null}
        centered
        title={`${editor === "new" ? "新增" : "编辑"}${catalogTitles[kind]}`}
        onCancel={() => !busy && setEditor(null)}
        footer={null}
        destroyOnHidden
      >
        <Form
          name="catalog-editor"
          form={form}
          layout="vertical"
          onFinish={save}
          disabled={busy}
        >
          {scoped && (
            <Form.Item
              name="parent_id"
              label="所属 App"
              rules={[{ required: true, message: "请选择 App" }]}
            >
              <Select
                showSearch
                optionFilterProp="label"
                disabled={editor !== "new"}
                placeholder="选择所属 App"
                options={apps.map((a) => ({
                  label: a.name + (a.enabled ? "" : "（已停用）"),
                  value: a.id,
                  disabled: !a.enabled,
                }))}
              />
            </Form.Item>
          )}
          <Form.Item
            name="name"
            label={`${catalogTitles[kind]}名称`}
            rules={[
              { required: true, whitespace: true, message: "请输入名称" },
            ]}
          >
            <Input
              maxLength={120}
              placeholder={
                kind === "app_version"
                  ? "例如：2.3.0"
                  : kind === "android_version"
                    ? "例如：Android 16"
                    : undefined
              }
            />
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input.TextArea rows={3} maxLength={1000} />
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
          <div className="modal-actions">
            <Button onClick={() => setEditor(null)} disabled={busy}>
              取消
            </Button>
            <Button
              type="primary"
              htmlType="submit"
              aria-label="保存基础数据"
              loading={busy}
            >
              保存
            </Button>
          </div>
        </Form>
      </Modal>
    </>
  );
}
