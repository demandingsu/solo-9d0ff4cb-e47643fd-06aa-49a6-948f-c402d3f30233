# 截标时间核对工具

面向采购专员 / 评标秘书的网页工具：
- **截标时间核对**：从公开招标公告站抓取公告，按项目串起公告链，
  自动应用更正与撤销，给出每个项目的**最终截标时间 / 停办状态**及**依据链接**。
- **技术标匿名核查**：匿名技术标送审前，抓取技术标目录的同源分页与详情页，
  下载 ZIP 附件并检查其中 DOCX 的**文件名**及文档属性
  （`creator` / `lastModifiedBy`）是否包含投标企业名称，防止身份泄露。

仓库包含两个服务：

| 服务 | 目录 | 说明 |
| --- | --- | --- |
| 核对工具 | `app/` | FastAPI 网页：截标核对 + 技术标匿名核查，SQLite 保存采集轮次 |
| 示例公告站 | `sample_site/` | FastAPI 模拟公告站：3 页公告列表 + 11 条公告详情（含更正、撤销），另有 3 个标段的技术标附件目录（含身份痕迹 / 损坏 / 超限样例），支持故障注入 |

## 快速开始（Docker Compose）

```bash
docker compose up --build
```

- 核对工具：http://localhost:8000/ （打开后点击 **开始抓取**，默认已填入示例站列表网址）
- 技术标匿名核查：http://localhost:8000/check （默认已填入示例标段与企业名单，直接点 **开始核查**）
- 示例公告站：http://localhost:8080/list.html?page=1
- 示例技术标目录：http://localhost:8080/bids.html?section=BD-2026-01&page=1

## 功能说明

### 截标时间核对（首页）

1. **抓取**：输入与示例站同结构的列表网址，点击抓取。工具只跟随与起始网址
   **同源**的分页链接（`nav.pagination`）与详情链接（`ul.notice-list`），
   逐个读取详情页的：项目编号、公告编号、公告类型、发布时间、截标时间、
   更正/撤销所指公告编号。
2. **分析**：同一项目编号的公告按发布时间串链——
   - 招标公告确立截标时间；
   - 更正公告更新截标时间（链上记录 `旧值 → 新值`）；
   - 撤销公告将项目标记为 **停办**。
   页面展示每个项目的最终状态、当前截标时间，以及每条公告的来源链接（依据）。
3. **轮次持久化（SQLite）**：每次抓取是一个轮次，公告快照挂在轮次下，
   保存于 SQLite（容器卷 `app-data`，`/data/app.db`）。
   - **任一页面失败**：轮次标记失败并记录出错网址与原因，页面红色横幅提示，
     项目结果仍展示上一轮成功数据；
   - **重抓不叠加**：公告只在整轮全部成功后于单事务内写入，失败轮次不留
     半截数据；页面始终只展示最近一次成功轮次的结果。

### 技术标匿名核查（/check）

1. **输入**：标段号、该标段投标企业名称（每行一个）、技术标目录 URL。
2. **抓取**：从目录 URL 出发，跟随同源分页（`nav.pagination`）与投标详情
   链接（`ul.bid-list`），在详情页收集 ZIP 附件链接（`ul.attachments` 中的
   `.zip`），逐一下载。
3. **检查**：对每份 ZIP 中的每个 DOCX 检查——
   - **文件名**是否包含任一企业名称；
   - `docProps/core.xml` 中的 **creator**、**lastModifiedBy** 是否包含任一企业名称。
4. **结论**：
   - **可送审**（绿）：全部附件查完且无任何命中；
   - **发现身份痕迹**（红）：列出命中清单（企业、ZIP 内文件、字段、字段内容、
     来源附件及详情页链接）与清理提示，暂不可送审；
   - **无法核定**（黄）：存在页面读取失败、附件损坏（ZIP/DOCX 无法解压）或
   单份 ZIP **累计解压超过 20 MiB** 的网址——列出问题网址清单，已发现的
     命中仍然展示，需处理后重新核查。
   - 单点失败不中断整体核查；核查结果不落库，提交即查即得。

示例站技术标附件区内置三个标段，可演示全部结论：

| 标段 | 内容 | 预期结论 |
| --- | --- | --- |
| `BD-2026-01` | creator / 文件名 / lastModifiedBy 三处命中 + 损坏附件 + 解压超 20 MiB 附件 | 无法核定（含命中与问题网址） |
| `BD-2026-02` | 全部干净 | 可送审 |
| `BD-2026-03` | 一处 creator 命中，附件均可读 | 发现身份痕迹 |

## 演示失败处理

```bash
# 公告采集：开启故障注入，详情页 NT-2026-0004 返回 500
curl "http://localhost:8080/admin/fail?on=1"
# 在 http://localhost:8000/ 点击“开始抓取” → 第 N 轮失败，报出该网址，结果仍是上一轮
curl "http://localhost:8080/admin/fail?on=0"   # 关闭后重抓 → 新一轮成功，数据整体替换

# 匿名核查：开启投标详情故障注入，BID-2026-07 返回 500
curl "http://localhost:8080/admin/bidfail?on=1"
# 在 http://localhost:8000/check 核查 BD-2026-01 → 问题网址多出一条“页面读取失败：HTTP 500”
curl "http://localhost:8080/admin/bidfail?on=0"
```

## 页面结构约定（解析契约）

输入网址须与示例站同结构：

- 公告列表页 `list.html?page=N`：公告链接在 `ul.notice-list a`，分页链接在 `nav.pagination a`；
- 公告详情页 `detail/<公告编号>.html`：容器 `div.notice[data-notice-type]`
  （`tender`/`correction`/`cancellation`），字段元素
  `#project-no`、`#notice-no`、`#publish-time`、`#deadline`（如有）、
  `#target-notice`（更正/撤销必填）；
- 技术标目录页 `bids.html?section=<标段号>&page=N`：投标详情链接在
  `ul.bid-list a`，分页链接在 `nav.pagination a`；
- 投标详情页 `bid/<投标编号>.html`：ZIP 附件链接在 `ul.attachments a`（`.zip` 结尾）。

## 配置（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DB_PATH` | `/data/app.db`（容器） | SQLite 文件路径 |
| `DEFAULT_START_URL` | `http://sample-site:8000/list.html?page=1` | 抓取表单预填的列表网址 |
| `DEFAULT_CHECK_URL` | `http://sample-site:8000/bids.html?section=BD-2026-01&page=1` | 核查表单预填的技术标目录网址 |
| `DEFAULT_CHECK_SECTION` | `BD-2026-01` | 核查表单预填的标段号 |
| `DEFAULT_CHECK_COMPANIES` | 见 compose | 核查表单预填的企业名单（每行一个） |
| `URL_REWRITE_FROM` / `URL_REWRITE_TO` | 见 compose | 把结果页中的内部网址改写为浏览器可访问地址 |

## 本地开发与测试

```bash
pip install -r app/requirements.txt -r sample_site/requirements.txt pytest
python -m pytest tests/            # 单元测试：公告链分析 + 详情页解析 + 匿名核查
# 手动联调：
cd sample_site && uvicorn main:app --port 8080 &
cd app && DB_PATH=/tmp/app.db DEFAULT_START_URL=http://localhost:8080/list.html?page=1 \
  DEFAULT_CHECK_URL="http://localhost:8080/bids.html?section=BD-2026-01&page=1" \
  uvicorn main:app --port 8000
```
