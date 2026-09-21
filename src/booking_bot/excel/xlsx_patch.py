"""Ghi file Excel KHONG MAT DU LIEU (lossless) bang cach sua truc tiep XML.

TAI SAO KHONG DUNG openpyxl DE GHI?
-----------------------------------
Da do tren file Follow Up that (1 sheet, 47 cot, 33 external link, 1495 defined name):
sau khi openpyxl mo va luu lai

    - cot NO (cong thuc =IF(LEFT(M2,4)="TEGV",RIGHT(M2,5)-2108,"")) doc lai = None
      vi openpyxl xoa toan bo gia tri tinh san cua cong thuc
    - mat 2 defined name (1495 -> 1493)
    - mat printerSettings, calcChain; comment bi viet lai
    - 33 external link bi ghi lai (kich thuoc thay doi)

Bot se tu lam hong file cua chinh User -> khong chap nhan duoc.

CACH LAM O DAY
--------------
Mo file .xlsx nhu mot file zip. Chi sua dung 3 thu:
    xl/styles.xml            them fill / xf moi (khong xoa, khong doi cai cu)
    xl/worksheets/sheetN.xml doi thuoc tinh s="" cua o can to mau, hoac gia tri o can ghi
    xl/workbook.xml          chi khi co them cong thuc moi (bat fullCalcOnLoad)
Moi part khac duoc chep lai NGUYEN VEN tung byte.

Quy tac an toan:
    - KHONG BAO GIO ghi de o co cong thuc
    - Giu nguyen gia tri tinh san <v> cua moi cong thuc
    - Style moi duoc tai su dung (dedup) -> chay 1000 lan styles.xml khong phinh to
"""

from __future__ import annotations

import copy
import datetime as dt
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from lxml import etree

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PR = "http://schemas.openxmlformats.org/package/2006/relationships"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

_PARSER = etree.XMLParser(remove_blank_text=False, resolve_entities=False, huge_tree=True,
                          no_network=True)

_REF = re.compile(r"^([A-Z]{1,3})(\d+)$")
EXCEL_EPOCH = dt.datetime(1899, 12, 30)

# numFmtId co san cua Excel la dinh dang ngay/gio
BUILTIN_DATE_FMTS = set(range(14, 23)) | set(range(27, 37)) | set(range(45, 48)) | set(range(50, 59))


def q(tag: str) -> str:
    return f"{{{NS}}}{tag}"


class XlsxPatchError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Tien ich toa do
# ---------------------------------------------------------------------------


def col_to_idx(col: str) -> int:
    n = 0
    for ch in col:
        n = n * 26 + (ord(ch) - 64)
    return n


def idx_to_col(idx: int) -> str:
    out = ""
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        out = chr(65 + rem) + out
    return out


def split_ref(ref: str) -> tuple[int, int]:
    """'AB12' -> (12, 28)."""
    m = _REF.match(ref.replace("$", ""))
    if not m:
        raise XlsxPatchError(f"Toa do khong hop le: {ref}")
    return int(m.group(2)), col_to_idx(m.group(1))


def to_serial(value: dt.date | dt.datetime) -> float | int:
    if isinstance(value, dt.datetime):
        delta = value - EXCEL_EPOCH
        return round(delta.days + delta.seconds / 86400, 8)
    return (dt.datetime(value.year, value.month, value.day) - EXCEL_EPOCH).days


# ---------------------------------------------------------------------------
# Dich cong thuc khi sao chep sang dong khac (giong keo cong thuc trong Excel)
# ---------------------------------------------------------------------------

_TOKEN = re.compile(r"(\"[^\"]*\")|('[^']*'!)|(\$?)([A-Z]{1,3})(\$?)(\d+)(?![\d(A-Za-z_])")


