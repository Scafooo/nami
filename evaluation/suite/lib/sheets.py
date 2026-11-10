"""Workbooks for the annotators: an Instructions sheet and an Items sheet, one row per item, the columns to
fill in yellow with a drop-down list of the allowed answers. A CSV twin (';', UTF-8 with BOM, for Excel
with Italian or German settings) is written next to it. read() accepts either back."""

from __future__ import annotations

import csv
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

FILL = PatternFill("solid", fgColor="FFF4B0")


def write(path: Path, instructions: list[str], columns: list[dict], rows: list[list]) -> None:
    """columns: {"name", "width", "answer": bool, "options": [..] or None}."""
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ins = wb.active
    ins.title = "Instructions"
    for line in instructions:
        ins.append([line])
    ins.column_dimensions["A"].width = 140
    for row in ins.iter_rows():
        row[0].alignment = Alignment(wrap_text=True, vertical="top")
    ws = wb.create_sheet("Items")
    ws.append([c["name"] for c in columns])
    for r in rows:
        ws.append(r)
    ws.freeze_panes = "B2"
    for j, c in enumerate(columns, 1):
        col = get_column_letter(j)
        ws.column_dimensions[col].width = c.get("width", 20)
        ws.cell(1, j).font = Font(bold=True)
        if c.get("answer"):
            for i in range(1, len(rows) + 2):
                ws.cell(i, j).fill = FILL
            if c.get("options"):
                dv = DataValidation(type="list", formula1='"' + ",".join(c["options"]) + '"', allow_blank=True,
                                    showErrorMessage=True, errorTitle="Not allowed", error="Choose from the list.")
                ws.add_data_validation(dv)
                dv.add(f"{col}2:{col}{len(rows) + 1}")
    for row in ws.iter_rows(min_row=1, max_row=len(rows) + 1):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    wb.save(path)


def read(path: Path) -> dict[str, dict]:
    """item id -> {column name: answer}, from a returned .xlsx or .csv (';' or ',')."""
    if path.suffix == ".xlsx":
        ws = load_workbook(path, data_only=True)["Items"]
        rows = [[("" if v is None else str(v)).strip() for v in r] for r in ws.iter_rows(values_only=True)]
    else:
        text = path.read_text(encoding="utf-8-sig")
        first = text.splitlines()[0]
        rows = list(csv.reader(text.splitlines(), delimiter=";" if first.count(";") > first.count(",") else ","))
        rows = [[v.strip() for v in r] for r in rows]
    head, body = rows[0], rows[1:]
    return {r[0]: dict(zip(head, r)) for r in body if r and r[0]}


PREFIX = {"A1": "R", "A2": "P", "A3": "F", "A4": "S"}     # the ids of each task's rows


def returned(task: str, root: Path) -> dict[str, dict[str, dict]]:
    """annotator (file name) -> answers, for every filled file of this task in the folder `root`, whatever its
    name: the task is recognised from the ids of its rows (R001.. A1, P0001.. A2, F001.. A3, S001.. A4)."""
    files = sorted(p for p in root.rglob("*") if p.suffix in (".xlsx", ".csv") and not p.name.startswith("~$")) \
        if root.exists() else []
    out = {}
    for f in files:
        try:
            rows = read(f)
        except Exception:
            continue
        ids = list(rows)
        if ids and all(i.startswith(PREFIX[task]) and i[1:].isdigit() for i in ids[:5]):
            out[f.stem] = rows
    return out
