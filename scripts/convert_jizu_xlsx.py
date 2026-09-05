#!/usr/bin/env python
"""2026年機組數據 xlsx -> u1.csv / u2.csv（長表：磨煤機為列，保證/非保證為欄）"""
import csv
import sys
import openpyxl
from openpyxl.worksheet.worksheet import Worksheet

SRC = "data/2026年機組數據-20260729.xlsx"
SHEETS = {"工作表-U1": "data/u1.csv", "工作表U2": "data/u2.csv"}
WIDE_OUT = {"工作表-U1": "data/u1_by_date.csv", "工作表U2": "data/u2_by_date.csv"}

# 圖例色 -> 國家（由第2列 B:F 圖例驗證）
COUNTRY_BY_FILL = {
    ("idx", 40): "印尼",
    ("idx", 52): "澳洲",
    ("idx", 45): "俄羅斯",
    ("rgb", "FF99FF66"): "南非",
    ("rgb", "FFFFFF66"): "哈薩克",
}
CANON_ORDER = ["南非", "印尼", "澳洲", "俄羅斯", "哈薩克"]  # 混煤 key 正規化排序
ORANGE = ("rgb", "FFFF9900")  # 僅出現於批號1097，前後文皆俄羅斯
CYAN = ("rgb", "FF00CCFF")    # 批號1089/1084+1089，無法判定國家

VALID = set(CANON_ORDER)


def fill_key(cell):
    f = cell.fill
    if f is None or f.patternType is None:
        return None
    c = f.fgColor
    if c.type == "rgb" and c.rgb and c.rgb != "00000000":
        return ("rgb", c.rgb)
    if c.type == "indexed":
        return ("idx", c.indexed)
    return None


def norm_country(raw, fill):
    """回傳單一中文 key"""
    v = str(raw).strip()
    if "+" in v:
        parts = [p.strip() for p in v.split("+")]
        if all(p in CANON_ORDER for p in parts):  # 國家混煤：正規化排序
            return "+".join(sorted(parts, key=CANON_ORDER.index))
        return f"未知_批號{v}"  # 批號混合（如 1084+1089）
    if v in VALID:
        return v
    # 數字批號誤填：依底色判斷
    if fill == ORANGE and v == "1097":
        return "俄羅斯"
    return f"未知_批號{v}"


MILL_COLS = [
    # (欄位名, 保證起始col)  佔4欄 A-D；僅取保證時段
    ("煤源", 2),
    ("用煤量", 10),
    ("熱值_kcalkg", 41),     # AO
    ("揮發份_pct", 49),      # AW
    ("總水份_pct", 57),      # BE
    ("灰份_pct", 65),        # BM
]
DAILY = {
    "用煤量合計_保證": 22,
    "發電量_保證_MWh": 25,
    "平均熱值_保證_kcalkg": 28,
    "保證時段發電量_avg_MW": 31, "保證時段發電量_max_MW": 32, "保證時段發電量_min_MW": 33,
    "燃燒器角度_度": 34, "主蒸汽溫度_C": 35, "再熱蒸汽溫度_C": 36, "溫度Tag_C": 37,
    "原煤耗_g": 38, "未燃碳_飛灰保證_pct": 39, "熱耗率": 40,
    "SCR入口NOx": 73, "SCR入口CO": 74, "冷凝器真空度": 75, "冷凝器出口水溫_C": 76,
    "燃燒風門開度_A_pct": 77, "燃燒風門開度_B_pct": 78,
    "燃燒風門開度_C_pct": 79, "燃燒風門開度_D_pct": 80,
    "火上風門開度_FCP": 81, "火上風門開度_FCS": 82,
    "火上風門開度_FCT": 83, "火上風門開度_FCF": 84,
}


def load(path, **kw):
    try:
        return openpyxl.load_workbook(path, **kw)
    except Exception as e:
        sys.exit(f"無法讀取 {path}: {e}")