def shift_formula(text: str, row_delta: int) -> str:
    """Dich tham chieu dong tuong doi: ROUND(W5*0.95,2) +1 -> ROUND(W6*0.95,2).

    - Bo qua chuoi trong ngoac kep ("TEGV" khong bi dung toi)
    - Giu nguyen tham chieu tuyet doi $5
    - Khong dung toi ten ham (LEFT( co dau mo ngoac ngay sau)
    """
    if row_delta == 0:
        return text

    def repl(m: re.Match) -> str:
        if m.group(1) or m.group(2):
            return m.group(0)
        dollar_c, col, dollar_r, row = m.group(3), m.group(4), m.group(5), m.group(6)
        if dollar_r:
            return m.group(0)
        prev = m.string[m.start() - 1] if m.start() > 0 else ""
        if prev.isalpha() or prev == "_":
            return m.group(0)  # mot phan cua ten (vd LOG10)
        return f"{dollar_c}{col}{dollar_r}{int(row) + row_delta}"

    return _TOKEN.sub(repl, text)


def normalize_formula(text: str, row: int) -> str:
    """Bien cong thuc thanh mau so sanh duoc giua cac dong: M2 -> M{r}, M1 -> M{r-1}."""

    def repl(m: re.Match) -> str:
        if m.group(1) or m.group(2):
            return m.group(0)
        dollar_c, col, dollar_r, r = m.group(3), m.group(4), m.group(5), m.group(6)
        if dollar_r:
            return m.group(0)
        prev = m.string[m.start() - 1] if m.start() > 0 else ""
        if prev.isalpha() or prev == "_":
            return m.group(0)
        d = int(r) - row
        return f"{dollar_c}{col}{{r{d:+d}}}" if d else f"{dollar_c}{col}{{r}}"

    return _TOKEN.sub(repl, text.lstrip("="))


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------


class StyleBook:
    """Quan ly xl/styles.xml: them fill/xf moi va TAI SU DUNG cai da co."""

    def __init__(self, tree: etree._ElementTree):
        self.tree = tree
        root = tree.getroot()
        self.fills = root.find(q("fills"))
        self.xfs = root.find(q("cellXfs"))
        self.numfmts = root.find(q("numFmts"))
        if self.fills is None or self.xfs is None:
            raise XlsxPatchError("styles.xml thieu <fills> hoac <cellXfs>")
        self.changed = False
        self._xf_sig: dict[str, int] = {self._sig(x): i for i, x in enumerate(self.xfs)}

    @staticmethod
    def _sig(el: etree._Element) -> str:
        attrs = "|".join(f"{k}={v}" for k, v in sorted(el.attrib.items()))
        kids = b"".join(etree.tostring(c) for c in el).decode()
        return f"{attrs}#{kids}"

    def _bump(self, parent: etree._Element) -> None:
        parent.set("count", str(len(parent)))
        self.changed = True

    # -- fill ------------------------------------------------------------

    def fill_id(self, rgb: str) -> int:
        rgb = rgb.upper()
        for i, f in enumerate(self.fills):
            pf = f.find(q("patternFill"))
            if pf is None or pf.get("patternType") != "solid":
                continue
            fg = pf.find(q("fgColor"))
            if fg is not None and (fg.get("rgb") or "").upper() == rgb and len(fg.attrib) == 1:
                return i
        fill = etree.SubElement(self.fills, q("fill"))
        pf = etree.SubElement(fill, q("patternFill"), patternType="solid")
        etree.SubElement(pf, q("fgColor"), rgb=rgb)
        etree.SubElement(pf, q("bgColor"), indexed="64")
        self._bump(self.fills)
        return len(self.fills) - 1

    def fill_rgb_of_xf(self, xf_idx: int) -> str:
        """Mau nen RGB hien tai cua mot style ('' neu la theme/khong co)."""
        if xf_idx >= len(self.xfs):
            return ""
        fid = int(self.xfs[xf_idx].get("fillId", "0"))
        if fid >= len(self.fills):
            return ""
        pf = self.fills[fid].find(q("patternFill"))
        if pf is None or pf.get("patternType") != "solid":
            return ""
        fg = pf.find(q("fgColor"))
        return (fg.get("rgb") or "").upper() if fg is not None else ""

    # -- xf --------------------------------------------------------------

    def _add_or_reuse(self, xf: etree._Element) -> int:
        sig = self._sig(xf)
        if sig in self._xf_sig:
            return self._xf_sig[sig]
        self.xfs.append(xf)
        self._bump(self.xfs)
        idx = len(self.xfs) - 1
        self._xf_sig[sig] = idx
        return idx

    def with_fill(self, xf_idx: int, rgb: str) -> int:
        base = self.xfs[xf_idx] if xf_idx < len(self.xfs) else self.xfs[0]
        fid = str(self.fill_id(rgb))
        if base.get("fillId") == fid:
            return xf_idx
        xf = copy.deepcopy(base)
        xf.set("fillId", fid)
        xf.set("applyFill", "1")
        return self._add_or_reuse(xf)

    def numfmt_code(self, xf_idx: int) -> tuple[int, str]:
        if xf_idx >= len(self.xfs):
            return 0, ""
        fid = int(self.xfs[xf_idx].get("numFmtId", "0"))
        code = ""
        if self.numfmts is not None:
            for nf in self.numfmts:
                if nf.get("numFmtId") == str(fid):
                    code = nf.get("formatCode", "")
        return fid, code

    def is_date_style(self, xf_idx: int) -> bool:
        fid, code = self.numfmt_code(xf_idx)
        if fid in BUILTIN_DATE_FMTS:
            return True
        if not code:
            return False
        stripped = re.sub(r'"[^"]*"|\[[^\]]*\]', "", code).lower()
        return bool(re.search(r"[dmy]", stripped)) and "0.0" not in stripped

    def with_date_format(self, xf_idx: int, code: str = "dd/mm/yyyy") -> int:
        if self.is_date_style(xf_idx):
            return xf_idx
        if self.numfmts is None:
            self.numfmts = etree.Element(q("numFmts"))
            self.tree.getroot().insert(0, self.numfmts)
        fmt_id = None
        for nf in self.numfmts:
            if nf.get("formatCode") == code:
                fmt_id = nf.get("numFmtId")
        if fmt_id is None:
            used = [int(nf.get("numFmtId", "0")) for nf in self.numfmts]
            fmt_id = str(max([163, *used]) + 1)
            etree.SubElement(self.numfmts, q("numFmt"), numFmtId=fmt_id, formatCode=code)
            self._bump(self.numfmts)
        base = self.xfs[xf_idx] if xf_idx < len(self.xfs) else self.xfs[0]
        xf = copy.deepcopy(base)
        xf.set("numFmtId", fmt_id)
        xf.set("applyNumberFormat", "1")
        return self._add_or_reuse(xf)


