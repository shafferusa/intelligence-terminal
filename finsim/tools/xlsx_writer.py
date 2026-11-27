"""A minimal .xlsx writer on the standard library: sheets of rows, header bold, column widths, autofilter, frozen header."""
import zipfile, datetime
from xml.sax.saxutils import escape

def _col(n):
    s = ""
    while n:
        n, r = divmod(n - 1, 26); s = chr(65 + r) + s
    return s

def _cell(ref, v, style=0):
    if v is None or v == "":
        return ""
    if isinstance(v, bool):
        return f'<c r="{ref}" t="b" s="{style}"><v>{int(v)}</v></c>'
    if isinstance(v, (int, float)):
        return f'<c r="{ref}" s="{style}"><v>{v}</v></c>'
    t = escape(str(v)).replace("\n", "&#10;")
    return f'<c r="{ref}" t="inlineStr" s="{style}"><is><t xml:space="preserve">{t}</t></is></c>'

def write(path, sheets):
    """sheets: list of (name, header, rows, widths?)"""
    z = zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED)
    names = [s[0][:31] for s in sheets]
    z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>' + "".join(f'<Override PartName="/xl/worksheets/sheet{i+1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(len(sheets))) + '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/><Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/></Types>')
    z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/></Relationships>')
    now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    z.writestr("docProps/core.xml", f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>finsim tradeable universe</dc:title><dc:creator>finsim</dc:creator><dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified></cp:coreProperties>')
    z.writestr("docProps/app.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><Application>finsim</Application></Properties>')
    z.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><bookViews><workbookView/></bookViews><sheets>' + "".join(f'<sheet name="{escape(n)}" sheetId="{i+1}" r:id="rId{i+1}"/>' for i, n in enumerate(names)) + '</sheets></workbook>')
    z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + "".join(f'<Relationship Id="rId{i+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i+1}.xml"/>' for i in range(len(sheets))) + f'<Relationship Id="rId{len(sheets)+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
    z.writestr("xl/styles.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts count="2"><numFmt numFmtId="164" formatCode="#,##0.00"/><numFmt numFmtId="165" formatCode="0.00%"/></numFmts><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF1F3864"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" applyFont="1" applyFill="1"/><xf numFmtId="164" fontId="0" fillId="0" borderId="0" applyNumberFormat="1"/><xf numFmtId="165" fontId="0" fillId="0" borderId="0" applyNumberFormat="1"/></cellXfs></styleSheet>')
    for i, sh in enumerate(sheets):
        name, header, rows = sh[0], sh[1], sh[2]
        widths = sh[3] if len(sh) > 3 else None
        pct = set(sh[4]) if len(sh) > 4 else set()
        ncol = len(header)
        if not widths:
            widths = [min(60, max(10, max([len(str(header[c]))] + [len(str(r[c])) for r in rows[:400] if c < len(r) and r[c] is not None]) + 2)) for c in range(ncol)]
        xml = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"' + (' tabSelected="1"' if i == 0 else '') + '><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols>' + "".join(f'<col min="{c+1}" max="{c+1}" width="{w}" customWidth="1"/>' for c, w in enumerate(widths)) + '</cols><sheetData>']
        xml.append('<row r="1">' + "".join(_cell(f"{_col(c+1)}1", h, 1) for c, h in enumerate(header)) + '</row>')
        for r, row in enumerate(rows, start=2):
            cells = []
            for c, v in enumerate(row):
                st = 3 if c in pct else (2 if isinstance(v, float) else 0)
                cells.append(_cell(f"{_col(c+1)}{r}", v, st))
            xml.append(f'<row r="{r}">' + "".join(cells) + '</row>')
        xml.append(f'</sheetData><autoFilter ref="A1:{_col(ncol)}{max(1, len(rows) + 1)}"/></worksheet>')
        z.writestr(f"xl/worksheets/sheet{i+1}.xml", "".join(xml))
    z.close()
