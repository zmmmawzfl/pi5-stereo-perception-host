"""生成可打印的棋盘格标定板（精确物理尺寸，1:1）。

用法::

    cd macos-pc && python -m tools.make_checkerboard --pattern 9x6 --square-mm 30 --page a3 \
        --out ../artifacts/calibration/board_9x6_30mm_a3

输出 .pdf（矢量，页面尺寸精确到毫米，直接 100% 打印）+ .png（300 dpi 预览）。
页面底部带一条 100 mm 比例尺，打印完先用卡尺量它，量出来不是 100 mm
就说明打印被缩放了，改回"实际大小/100%"再打。

为什么是 9×6：pc.calib 的 --pattern 收的是**内角点数**，9×6 内角点
= 10×7 个方格，和默认参数一致。

为什么 PDF 是矢量：早先的写法把整页 300 dpi 位图交给 Pillow 存 PDF（DCTDecode），
方格边缘带 JPEG 振铃，打印出来发灰；现在直接写 PDF 绘图指令（re f），
边缘是数学直线，任何打印分辨率下都锐利。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Tuple

from PIL import Image, ImageDraw, ImageFont

#: 常见纸张（横向，毫米）
PAGES_MM = {
    "a4": (297.0, 210.0),
    "a3": (420.0, 297.0),
    "a2": (594.0, 420.0),
    "letter": (279.4, 215.9),
}

DPI = 300
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

MM_PER_INCH = 25.4
PT_PER_INCH = 72.0

#: 底部比例尺（毫米）：条长 100 mm，刻度朝上，文字放在条右侧——
#: 老版本把文字画在条下方，字高 5 mm 而条距底只有 6 mm，底部被页边裁掉。
RULER_BAR_MM = 100.0
RULER_LEFT_MM = 20.0
RULER_BOTTOM_MM = 6.0
RULER_THICK_MM = 1.0
RULER_TICK_MM = 4.0
RULER_TICK_MAJOR_MM = 6.0
RULER_GAP_MM = 4.0
RULER_FONT_MM = 4.5
RULER_LABEL = "100 mm scale check"


def board_extent_mm(cols: int, rows: int, square_mm: float, margin_squares: float = 1.0) -> Tuple[float, float]:
    """整块板（含白边）的宽高，毫米。cols×rows 是内角点数。"""
    return (
        (cols + 1 + 2 * margin_squares) * square_mm,
        (rows + 1 + 2 * margin_squares) * square_mm,
    )


def board_origin_mm(
    cols: int, rows: int, square_mm: float, page: str, margin_squares: float = 1.0
) -> Tuple[float, float]:
    """网格左上角在页面上的位置（毫米，左上角为原点）。"""
    page_w_mm, page_h_mm = PAGES_MM[page]
    board_w_mm, board_h_mm = board_extent_mm(cols, rows, square_mm, margin_squares)
    return (
        (page_w_mm - board_w_mm) / 2.0 + margin_squares * square_mm,
        (page_h_mm - board_h_mm) / 2.0 + margin_squares * square_mm,
    )


def page_px(page: str, dpi: int = DPI) -> Tuple[int, int]:
    w_mm, h_mm = PAGES_MM[page]
    return round(w_mm / 25.4 * dpi), round(h_mm / 25.4 * dpi)


def mm_to_px(mm: float, dpi: int = DPI) -> float:
    return mm / 25.4 * dpi


def _check_page(
    cols: int, rows: int, square_mm: float, page: str, margin_squares: float
) -> Tuple[float, float, float, float]:
    if page not in PAGES_MM:
        raise ValueError(f"未知纸张 {page!r}，可选 {sorted(PAGES_MM)}")
    page_w_mm, page_h_mm = PAGES_MM[page]
    board_w_mm, board_h_mm = board_extent_mm(cols, rows, square_mm, margin_squares)
    if board_w_mm > page_w_mm - 4 or board_h_mm > page_h_mm - 4:
        raise ValueError(
            f"{cols}x{rows} / {square_mm} mm 需要 {board_w_mm:.0f}x{board_h_mm:.0f} mm，"
            f"{page.upper()} 只有 {page_w_mm:.0f}x{page_h_mm:.0f} mm；换大纸或缩小方格"
        )
    return page_w_mm, page_h_mm, board_w_mm, board_h_mm


def ruler_parts(page_h_mm: float) -> Tuple[Tuple[float, float, float, float], List[Tuple[float, float]], Tuple[float, float]]:
    """比例尺几何（毫米，左上角原点）。

    返回 (bar, ticks, label)：bar = (left, top, w, h)；ticks = [(x, 高度), ...]，
    高度向上量；label = (x, 基线 y)。
    """
    top = page_h_mm - RULER_BOTTOM_MM - RULER_THICK_MM
    bar = (RULER_LEFT_MM, top, RULER_BAR_MM, RULER_THICK_MM)
    ticks = [
        (RULER_LEFT_MM + t, RULER_TICK_MAJOR_MM if t % 50 == 0 else RULER_TICK_MM)
        for t in range(0, int(RULER_BAR_MM) + 1, 10)
    ]
    label = (RULER_LEFT_MM + RULER_BAR_MM + RULER_GAP_MM, page_h_mm - RULER_BOTTOM_MM)
    return bar, ticks, label


def render(
    cols: int = 9,
    rows: int = 6,
    square_mm: float = 30.0,
    page: str = "a3",
    margin_squares: float = 1.0,
    dpi: int = DPI,
    ruler: bool = True,
) -> Image.Image:
    page_w_mm, page_h_mm, board_w_mm, _ = _check_page(cols, rows, square_mm, page, margin_squares)

    width_px, height_px = page_px(page, dpi)
    img = Image.new("L", (width_px, height_px), 255)
    draw = ImageDraw.Draw(img)

    px = mm_to_px(square_mm, dpi)
    x0_mm, y0_mm = board_origin_mm(cols, rows, square_mm, page, margin_squares)
    x0 = mm_to_px(x0_mm, dpi)
    y0 = mm_to_px(y0_mm, dpi)

    # 方格数 = 内角点 + 1；左上角为黑格，保证 9x6 内角点被检出
    for r in range(rows + 1):
        for c in range(cols + 1):
            if (r + c) % 2 == 0:
                left = x0 + c * px
                top = y0 + r * px
                draw.rectangle([round(left), round(top), round(left + px) - 1, round(top + px) - 1], fill=0)

    if ruler:
        bar, ticks, (lx, ly) = ruler_parts(page_h_mm)
        bx, by, bw, bh = (mm_to_px(v, dpi) for v in bar)
        thick = max(2, round(bh))
        draw.rectangle([round(bx), round(by), round(bx + bw), round(by + thick)], fill=0)
        for tx_mm, h_mm in ticks:
            tx = mm_to_px(tx_mm, dpi)
            draw.line(
                [round(tx), round(by), round(tx), round(by - mm_to_px(h_mm, dpi))],
                fill=0,
                width=thick,
            )
        try:
            font = ImageFont.truetype(FONT, size=max(12, round(mm_to_px(RULER_FONT_MM, dpi))))
        except OSError:
            font = ImageFont.load_default()
        draw.text(
            (round(mm_to_px(lx, dpi)), round(mm_to_px(ly, dpi))),
            RULER_LABEL,
            fill=0,
            font=font,
            anchor="ls",
        )

    return img


def _num(v: float) -> str:
    s = f"{v:.3f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def _pt(mm: float) -> float:
    return mm * PT_PER_INCH / MM_PER_INCH


def pdf_bytes(
    cols: int = 9,
    rows: int = 6,
    square_mm: float = 30.0,
    page: str = "a3",
    margin_squares: float = 1.0,
) -> bytes:
    """矢量 PDF：方格与比例尺都是绘图指令，页面尺寸精确到 0.001 pt（约 0.4 µm）。"""
    page_w_mm, page_h_mm, _, _ = _check_page(cols, rows, square_mm, page, margin_squares)
    x0_mm, y0_mm = board_origin_mm(cols, rows, square_mm, page, margin_squares)

    ops = ["1 g", "0 0 0 rg"]
    for r in range(rows + 1):
        for c in range(cols + 1):
            if (r + c) % 2 == 0:
                # PDF 原点在左下角，所以 y 要翻过来
                left_mm = x0_mm + c * square_mm
                bottom_mm = page_h_mm - (y0_mm + (r + 1) * square_mm)
                ops.append(
                    f"{_num(_pt(left_mm))} {_num(_pt(bottom_mm))} "
                    f"{_num(_pt(square_mm))} {_num(_pt(square_mm))} re f"
                )

    bar, ticks, (lx_mm, ly_mm) = ruler_parts(page_h_mm)
    bar_l, _, bar_w, bar_h = bar
    ops.append(
        f"{_num(_pt(bar_l))} {_num(_pt(RULER_BOTTOM_MM))} "
        f"{_num(_pt(bar_w))} {_num(_pt(bar_h))} re f"
    )
    for tick_x_mm, tick_h_mm in ticks:
        ops.append(
            f"{_num(_pt(tick_x_mm - 0.15))} {_num(_pt(RULER_BOTTOM_MM + bar_h))} "
            f"{_num(_pt(0.3))} {_num(_pt(tick_h_mm))} re f"
        )
    ops.append(
        "BT /F1 "
        f"{_num(_pt(RULER_FONT_MM))} Tf 1 0 0 1 {_num(_pt(lx_mm))} {_num(_pt(page_h_mm - ly_mm))} Tm "
        f"({RULER_LABEL}) Tj ET"
    )
    content = ("\n".join(ops) + "\n").encode("ascii")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_num(_pt(page_w_mm))} {_num(_pt(page_h_mm))}] "
            f"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ).encode("ascii"),
        b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n" + content + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: List[int] = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode("ascii")
    return bytes(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成 1:1 可打印棋盘格标定板")
    ap.add_argument("--pattern", default="9x6", help="内角点数，如 9x6")
    ap.add_argument("--square-mm", type=float, default=30.0, help="方格边长（毫米）")
    ap.add_argument("--page", default="a3", choices=sorted(PAGES_MM))
    ap.add_argument("--margin-squares", type=float, default=1.0, help="白边宽度，单位=方格")
    ap.add_argument("--dpi", type=int, default=DPI, help="PNG 预览分辨率（PDF 与它无关）")
    ap.add_argument("--out", required=True, help="输出路径前缀（不带扩展名）")
    args = ap.parse_args(argv)

    try:
        cols, rows = (int(v) for v in str(args.pattern).lower().split("x"))
    except ValueError:
        ap.error("--pattern 形如 9x6")

    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)

    pdf = pdf_bytes(cols, rows, args.square_mm, args.page, args.margin_squares)
    out.with_suffix(".pdf").write_bytes(pdf)

    img = render(cols, rows, args.square_mm, args.page, args.margin_squares, args.dpi)
    img.save(out.with_suffix(".png"), dpi=(args.dpi, args.dpi))

    board_w, board_h = board_extent_mm(cols, rows, args.square_mm, args.margin_squares)
    print(f"内角点 {cols}x{rows}，方格 {args.square_mm:g} mm，白边 {args.margin_squares:g} 格")
    print(f"整板 {board_w:.0f} x {board_h:.0f} mm（含白边），纸 {args.page.upper()}")
    print(f"输出 {out.with_suffix('.pdf')}（矢量，{len(pdf)} B） / {out.with_suffix('.png')}")
    print("打印要求：100% / 实际大小，不要'适合页面'；打完量方格与 100 mm 比例尺")
    return 0


if __name__ == "__main__":
    sys.exit(main())
