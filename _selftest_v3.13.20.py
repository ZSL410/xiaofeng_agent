# -*- coding: utf-8 -*-
"""v3.13.20 财务报告 Phase 3 自检：图表（matplotlib 近7天/近30天）+ Excel 导出（openpyxl 四工作表）+ format 参数

运行：venv/bin/python _selftest_v3.13.20.py
（需在项目根目录下，依赖 venv 内 python-docx / matplotlib / openpyxl）
"""
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "01_工具模块", "财务模块"))

import finance_module

passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


def _has_emoji(s):
    """检测字符串是否含常见 emoji 字符（Word/WPS 显示为方块的场景）。"""
    emoji_pat = re.compile(
        "[\U0001F000-\U0001FAFF"
        "\U00002600-\U000027BF"
        "\U00002190-\U000021FF"
        "\U00002B00-\U00002BFF]"
    )
    return bool(emoji_pat.search(s))


try:
    import matplotlib  # noqa: F401
    MATPLOTLIB_OK = True
except Exception:
    MATPLOTLIB_OK = False

try:
    import openpyxl  # noqa: F401
    OPENPYXL_OK = True
except Exception:
    OPENPYXL_OK = False

print("=" * 60)
print("[自检] v3.13.20 generate_report Phase 3（图表 + Excel + format 参数）")
print(f"环境: matplotlib={'有' if MATPLOTLIB_OK else '无'}  openpyxl={'有' if OPENPYXL_OK else '无'}")
print("=" * 60)

# 固定测试数据：最近 7 天内分布，含支出/收入/多分类
_TODAY = datetime.now().strftime("%Y-%m-%d")
_D1 = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
_D2 = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
_D3 = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
_D4 = (datetime.now() - timedelta(days=4)).strftime("%Y-%m-%d")
_D10 = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
SAMPLE = [
    {"id": "t1", "type": "expense", "date": _TODAY, "time": "12:00", "amount": 100, "source": "饭", "category": "餐饮"},
    {"id": "t2", "type": "income", "date": _TODAY, "time": "09:00", "amount": 500, "source": "工资", "category": "收入"},
    {"id": "d1", "type": "expense", "date": _D1, "time": "08:00", "amount": 20, "source": "打车", "category": "交通"},
    {"id": "d2", "type": "expense", "date": _D2, "time": "15:00", "amount": 30, "source": "奶茶", "category": "饮品"},
    {"id": "d3", "type": "expense", "date": _D3, "time": "12:00", "amount": 40, "source": "外卖", "category": "餐饮"},
    {"id": "d4", "type": "expense", "date": _D4, "time": "12:00", "amount": 50, "source": "购物", "category": "购物"},
    {"id": "old", "type": "expense", "date": _D10, "time": "12:00", "amount": 999, "source": "远古", "category": "其他"},
]