# ---------------------------------------------------------------------------
# Sheet
# ---------------------------------------------------------------------------


class SheetPatch:
    def __init__(self, name: str, part: str, tree: etree._ElementTree, pkg: XlsxPackage):
        self.name = name
        self.part = part
        self.tree = tree
        self.pkg = pkg
        self.root = tree.getroot()
        self.data = self.root.find(q("sheetData"))
        if self.data is None:
            raise XlsxPatchError(f"Sheet {name} khong co sheetData")
        self.changed = False
        self._rows: dict[int, etree._Element] = {int(r.get("r")): r for r in self.data.findall(q("row"))}
        self._col_styles = self._read_col_styles()

    def _read_col_styles(self) -> dict[int, int]:
        out: dict[int, int] = {}
        cols = self.root.find(q("cols"))
        if cols is None:
            return out
        for c in cols.findall(q("col")):
            if c.get("style") is None:
                continue
            for i in range(int(c.get("min")), int(c.get("max")) + 1):
                out[i] = int(c.get("style"))
        return out

    # -- dong / o -----------------------------------------------------------

    def row_el(self, r: int, create: bool = True, template: int | None = None) -> etree._Element | None:
        if r in self._rows:
            return self._rows[r]
        if not create:
            return None
        el = etree.Element(q("row"), r=str(r))
        if template and template in self._rows:
            for attr in ("s", "customFormat", "ht", "customHeight"):
                v = self._rows[template].get(attr)
                if v is not None:
                    el.set(attr, v)
        after = [k for k in self._rows if k < r]
        if after:
            self._rows[max(after)].addnext(el)
        else:
            self.data.insert(0, el)
        self._rows[r] = el
        self.changed = True
        return el

    def cell_el(self, r: int, c: int, create: bool = True, style: int | None = None) -> etree._Element | None:
        row = self.row_el(r, create=create)
        if row is None:
            return None
        ref = f"{idx_to_col(c)}{r}"
        insert_before = None
        for cell in row.findall(q("c")):
            cref = cell.get("r")
            if cref == ref:
                return cell
            if cref and split_ref(cref)[1] > c and insert_before is None:
                insert_before = cell
        if not create:
            return None
        new = etree.Element(q("c"), r=ref)
        if style is None:
            if row.get("customFormat") == "1" and row.get("s"):
                style = int(row.get("s"))
            else:
                style = self._col_styles.get(c, 0)
        if style:
            new.set("s", str(style))
        if insert_before is not None:
            insert_before.addprevious(new)
        else:
            row.append(new)
        self.changed = True
        return new

    @staticmethod
    def style_of(cell: etree._Element) -> int:
        return int(cell.get("s", "0"))

    @staticmethod
    def is_formula(cell: etree._Element | None) -> bool:
        return cell is not None and cell.find(q("f")) is not None

    def formula_text(self, cell: etree._Element) -> str | None:
        """Tra ve cong thuc (ke ca shared formula) tai o nay."""
        f = cell.find(q("f"))
        if f is None:
            return None
        if f.text:
            return f.text
        if f.get("t") == "shared" and f.get("si") is not None:
            si = f.get("si")
            r, _ = split_ref(cell.get("r"))
            for row in self._rows.values():
                for c in row.findall(q("c")):
                    mf = c.find(q("f"))
                    if mf is not None and mf.get("t") == "shared" and mf.get("si") == si and mf.text:
                        mr, _ = split_ref(c.get("r"))
                        return shift_formula(mf.text, r - mr)
        return None

    def cell_rgb(self, r: int, c: int) -> str:
        cell = self.cell_el(r, c, create=False)
        if cell is None:
            return ""
        return self.pkg.styles.fill_rgb_of_xf(self.style_of(cell))

    # -- to mau -------------------------------------------------------------

    def fill_row(self, r: int, rgb: str, cols: list[int]) -> int:
        """To mau cac o trong dong r. Tra ve so o thay doi. Khong dung toi gia tri."""
        styles = self.pkg.styles
        changed = 0
        for c in cols:
            cell = self.cell_el(r, c)
            old = self.style_of(cell)
            new = styles.with_fill(old, rgb)
            if new != old:
                cell.set("s", str(new))
                changed += 1
        if changed:
            self.changed = True
        return changed

    # -- ghi gia tri ----------------------------------------------------------

    def set_value(self, r: int, c: int, value: Any, *, style: int | None = None) -> bool:
        """Ghi 1 gia tri. KHONG ghi de cong thuc -> tra ve False."""
        cell = self.cell_el(r, c, style=style)
        if self.is_formula(cell):
            return False

        for tag in ("v", "is"):
            for old in cell.findall(q(tag)):
                cell.remove(old)
        if "t" in cell.attrib:
            del cell.attrib["t"]

        if value is None or value == "":
            self.changed = True
            return True

        styles = self.pkg.styles
        if isinstance(value, bool):
            cell.set("t", "b")
            etree.SubElement(cell, q("v")).text = "1" if value else "0"
        elif isinstance(value, (dt.date, dt.datetime)):
            etree.SubElement(cell, q("v")).text = str(to_serial(value))
            cur = self.style_of(cell)
            if not styles.is_date_style(cur):
                cell.set("s", str(styles.with_date_format(cur)))
        elif isinstance(value, (int, float)):
            v = int(value) if isinstance(value, float) and value.is_integer() else value
            etree.SubElement(cell, q("v")).text = repr(v) if isinstance(v, float) else str(v)
        else:
            text = str(value)
            cell.set("t", "inlineStr")
            is_ = etree.SubElement(cell, q("is"))
            t = etree.SubElement(is_, q("t"))
            t.text = text
            if text != text.strip():
                t.set(XML_SPACE, "preserve")
        self.changed = True
        return True

    def set_cached_value(self, r: int, c: int, value: Any) -> bool:
        """Gan GIA TRI TINH SAN cho o cong thuc (giong Excel luu file).

        CHI dung de tao du lieu mau / test. Bot KHONG dung ham nay tren file that.
        """
        cell = self.cell_el(r, c, create=False)
        if not self.is_formula(cell):
            return False
        for old in cell.findall(q("v")):
            cell.remove(old)
        if isinstance(value, str):
            cell.set("t", "str")
            etree.SubElement(cell, q("v")).text = value
        elif value is not None:
            if "t" in cell.attrib:
                del cell.attrib["t"]
            v = int(value) if isinstance(value, float) and value.is_integer() else value
            etree.SubElement(cell, q("v")).text = repr(v) if isinstance(v, float) else str(v)
        self.changed = True
        return True

    def copy_formulas(self, from_row: int, to_row: int, cols: list[int]) -> int:
        """Keo cong thuc tu dong mau xuong dong moi (giong fill-down trong Excel)."""
        n = 0
        for c in cols:
            src = self.cell_el(from_row, c, create=False)
            if not self.is_formula(src):
                continue
            text = self.formula_text(src)
            if not text:
                continue
            dst = self.cell_el(to_row, c, style=self.style_of(src))
            if self.is_formula(dst) or dst.find(q("v")) is not None or dst.find(q("is")) is not None:
                continue
            f = etree.SubElement(dst, q("f"))
            f.text = shift_formula(text, to_row - from_row)
            n += 1
        if n:
            self.changed = True
            self.pkg.need_full_calc = True
        return n

    def copy_row_styles(self, from_row: int, to_row: int, cols: list[int]) -> None:
        for c in cols:
            src = self.cell_el(from_row, c, create=False)
            if src is None:
                continue
            dst = self.cell_el(to_row, c, style=self.style_of(src))
            if dst.get("s") is None and src.get("s") is not None:
                dst.set("s", src.get("s"))

    # -- merge / dimension ------------------------------------------------

    def merged_ranges(self) -> list[str]:
        mc = self.root.find(q("mergeCells"))
        return [m.get("ref") for m in mc.findall(q("mergeCell"))] if mc is not None else []

    def update_dimension(self) -> None:
        dim = self.root.find(q("dimension"))
        if dim is None or not self._rows:
            return
        max_r = max(self._rows)
        max_c = 1
        for row in self._rows.values():
            for c in row.findall(q("c")):
                max_c = max(max_c, split_ref(c.get("r"))[1])
        ref = dim.get("ref", "A1")
        start = ref.split(":")[0]
        new_ref = f"{start}:{idx_to_col(max_c)}{max_r}"
        if new_ref != ref:
            dim.set("ref", new_ref)