def main():
    wb = load(SRC, data_only=True)
    wb_styles = load(SRC)  # 取底色用（data_only 亦可取，但分開載入保險）
    for sheet, out in SHEETS.items():
        ws, wss = wb[sheet], wb_styles[sheet]
        assert isinstance(ws, Worksheet) and isinstance(wss, Worksheet)
        # 展開合併儲格（如 B103:E103 整組同煤源）
        merged = {}
        for rng in ws.merged_cells.ranges:
            tl = ws.cell(row=rng.min_row, column=rng.min_col)
            tls = wss.cell(row=rng.min_row, column=rng.min_col)
            for r in range(rng.min_row, rng.max_row + 1):
                for c in range(rng.min_col, rng.max_col + 1):
                    merged[(r, c)] = (tl.value, fill_key(tls))

        def val(r, c):
            if (r, c) in merged:
                return merged[(r, c)]
            cell, cells = ws.cell(row=r, column=c), wss.cell(row=r, column=c)
            return cell.value, fill_key(cells)

        header = ["日期", "磨煤機"]
        for name, _ in MILL_COLS:
            header.append(f"{name}_保證")
        header += list(DAILY)

        rows, n_unknown = [], 0
        for r in range(5, ws.max_row + 1):
            d = ws.cell(row=r, column=1).value
            if d is None:
                continue
            date = d.strftime("%Y-%m-%d")
            # 保證煤源全空白的日（未運轉）整日略過
            if all(val(r, 2 + mi)[0] in (None, "") for mi in range(4)):
                continue
            daily_vals = []
            for h in header[8:]:  # 前8欄：日期+磨煤機+6組保證欄
                v, _ = val(r, DAILY[h])
                daily_vals.append(v)
            for mi, mill in enumerate("ABCD"):
                src, fill = val(r, 2 + mi)
                if src in (None, ""):  # 該磨保證無煤源紀錄，略過
                    continue
                row = [date, mill]
                for name, col0 in MILL_COLS:
                    v, f2 = (src, fill) if name == "煤源" else val(r, col0 + mi)
                    if name == "煤源":
                        k = norm_country(v, fill)
                        n_unknown += k.startswith("未知")
                        v = k
                    row.append(v)
                row += daily_vals
                rows.append(row)

        try:
            with open(out, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(header)
                w.writerows(rows)
        except OSError as e:
            sys.exit(f"無法寫入 {out}: {e}")

        # 依日期併列版：磨煤機欄位展開為 _A~_D，日層欄位不重複
        mill_cols = header[2:8]                       # 6個磨煤機層欄位
        day_cols = ["日期"] + header[8:]
        wide_header = day_cols[:1] + [
            f"{c}_{m}" for c in mill_cols for m in "ABCD"] + day_cols[1:]
        by_date = {}
        for row in rows:
            d = by_date.setdefault(row[0], {})
            mi = "ABCD".index(row[1])
            for j, c in enumerate(mill_cols):
                d[f"{c}_{'ABCD'[mi]}"] = row[2 + j]
            for j, h in enumerate(day_cols):
                d.setdefault(h, row[0] if j == 0 else row[7 + j])
        wide_rows = [[by_date[d].get(h, "") for h in wide_header]
                     for d in sorted(by_date)]
        try:
            with open(WIDE_OUT[sheet], "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(wide_header)
                w.writerows(wide_rows)
        except OSError as e:
            sys.exit(f"無法寫入 {WIDE_OUT[sheet]}: {e}")
        print(f"{sheet} -> {out}: {len(rows)} 列, {WIDE_OUT[sheet]}: "
              f"{len(wide_rows)} 列 x {len(wide_header)} 欄, 未知煤源 {n_unknown} 格")

    # 自我檢查：u1 第一個有資料的完整日，四磨保證用量和應等於日層合計
    check_done = False
    try:
        with open("data/u1.csv", encoding="utf-8-sig") as f:
            rd = list(csv.DictReader(f))
    except OSError as e:
        sys.exit(f"無法讀回 u1.csv: {e}")
    for date in sorted({x["日期"] for x in rd}):  # 空白煤源日已剔除，任取一日驗證
        day = [x for x in rd if x["日期"] == date]
        try:
            s = sum(float(x["用煤量_保證"] or 0) for x in day)
            t = float(day[0]["用煤量合計_保證"] or 0)
        except (ValueError, KeyError) as e:
            sys.exit(f"self-check 資料格式錯誤 {date}: {e}")
        assert abs(s - t) < 1e-6 * max(1, t), f"{date}: mills={s} != total={t}"
        check_done = True
        break
    print("self-check:", "OK" if check_done else "SKIPPED")


if __name__ == "__main__":
    sys.exit(main())
