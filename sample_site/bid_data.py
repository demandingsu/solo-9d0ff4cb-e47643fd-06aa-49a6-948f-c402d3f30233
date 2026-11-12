"""示例站技术标附件数据：按标段生成投标详情与 ZIP 附件（内存中生成，不落盘）。

样例覆盖核查工具需要演示的全部情形：
- TB-01.zip：DOCX 的 creator 含企业名称（命中 creator）
- TB-02.zip：DOCX 文件名含企业名称（命中文件名）
- TB-03.zip：DOCX 的 lastModifiedBy 含企业名称（命中 lastModifiedBy）
- TB-04.zip：干净附件（无命中）
- TB-05.zip：损坏的附件（不是有效 ZIP）
- TB-06.zip：累计解压超过 20 MiB（内含一个超大 DOCX）
- TB-07.zip：干净附件；其详情页可用于“页面读取失败”故障注入
- TB-11.zip / TB-12.zip：第二标段 BD-2026-02，全部干净，用于演示“可送审”
- TB-21.zip / TB-22.zip：第三标段 BD-2026-03，有命中但无问题附件，用于演示“发现身份痕迹”
"""

import io
import zipfile
from xml.sax.saxutils import escape

SECTION_NO = "BD-2026-01"
BID_PAGE_SIZE = 3  # 每页 3 条，7 条数据 → 3 页，演示分页抓取

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
</Types>"""

_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

_CORE_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <dc:creator>{creator}</dc:creator>
  <cp:lastModifiedBy>{last_modified_by}</cp:lastModifiedBy>
  <dc:title>技术标文件</dc:title>
</cp:coreProperties>"""

_DOCUMENT_HEAD = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>"""

_DOCUMENT_TAIL = "</w:t></w:r></w:p></w:body></w:document>"


def make_docx(creator: str, last_modified_by: str, stored_bytes: int = 0) -> bytes:
    """生成一个最小 DOCX（含 docProps/core.xml）。

    stored_bytes 以不压缩方式在 DOCX 内嵌入大文件（如图纸），
    使 DOCX 文件本身达到指定大小——外层 ZIP 解压该条目时即计入此体积。
    """
    core = _CORE_XML.format(creator=escape(creator), last_modified_by=escape(last_modified_by))
    document = _DOCUMENT_HEAD + "技术标正文。" + _DOCUMENT_TAIL
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/document.xml", document)
        z.writestr("docProps/core.xml", core)
        if stored_bytes:
            z.writestr("word/media/drawings.bin", b"\0" * stored_bytes, compress_type=zipfile.ZIP_STORED)
    return buf.getvalue()


def make_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


# 超大 DOCX：内嵌约 21 MiB 不压缩“图纸”，使 TB-06.zip 累计解压超过 20 MiB 上限
_BIG_STORED = 21 * 1024 * 1024

BIDS = [
    {
        "bid_id": "BID-2026-01",
        "section": SECTION_NO,
        "code": "暗标编号 001",
        "submit_time": "2026-11-10 09:12",
        "files": ["TB-01.zip"],
    },
    {
        "bid_id": "BID-2026-02",
        "section": SECTION_NO,
        "code": "暗标编号 002",
        "submit_time": "2026-11-10 10:03",
        "files": ["TB-02.zip"],
    },
    {
        "bid_id": "BID-2026-03",
        "section": SECTION_NO,
        "code": "暗标编号 003",
        "submit_time": "2026-11-10 11:47",
        "files": ["TB-03.zip"],
    },
    {
        "bid_id": "BID-2026-04",
        "section": SECTION_NO,
        "code": "暗标编号 004",
        "submit_time": "2026-11-10 14:20",
        "files": ["TB-04.zip"],
    },
    {
        "bid_id": "BID-2026-05",
        "section": SECTION_NO,
        "code": "暗标编号 005",
        "submit_time": "2026-11-10 15:31",
        "files": ["TB-05.zip"],
    },
    {
        "bid_id": "BID-2026-06",
        "section": SECTION_NO,
        "code": "暗标编号 006",
        "submit_time": "2026-11-10 16:08",
        "files": ["TB-06.zip"],
    },
    {
        "bid_id": "BID-2026-07",
        "section": SECTION_NO,
        "code": "暗标编号 007",
        "submit_time": "2026-11-10 16:55",
        "files": ["TB-07.zip"],
    },
    # 第二标段：全部干净，用于演示“可送审”结论
    {
        "bid_id": "BID-2026-11",
        "section": "BD-2026-02",
        "code": "暗标编号 001",
        "submit_time": "2026-11-11 09:05",
        "files": ["TB-11.zip"],
    },
    {
        "bid_id": "BID-2026-12",
        "section": "BD-2026-02",
        "code": "暗标编号 002",
        "submit_time": "2026-11-11 10:40",
        "files": ["TB-12.zip"],
    },
    # 第三标段：有命中但无问题附件，用于演示“发现身份痕迹”结论
    {
        "bid_id": "BID-2026-21",
        "section": "BD-2026-03",
        "code": "暗标编号 001",
        "submit_time": "2026-11-12 09:00",
        "files": ["TB-21.zip"],
    },
    {
        "bid_id": "BID-2026-22",
        "section": "BD-2026-03",
        "code": "暗标编号 002",
        "submit_time": "2026-11-12 10:30",
        "files": ["TB-22.zip"],
    },
]


def _build_zip_files() -> dict[str, bytes]:
    return {
        # creator 命中：华宇建设有限公司
        "TB-01.zip": make_zip({"技术方案.docx": make_docx("华宇建设有限公司", "张三")}),
        # 文件名命中：宏远工程集团
        "TB-02.zip": make_zip({"技术标-宏远工程集团.docx": make_docx("李四", "李四")}),
        # lastModifiedBy 命中：中建科工股份有限公司
        "TB-03.zip": make_zip({"施工组织设计.docx": make_docx("王五", "中建科工股份有限公司")}),
        # 干净附件
        "TB-04.zip": make_zip({"技术方案.docx": make_docx("赵六", "赵六")}),
        # 损坏附件：不是有效 ZIP
        "TB-05.zip": ("PK\x03\x04 这不是有效的 ZIP 内容，模拟下载到损坏的附件。" * 4).encode("utf-8"),
        # 解压超限：干净的小 DOCX + 超大 DOCX（累计解压 > 20 MiB）
        "TB-06.zip": make_zip(
            {
                "技术方案.docx": make_docx("钱七", "钱七"),
                "附图-全套图纸.docx": make_docx("钱七", "钱七", stored_bytes=_BIG_STORED),
            }
        ),
        # 干净附件（详情页可用于故障注入演示“页面读取失败”）
        "TB-07.zip": make_zip({"技术方案.docx": make_docx("孙八", "孙八")}),
        # 第二标段：全部干净
        "TB-11.zip": make_zip({"技术方案.docx": make_docx("周九", "周九")}),
        "TB-12.zip": make_zip({"施工组织设计.docx": make_docx("吴十", "吴十")}),
        # 第三标段：creator 命中 华宇建设有限公司 + 干净附件
        "TB-21.zip": make_zip({"技术方案.docx": make_docx("华宇建设有限公司", "郑一")}),
        "TB-22.zip": make_zip({"技术方案.docx": make_docx("冯二", "冯二")}),
    }


ZIP_FILES = _build_zip_files()


def sorted_bids():
    """列表页按提交时间倒序展示。"""
    return sorted(BIDS, key=lambda b: (b["submit_time"], b["bid_id"]), reverse=True)


def get_bid(bid_id: str):
    for b in BIDS:
        if b["bid_id"] == bid_id:
            return b
    return None