# ---------------------------------------------------------------------------
# Package
# ---------------------------------------------------------------------------


class XlsxPackage:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        with zipfile.ZipFile(self.path) as zf:
            self._infos = zf.infolist()
            self._raw = {i.filename: zf.read(i.filename) for i in self._infos}
        self._trees: dict[str, etree._ElementTree] = {}
        self._sheets: dict[str, SheetPatch] = {}
        self._styles: StyleBook | None = None
        self.need_full_calc = False
        self.sheet_parts = self._resolve_sheet_parts()

    # -- XML ----------------------------------------------------------------

    def _tree(self, part: str) -> etree._ElementTree:
        if part not in self._trees:
            if part not in self._raw:
                raise XlsxPatchError(f"Khong co part {part}")
            self._trees[part] = etree.ElementTree(etree.fromstring(self._raw[part], _PARSER))
        return self._trees[part]

    def _resolve_sheet_parts(self) -> dict[str, str]:
        wb = self._tree("xl/workbook.xml").getroot()
        rels = etree.fromstring(self._raw["xl/_rels/workbook.xml.rels"], _PARSER)
        targets = {r.get("Id"): r.get("Target") for r in rels.findall(f"{{{NS_PR}}}Relationship")}
        out: dict[str, str] = {}
        sheets = wb.find(q("sheets"))
        for sh in sheets.findall(q("sheet")) if sheets is not None else []:
            target = targets.get(sh.get(f"{{{NS_R}}}id"), "")
            if target.startswith("/"):
                part = target.lstrip("/")
            else:
                part = str(PurePosixPath("xl") / target)
            out[sh.get("name")] = part
        return out

    # -- API ----------------------------------------------------------------

    @property
    def styles(self) -> StyleBook:
        if self._styles is None:
            self._styles = StyleBook(self._tree("xl/styles.xml"))
        return self._styles

    def sheet_names(self) -> list[str]:
        return list(self.sheet_parts)

    def sheet(self, name: str) -> SheetPatch:
        if name not in self._sheets:
            part = self.sheet_parts.get(name)
            if part is None:
                raise XlsxPatchError(f"Khong co sheet '{name}'. Co: {', '.join(self.sheet_parts)}")
            self._sheets[name] = SheetPatch(name, part, self._tree(part), self)
        return self._sheets[name]

    @property
    def changed(self) -> bool:
        return any(s.changed for s in self._sheets.values()) or (
            self._styles is not None and self._styles.changed
        )

    def changed_parts(self) -> list[str]:
        parts = [s.part for s in self._sheets.values() if s.changed]
        if self._styles is not None and self._styles.changed:
            parts.append("xl/styles.xml")
        if self.need_full_calc:
            parts.append("xl/workbook.xml")
        return parts

    def _serialize(self, part: str) -> bytes:
        tree = self._trees[part]
        return etree.tostring(tree, xml_declaration=True, encoding="UTF-8", standalone=True)

    def save(self, dest: str | Path) -> list[str]:
        """Ghi ra dest. Tra ve danh sach part da thay doi."""
        for sh in self._sheets.values():
            if sh.changed:
                sh.update_dimension()
        if self.need_full_calc:
            wb = self._tree("xl/workbook.xml").getroot()
            calc = wb.find(q("calcPr"))
            if calc is None:
                calc = etree.SubElement(wb, q("calcPr"))
            calc.set("fullCalcOnLoad", "1")

        changed = set(self.changed_parts())
        with zipfile.ZipFile(dest, "w") as out:
            for info in self._infos:
                data = self._serialize(info.filename) if info.filename in changed else self._raw[info.filename]
                zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                zi.compress_type = info.compress_type or zipfile.ZIP_DEFLATED
                zi.external_attr = info.external_attr
                zi.create_system = info.create_system
                out.writestr(zi, data)
        return sorted(changed)


