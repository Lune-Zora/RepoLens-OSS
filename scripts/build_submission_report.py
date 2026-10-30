"""Build editable reports from reviewed Markdown and the compliance register."""
from pathlib import Path
import json
import re
from docx import Document
from docx.shared import Cm, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.section import WD_SECTION_START, WD_ORIENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/02_架构/技术报告.md"
REFERENCES = {}


def setup(document):
    section = document.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.top_margin = section.bottom_margin = Cm(2.5)
    section.left_margin = section.right_margin = Cm(3)
    section.header_distance = section.footer_distance = Cm(1.5)
    for name in ["Normal", "Body Text", "List Bullet", "Title", "Heading 1", "Heading 2"]:
        style = document.styles[name]
        style.font.name = "宋体"
        style.element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "宋体")
        style.font.size = Pt({"Title": 22, "Heading 1": 16, "Heading 2": 14}.get(name, 12))
        style.font.color.rgb = RGBColor(0, 0, 0)
        style.paragraph_format.line_spacing = 1
        style.paragraph_format.space_after = Pt(6)
        if name.startswith("Heading"):
            style.font.bold = True
            style.paragraph_format.keep_with_next = True
    section.header.paragraphs[0].text = "RepoLens-OSS 技术报告"
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.add_run("第 ")
    for name in ["PAGE", "NUMPAGES"]:
        field(footer, name)
        footer.add_run(" 页 / 共 " if name == "PAGE" else " 页")
    document.core_properties.author = "RepoLens-OSS Team"


def field(paragraph, code):
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instruction = OxmlElement("w:instrText")
    instruction.text = " " + code + " "
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.extend([begin, instruction, end])


def clean(text):
    def replace(match):
        label, path = match.groups()
        if path not in REFERENCES:
            REFERENCES[path] = (len(REFERENCES) + 1, label)
        return label + "〔E%02d〕" % REFERENCES[path][0]
    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", replace, str(text)).replace("**", "").replace("`", "")


def paragraph(document, text, style=None):
    return document.add_paragraph(clean(text), style=style)


def table(document, rows, widths=None, font_size=9):
    result = document.add_table(rows=0, cols=len(rows[0]))
    result.style = "Table Grid"
    result.autofit = False
    for column, width in zip(result.columns, widths or [15 / len(rows[0])] * len(rows[0])):
        column.width = Cm(width)
    for index, row in enumerate(rows):
        cells = result.add_row().cells
        for cell, text in zip(cells, row):
            cell.text = clean(text)
            for item in cell.paragraphs:
                item.paragraph_format.space_after = Pt(2)
                item.paragraph_format.line_spacing = 1
                for run in item.runs:
                    run.font.size = Pt(font_size)
                    run.bold = index == 0
        properties = result.rows[-1]._tr.get_or_add_trPr()
        properties.append(OxmlElement("w:cantSplit"))
        if index == 0:
            properties.append(OxmlElement("w:tblHeader"))
    return result


def landscape(document, new_section=True):
    section = document.add_section(WD_SECTION_START.NEW_PAGE) if new_section else document.sections[0]
    section.orientation = WD_ORIENT.LANDSCAPE
    section.page_width, section.page_height = Cm(29.7), Cm(21)
    section.left_margin = section.right_margin = Cm(1.2)
    section.top_margin = section.bottom_margin = Cm(1.5)


def resource_table(document, entries):
    rows = [["序号", "资源名称及类型", "版本/来源", "许可证/授权类型", "使用/开放方式",
             "关键义务/限制", "团队自主修改或开发内容", "合规状态"]]
    for index, item in enumerate(entries, 1):
        rows.append([str(index), item["name"], item["version_source"], item["license"],
                     item["usage"], item["duty"], item["own"], item["status"]])
    table(document, rows, [0.7, 2.7, 3.3, 2.8, 3.5, 4, 4.8, 1.5], 8)


def build():
    entries = json.loads((ROOT / "reports/compliance/verification-20261030/resource-register.json").read_text(encoding="utf-8"))
    document = Document()
    setup(document)
    for text in ["2026年第八届", "全球校园人工智能算法精英大赛", "算法主题赛", "AI+开源", "技术报告"]:
        item = paragraph(document, text)
        item.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for run in item.runs:
            run.font.size = Pt(20 if text == "技术报告" else 16)
    for text in ["", "团队名称：请按报名信息填写", "参赛编号：请按报名信息填写",
                 "作品名称：RepoLens-OSS", "参赛方向：AI开发工具与开源协作", "日期：2026年10月30日"]:
        paragraph(document, text)
    document.add_page_break()
    paragraph(document, "目  录", "Title")
    field(document.add_paragraph(), 'TOC ' + chr(92) + 'o "1-2" ' + chr(92) + 'h')
    document.add_page_break()
    paragraph(document, "作品简介", "Heading 1")
    paragraph(document, (ROOT / "docs/01_需求/作品简介-提交用.txt").read_text(encoding="utf-8"))
    document.add_page_break()
    lines = SOURCE.read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        text = lines[index].strip()
        if not text or text.startswith("# ") or text.startswith(">"):
            index += 1
            continue
        if text.startswith("|"):
            rows = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                cells = [value.strip() for value in lines[index].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-+:?", value) for value in cells):
                    rows.append(cells)
                index += 1
            table(document, rows, [3, 6, 6] if len(rows[0]) == 3 else None)
            continue
        if text.startswith("### "):
            paragraph(document, text[4:], "Heading 2")
        elif text.startswith("## "):
            paragraph(document, text[3:], "Heading 1")
        elif text.startswith("- "):
            paragraph(document, text[2:], "List Bullet")
        else:
            paragraph(document, text)
        index += 1
    paragraph(document, "附录A：证据与来源定位", "Heading 1")
    rows = [["编号", "材料", "仓库相对路径"]]
    for path, (index, label) in REFERENCES.items():
        resolved = (SOURCE.parent / path.split("#", 1)[0]).resolve()
        try:
            relative = resolved.relative_to(ROOT).as_posix()
        except ValueError:
            relative = path
        rows.append(["E%02d" % index, label, relative])
    table(document, rows, [1, 4, 10], 8)
    landscape(document)
    paragraph(document, "附录B：开源及第三方资源使用清单", "Heading 1")
    paragraph(document, "正文采用模板A4纵向、宋体12pt与指定页边距。本附表为8列可读性采用A4横向。共49项：已核验42、待核验4、不适用3；已核验限于列明范围，未确认事项不填写为全部合规。许可原文随仓库verification-20261030交付。")
    resource_table(document, entries)
    paragraph(document, "团队分工与工具辅助", "Heading 1")
    paragraph(document, "团队提供分工：张琪负责设计/编码，仇泽主要负责实验/调试，朱傲睿负责材料。Codex/OpenAI用于代码阅读、建议及部分实现、运行验证、文档整理辅助，属于开发工具，不是作品运行依赖。团队对最终审核、验收和提交负责；未估算贡献比例，未把AI辅助部分全部声称为纯人工。")
    document.save(ROOT / "docs/02_架构/技术报告.docx")
    standalone = Document()
    setup(standalone)
    landscape(standalone, False)
    paragraph(standalone, "开源及第三方资源使用清单", "Title")
    paragraph(standalone, "2026-10-30。按附件2八字段生成；已核验42、待核验4、不适用3。软件许可、模型来源和账号服务授权分别说明。")
    resource_table(standalone, entries)
    standalone.save(ROOT / "reports/compliance/开源及第三方资源使用清单.docx")
    print("Built report and checklist Word documents")


if __name__ == "__main__":
    build()