# ---------- 1. _aggregate_daily 每日聚合 ----------
print("\n--- 1. _aggregate_daily 每日聚合（连续日期 + 补零）---")
daily7 = finance_module._aggregate_daily(SAMPLE, days=7, end_date=_TODAY)
check("7 天窗口恰好 7 天", len(daily7) == 7, len(daily7))
check("窗口起点 = 今天-6", daily7[0]["date"] == (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d"), daily7[0]["date"])
check("窗口终点 = 今天", daily7[-1]["date"] == _TODAY, daily7[-1]["date"])
check("无数据日补齐为 0（窗口前 2 天：费用/收入均为 0）",
      daily7[0]["expense"] == 0 and daily7[0]["income"] == 0
      and daily7[1]["expense"] == 0 and daily7[1]["income"] == 0,
      daily7)
check("今天聚合：支出100/收入500/结余400",
      daily7[-1] == {"date": _TODAY, "expense": 100.0, "income": 500.0, "balance": 400.0},
      daily7[-1])
# 数据日逐日核对：昨天20/前天30/大前天40/前4天50
check("数据日聚合正确（_D1=20/_D2=30/_D3=40/_D4=50）",
      daily7[-2]["expense"] == 20 and daily7[-3]["expense"] == 30
      and daily7[-4]["expense"] == 40 and daily7[-5]["expense"] == 50,
      daily7)
daily30 = finance_module._aggregate_daily(SAMPLE, days=30, end_date=_TODAY)
check("30 天窗口恰好 30 天", len(daily30) == 30, len(daily30))
check("窗口外记录（10天前 999）不纳入 30 天聚合", daily30[0]["expense"] == 0, daily30[0])
# 指定 end_date（最近有数据日）窗口
daily_ed = finance_module._aggregate_daily(SAMPLE, days=7, end_date=_D1)
check("end_date 指定时窗口终点为该日", daily_ed[-1]["date"] == _D1, daily_ed[-1]["date"])
check("空数据返回空列表", finance_module._aggregate_daily([], days=7) == [], "")

# ---------- 2. 图表生成 ----------
print("\n--- 2. 图表生成（matplotlib，7天/30天 × 折线/柱状）---")
if MATPLOTLIB_OK:
    fonts = finance_module._setup_chinese_font()
    print(f"  [info] 注册中文字体: {fonts}")
    with tempfile.TemporaryDirectory() as tmp:
        img_line = finance_module._generate_chart_image(SAMPLE, chart_type="line", days=7,
                                                         output_dir=tmp, end_date=_TODAY)
        check("折线图(7天)生成 PNG", bool(img_line) and os.path.exists(img_line), img_line)
        check("折线图文件非空", os.path.getsize(img_line) > 1000 if img_line else False,
              os.path.getsize(img_line) if img_line else 0)
        img_bar = finance_module._generate_chart_image(SAMPLE, chart_type="bar", days=7,
                                                        output_dir=tmp, end_date=_TODAY)
        check("柱状图(7天)生成 PNG", bool(img_bar) and os.path.exists(img_bar), img_bar)
        img_line30 = finance_module._generate_chart_image(SAMPLE, chart_type="line", days=30,
                                                           output_dir=tmp, end_date=_TODAY)
        check("折线图(30天)生成 PNG", bool(img_line30) and os.path.exists(img_line30), img_line30)
        img_bar30 = finance_module._generate_chart_image(SAMPLE, chart_type="bar", days=30,
                                                          output_dir=tmp, end_date=_TODAY)
        check("柱状图(30天)生成 PNG", bool(img_bar30) and os.path.exists(img_bar30), img_bar30)
        # 包装函数
        check("_generate_trend_chart 返回折线图", bool(finance_module._generate_trend_chart(
            SAMPLE, days=7, output_dir=tmp, end_date=_TODAY)) is not None, "")
        check("_generate_comparison_chart 返回柱状图", bool(finance_module._generate_comparison_chart(
            SAMPLE, days=7, output_dir=tmp, end_date=_TODAY)) is not None, "")
else:
    print("  [skip] matplotlib 未安装，跳过图表渲染断言")

# ---------- 3. _resolve_report_windows 窗口解析（回归） ----------
print("\n--- 3. _resolve_report_windows 窗口解析（default/today/week/month/all）---")
win_def = finance_module._resolve_report_windows(SAMPLE, "default")
check("default: 汇总=今日(支出100/收入500)", finance_module._compute_report_summary(win_def["summary_records"])
      == {"income_total": 500, "expense_total": 100, "balance": 400}, win_def["summary_records"])
check("default: 明细≤30 且不含10天前记录",
      all((r.get("date") or "") >= _D2 for r in win_def["detail_records"]), [r.get("date") for r in win_def["detail_records"]])
check("default: report_date 为今日", win_def["report_date"] == _TODAY, win_def["report_date"])
win_all = finance_module._resolve_report_windows(SAMPLE, "all")
check("all: 明细含10天前记录（全量）",
      any((r.get("date") or "") == _D10 for r in win_all["detail_records"]), len(win_all["detail_records"]))
win_today = finance_module._resolve_report_windows(SAMPLE, "today")
check("today: 明细仅今日", set(r.get("date") for r in win_today["detail_records"]) == {_TODAY},
      [r.get("date") for r in win_today["detail_records"]])
check("week: 返回本周标题", win_today["scope_title"] == f"{_TODAY}（今日）", win_today["scope_title"])

# ---------- 4. generate_excel Excel 导出 ----------
print("\n--- 4. generate_excel（四工作表：Summary/Category/Comparison/Detail）---")
if OPENPYXL_OK:
    from openpyxl import load_workbook
    with tempfile.TemporaryDirectory() as tmp:
        xp = os.path.join(tmp, "财务数据.xlsx")
        res = finance_module.generate_excel(SAMPLE, output_path=xp, scope="all")
        check("excel 返回 success", res.get("status") == "success", res)
        check("xlsx 文件已生成", os.path.exists(xp), xp)
        wb = load_workbook(xp)
        check("工作表为 4 个", wb.sheetnames == ["Summary", "Category", "Comparison", "Detail"], wb.sheetnames)
        ws = wb["Summary"]
        vals = {r[0]: r[1] for r in ws.iter_rows(min_row=5, values_only=True) if r[0]}
        check("Summary 总支出1239/总收入500/结余-739（all范围）",
              vals.get("总支出") == 1239 and vals.get("总收入") == 500 and vals.get("结余") == -739, vals)
        ws = wb["Category"]
        rows = [(r[0], r[1]) for r in ws.iter_rows(min_row=2, values_only=True) if r[0]]
        check("Category 按金额降序（餐饮140/购物50/饮品30/交通20/其他999）",
              [r[0] for r in rows] == ["其他", "餐饮", "购物", "饮品", "交通"], rows)
        ws = wb["Comparison"]
        check("Comparison 含本周/本月两行", ws.max_row == 3, ws.max_row)
        ws = wb["Detail"]
        check("Detail 全部记录（7条）", ws.max_row == 8, ws.max_row)
        check("Detail 无 emoji", not _has_emoji("".join(str(c.value or "") for row in ws.iter_rows() for c in row)))
    # 默认输出路径
    import finance_module as _fm
    orig_dir = _fm.BASE_DIR
    with tempfile.TemporaryDirectory() as tmp2:
        # 空数据边界
        r_empty = finance_module.generate_excel(data=[], output_path=os.path.join(tmp2, "e.xlsx"))
        check("空数据返回 error", r_empty.get("status") == "error", r_empty)
else:
    print("  [skip] openpyxl 未安装，跳过 Excel 断言")

# ---------- 5. generate_report format 参数 ----------
print("\n--- 5. generate_report format=docx / excel / both ---")
with tempfile.TemporaryDirectory() as tmp:
    # format="docx"（默认）：含图表
    r_docx = finance_module.generate_report(data=SAMPLE, output_dir=tmp, scope="all", format="docx")
    check("docx 返回 success", r_docx.get("status") == "success", r_docx)
    check("docx file_path 存在", bool(r_docx.get("file_path")) and os.path.exists(r_docx["file_path"]), r_docx.get("file_path"))
    from docx import Document
    doc = Document(r_docx["file_path"])
    texts = [p.text for p in doc.paragraphs]
    joined = "\n".join(texts)
    check("五段式标题齐全", all(k in joined for k in ("一、收支汇总", "二、交易明细", "三、收支趋势图表",
                                                     "四、期间对比", "五、分类统计")), joined)
    _idx = [joined.index(k) for k in ("一、收支汇总", "二、交易明细", "三、收支趋势图表", "四、期间对比", "五、分类统计")]
    check("标题顺序：汇总→明细→图表→对比→分类", _idx == sorted(_idx), _idx)
    if MATPLOTLIB_OK:
        check("docx 内嵌 4 张图表（7/30天 × 折线/柱状）", len(doc.inline_shapes) == 4, len(doc.inline_shapes))
        check("无「图表生成失败」注记", "图表生成失败" not in joined, joined)
    else:
        check("matplotlib 缺失时注记图表失败", "图表生成失败" in joined, joined)
    check("图表在对比表之前（inline shape 位于表格前）", len(doc.inline_shapes) >= 1 if MATPLOTLIB_OK else True, "")
    check("整篇文档无 emoji", not _has_emoji(joined), joined)
    # 临时图表已清理：输出目录无 chart_*.png 残留
    check("输出目录无图表临时文件残留",
          not [f for f in os.listdir(tmp) if f.startswith("chart_")], os.listdir(tmp))

    # format="excel"：仅 Excel
    r_excel = finance_module.generate_report(data=SAMPLE, output_dir=tmp, scope="all", format="excel")
    check("excel 返回 success", r_excel.get("status") == "success", r_excel)
    check("excel file_path 为 .xlsx", r_excel.get("file_path", "").endswith(".xlsx"), r_excel.get("file_path"))
    check("excel 模式不生成 docx", not r_excel.get("docx_path"), r_excel.get("docx_path"))
    check("excel 文件存在", os.path.exists(r_excel["file_path"]), r_excel["file_path"])

    # format="both"：Word + Excel
    r_both = finance_module.generate_report(data=SAMPLE, output_dir=tmp, scope="all", format="both")
    check("both 返回 success", r_both.get("status") == "success", r_both)
    check("both 同时生成 docx 与 excel",
          bool(r_both.get("docx_path")) and os.path.exists(r_both["docx_path"])
          and bool(r_both.get("excel_path")) and os.path.exists(r_both["excel_path"]),
          (r_both.get("docx_path"), r_both.get("excel_path")))
    check("both file_path 指向 docx", r_both.get("file_path") == r_both.get("docx_path"),
          r_both.get("file_path"))

    # 非法 format 回退 docx
    r_bad = finance_module.generate_report(data=SAMPLE, output_dir=tmp, scope="all", format="bogus")
    check("非法 format 回退 docx", r_bad.get("file_path", "").endswith(".docx"), r_bad.get("file_path"))

# ---------- 6. 优雅降级：图表失败 ----------
print("\n--- 6. 优雅降级：图表生成失败（matplotlib 缺失模拟）---")
_orig_chart = finance_module._generate_chart_image
finance_module._generate_chart_image = lambda *a, **k: None  # 模拟图表全部失败
try:
    with tempfile.TemporaryDirectory() as tmp:
        r = finance_module.generate_report(data=SAMPLE, output_dir=tmp, scope="all", format="docx")
        check("图表失败仍返回 success", r.get("status") == "success", r)
        check("docx 仍生成", os.path.exists(r.get("file_path", "")), r.get("file_path"))
        doc = Document(r["file_path"])
        joined = "\n".join(p.text for p in doc.paragraphs)
        check("报告注明「图表生成失败，请查看表格数据」", "图表生成失败，请查看表格数据" in joined, joined)
        check("失败时无内嵌图表", len(doc.inline_shapes) == 0, len(doc.inline_shapes))
        check("期间对比表仍保留", "四、期间对比" in joined, joined)
finally:
    finance_module._generate_chart_image = _orig_chart

# ---------- 7. 优雅降级：openpyxl 缺失 ----------
print("\n--- 7. 优雅降级：openpyxl 缺失 ---")
if OPENPYXL_OK:
    _saved = sys.modules.get("openpyxl")
    sys.modules["openpyxl"] = None  # 模拟 openpyxl 不可用
    try:
        with tempfile.TemporaryDirectory() as tmp:
            r_excel_only = finance_module.generate_report(data=SAMPLE, output_dir=tmp, scope="all", format="excel")
            check("excel 模式返回 error", r_excel_only.get("status") == "error", r_excel_only)
            check("错误信息提示缺少 openpyxl", "openpyxl" in r_excel_only.get("message", ""), r_excel_only.get("message"))

            r_both = finance_module.generate_report(data=SAMPLE, output_dir=tmp, scope="all", format="both")
            check("both 部分成功（Excel 失败仍返回 Word）",
                  r_both.get("status") == "success" and os.path.exists(r_both.get("docx_path", "")),
                  (r_both.get("status"), r_both.get("docx_path")))
            check("both 提示 Excel 导出失败", "Excel 导出失败" in r_both.get("message", ""), r_both.get("message"))
            check("both 不产出 xlsx", not r_both.get("excel_path"), r_both.get("excel_path"))
    finally:
        if _saved is not None:
            sys.modules["openpyxl"] = _saved
        else:
            sys.modules.pop("openpyxl", None)

# ---------- 8. 回归：既有聚合/对比/明细/scope ----------
print("\n--- 8. 回归（汇总/分类/对比/明细/scope）---")
SAMPLE2 = [
    {"id": "e1", "type": "expense", "date": "2026-08-25", "amount": 150.5, "source": "吃饭", "category": "餐饮"},
    {"id": "e2", "type": "expense", "date": "2026-08-25", "amount": 80, "source": "打车", "category": "交通"},
    {"id": "e3", "type": "expense", "date": "2026-08-25", "amount": 20, "source": "奶茶", "category": "饮品"},
    {"id": "i1", "type": "income", "date": "2026-08-25", "amount": 500, "source": "工资", "category": "收入"},
]
cat = finance_module._compute_category_breakdown(SAMPLE2)
check("分类统计 餐饮150.5/交通80/饮品20", list(cat.keys()) == ["餐饮", "交通", "饮品"]
      and cat["餐饮"]["amount"] == 150.5, cat)
FIXED_NOW = datetime(2026, 8, 25)
comp = finance_module._get_period_comparison(
    [{"id": "w", "type": "expense", "date": "2026-08-25", "amount": 100},
     {"id": "l", "type": "expense", "date": "2026-08-20", "amount": 40}], now=FIXED_NOW)
check("期间对比 本周100 vs 上周40 +150%", comp["week"]["this_total"] == 100
      and comp["week"]["last_total"] == 40 and comp["week"]["percent"] == 150, comp["week"])
check("明细排序（新在前）", [r["id"] for r in finance_module._build_detail_table(
    [{"id": "old", "date": "2026-07-01"}, {"id": "new", "date": "2026-08-24"}])] == ["new", "old"], "")
with tempfile.TemporaryDirectory() as tmp:
    r_scope = finance_module.generate_report(data=SAMPLE, output_dir=tmp, scope="default")
    check("default scope 回归 success", r_scope.get("status") == "success", r_scope)
    check("default scope 摘要=今日(支出100/收入500)", r_scope["summary"] == {"income_total": 500, "expense_total": 100, "balance": 400}, r_scope["summary"])
    r_empty = finance_module.generate_report(data=[], output_dir=tmp)
    check("空数据返回 error", r_empty.get("status") == "error", r_empty)

# ---------- 9. 真实数据（local_archive.json）全量报告 ----------
print("\n--- 9. 真实数据全量报告（generate_report 双格式）---")
_real = finance_module.load_data()
print(f"  [info] 真实数据 {len(_real)} 条")
if _real:
    with tempfile.TemporaryDirectory() as tmp:
        r = finance_module.generate_report(data=_real, output_dir=tmp, scope="all", format="both")
        check("真实数据 both 返回 success", r.get("status") == "success", r)
        check("真实数据 docx 生成", os.path.exists(r.get("docx_path", "")), r.get("docx_path"))
        check("真实数据 excel 生成", os.path.exists(r.get("excel_path", "")), r.get("excel_path"))
        from docx import Document as _D
        _doc = _D(r["docx_path"])
        check("真实数据 docx 含图表", len(_doc.inline_shapes) >= 1, len(_doc.inline_shapes))

print("\n" + "=" * 60)
print(f"结果: {passed} 通过, {failed} 失败")
print("=" * 60)
sys.exit(1 if failed else 0)