# ---------------------------------------------------------------------------
# Kiem tra goi truoc khi ghi
# ---------------------------------------------------------------------------


@dataclass
class PackageReport:
    parts: int = 0
    external_links: int = 0
    defined_names: int = 0
    comments: int = 0
    printer_settings: int = 0
    drawings: int = 0
    charts: int = 0
    images: int = 0
    pivot_tables: int = 0
    tables: int = 0
    vba: bool = False
    formulas: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def openpyxl_would_lose(self) -> list[str]:
        """Nhung thu openpyxl se lam mat neu dung no de luu file nay."""
        out = []
        if self.formulas:
            out.append(f"gia tri tinh san cua {self.formulas} o cong thuc")
        if self.printer_settings:
            out.append("thiet lap in")
        if self.charts or self.images:
            out.append(f"{self.charts} bieu do / {self.images} hinh anh")
        if self.pivot_tables:
            out.append(f"{self.pivot_tables} pivot table")
        if self.vba:
            out.append("macro VBA")
        if self.external_links:
            out.append(f"{self.external_links} external link (bi ghi lai)")
        return out

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()} | {"openpyxl_would_lose": self.openpyxl_would_lose}


def inspect_package(path: str | Path) -> PackageReport:
    rep = PackageReport()
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        rep.parts = len(names)
        rep.external_links = sum(1 for n in names if re.match(r"xl/externalLinks/externalLink\d+\.xml$", n))
        rep.comments = sum(1 for n in names if re.search(r"comments\d*\.xml$", n))
        rep.printer_settings = sum(1 for n in names if "printerSettings" in n)
        rep.drawings = sum(1 for n in names if re.match(r"xl/drawings/drawing\d+\.xml$", n))
        rep.charts = sum(1 for n in names if n.startswith("xl/charts/chart"))
        rep.images = sum(1 for n in names if n.startswith("xl/media/"))
        rep.pivot_tables = sum(1 for n in names if n.startswith("xl/pivotTables/"))
        rep.tables = sum(1 for n in names if re.match(r"xl/tables/table\d+\.xml$", n))
        rep.vba = "xl/vbaProject.bin" in names
        wb = zf.read("xl/workbook.xml")
        rep.defined_names = wb.count(b"<definedName ")
        for n in names:
            if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"):
                rep.formulas += len(re.findall(rb"<f[ >/]", zf.read(n)))
    return rep
