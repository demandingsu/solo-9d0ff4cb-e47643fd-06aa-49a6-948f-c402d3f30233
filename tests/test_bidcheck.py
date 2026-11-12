"""技术标匿名核查：ZIP/DOCX 检查与企业名称解析测试。"""

import io
import zipfile

from bidcheck import (
    FIELD_CREATOR,
    FIELD_FILENAME,
    FIELD_LAST_MODIFIED_BY,
    MAX_UNPACK_BYTES,
    CheckResult,
    check_zip,
    parse_companies,
)

ZIP_URL = "http://example.com/files/TB-01.zip"
PAGE_URL = "http://example.com/bid/BID-01.html"
COMPANIES = ["华宇建设有限公司", "宏远工程集团"]


def make_docx(creator="", last_modified_by="", include_core=True, stored_bytes=0):
    core = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<dc:creator>{creator}</dc:creator>"
        f"<cp:lastModifiedBy>{last_modified_by}</cp:lastModifiedBy>"
        "</cp:coreProperties>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", "<doc>正文</doc>")
        if include_core:
            z.writestr("docProps/core.xml", core)
        if stored_bytes:
            # 不压缩嵌入大文件，使 DOCX 文件本身达到指定大小
            z.writestr("word/media/big.bin", b"\0" * stored_bytes, compress_type=zipfile.ZIP_STORED)
    return buf.getvalue()


def make_zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


def check_one(data, companies=COMPANIES):
    return check_zip(data, companies, ZIP_URL, PAGE_URL)


# ---------- 命中 ----------


def test_hit_on_creator():
    hits, problems, docx_count = check_one(make_zip({"技术方案.docx": make_docx("华宇建设有限公司", "张三")}))
    assert not problems
    assert docx_count == 1
    assert len(hits) == 1
    h = hits[0]
    assert h.company == "华宇建设有限公司"
    assert h.field == FIELD_CREATOR
    assert h.value == "华宇建设有限公司"
    assert h.file == "技术方案.docx"
    assert h.zip_url == ZIP_URL
    assert h.page_url == PAGE_URL


def test_hit_on_last_modified_by():
    hits, problems, _ = check_one(make_zip({"施工组织设计.docx": make_docx("王五", "宏远工程集团")}))
    assert not problems
    assert [(h.company, h.field) for h in hits] == [("宏远工程集团", FIELD_LAST_MODIFIED_BY)]


def test_hit_on_filename():
    hits, problems, _ = check_one(make_zip({"技术标-宏远工程集团.docx": make_docx("李四", "李四")}))
    assert not problems
    assert [(h.company, h.field) for h in hits] == [("宏远工程集团", FIELD_FILENAME)]


def test_hit_on_filename_in_subdirectory():
    hits, _, _ = check_one(make_zip({"分册一/技术标-华宇建设有限公司.docx": make_docx("李四", "李四")}))
    assert [(h.company, h.field, h.file) for h in hits] == [("华宇建设有限公司", FIELD_FILENAME, "分册一/技术标-华宇建设有限公司.docx")]


def test_multiple_companies_in_one_field():
    hits, _, _ = check_one(make_zip({"a.docx": make_docx("华宇建设有限公司/宏远工程集团联合编制", "张三")}))
    assert {h.company for h in hits} == {"华宇建设有限公司", "宏远工程集团"}
    assert all(h.field == FIELD_CREATOR for h in hits)


def test_no_hit_when_company_not_listed():
    hits, problems, _ = check_one(make_zip({"a.docx": make_docx("未列入名单的公司", "某人")}))
    assert hits == []
    assert problems == []


def test_clean_zip_no_hit():
    hits, problems, docx_count = check_one(make_zip({"技术方案.docx": make_docx("赵六", "赵六")}))
    assert hits == []
    assert problems == []
    assert docx_count == 1


def test_non_docx_entries_ignored_for_hits():
    data = make_zip({"华宇建设有限公司-说明.txt": b"hello", "a.docx": make_docx("赵六", "赵六")})
    hits, problems, docx_count = check_one(data)
    assert hits == []
    assert problems == []
    assert docx_count == 1


def test_missing_core_xml_is_not_a_problem():
    hits, problems, docx_count = check_one(make_zip({"a.docx": make_docx(include_core=False)}))
    assert hits == []
    assert problems == []
    assert docx_count == 1


# ---------- 问题（无法核定） ----------


def test_corrupted_zip():
    hits, problems, docx_count = check_one(b"this is not a zip file at all")
    assert hits == []
    assert docx_count == 0
    assert len(problems) == 1
    assert problems[0].url == ZIP_URL
    assert problems[0].page_url == PAGE_URL
    assert "损坏" in problems[0].reason


def test_broken_docx_inside_zip():
    data = make_zip({"坏文件.docx": b"not really a docx"})
    hits, problems, _ = check_one(data)
    assert len(problems) == 1
    assert "坏文件.docx" in problems[0].reason


def test_unpack_over_20mib_aborts_zip():
    big_docx = make_docx("赵六", "赵六", stored_bytes=MAX_UNPACK_BYTES)  # 文件本身超过 20 MiB
    hits, problems, _ = check_one(make_zip({"大文件.docx": big_docx}))
    assert hits == []
    assert len(problems) == 1
    assert "20 MiB" in problems[0].reason
    assert problems[0].url == ZIP_URL


def test_hits_found_before_size_limit_are_kept():
    hit_docx = make_docx("华宇建设有限公司", "张三")
    big_docx = make_docx("赵六", "赵六", stored_bytes=MAX_UNPACK_BYTES)
    data = make_zip({"a-先查.docx": hit_docx, "z-大文件.docx": big_docx})
    hits, problems, _ = check_one(data)
    # 超限问题记录在案，但之前发现的命中仍然保留
    assert len(problems) == 1
    assert "20 MiB" in problems[0].reason
    assert [(h.company, h.field) for h in hits] == [("华宇建设有限公司", FIELD_CREATOR)]


# ---------- 结论 ----------


def test_verdict():
    r = CheckResult()
    assert r.verdict == "可送审"
    r.hits.append(object())
    assert r.verdict == "发现身份痕迹"
    r.problems.append(object())
    assert r.verdict == "无法核定"  # 有问题时优先于命中


# ---------- 企业名称解析 ----------


def test_parse_companies_multiline():
    assert parse_companies("甲公司\n乙公司\n丙公司\n") == ["甲公司", "乙公司", "丙公司"]


def test_parse_companies_separators_and_dedup():
    assert parse_companies("甲公司，乙公司、丙公司;甲公司\n 乙公司 ") == ["甲公司", "乙公司", "丙公司"]


def test_parse_companies_empty():
    assert parse_companies(" \n，、") == []
