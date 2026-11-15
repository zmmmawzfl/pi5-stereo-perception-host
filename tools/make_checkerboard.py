"""生成可打印的棋盘格标定板（精确物理尺寸，1:1）。

用法::

    cd macos-pc && python -m tools.make_checkerboard --pattern 9x6 --square-mm 30 --page a3 \
        --out ../artifacts/calibration/board_9x6_30mm_a3

输出 `.pdf`（页面尺寸精确到毫米，直接 100% 打印）+ `.png`（300 dpi 预览）。
页面底部带一条 100 mm 比例尺，打印完先用卡尺量它，量出来不是 100 mm
就说明打印被缩放了，改回"实际大小/100%"再打。

为什么是 9×6：`pc.calib` 的 `--pattern` 收的是**内角点数**，9×6 内角点
= 10×7 个方格，和默认参数一致。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Tuple

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


def board_extent_mm(cols: int, rows: int, square_mm: float, margin_squares: float = 1.0) -> Tuple[float, float]:
    """整块板（含白边）的宽高，毫米。cols×rows 是内角点数。"""
    return (
        (cols + 1 + 2 * margin_squares) * square_mm,
        (rows + 1 + 2 * margin_squares) * square_mm,
    )


def page_px(page: str, dpi: int = DPI) -> Tuple[int, int]:
    w_mm, h_mm = PAGES_MM[page]
    return round(w_mm / 25.4 * dpi), round(h_mm / 25.4 * dpi)


def mm_to_px(mm: float, dpi: int = DPI) -> float:
    return mm / 25.4 * dpi


def render(
    cols: int = 9,
    rows: int = 6,
    square_mm: float = 30.0,
    page: str = "a3",
    margin_squares: float = 1.0,
    dpi: int = DPI,
    ruler: bool = True,
) -> Image.Image:
    if page not in PAGES_MM:
        raise ValueError(f"未知纸张 {page!r}，可选 {sorted(PAGES_MM)}")
    page_w_mm, page_h_mm = PAGES_MM[page]
    board_w_mm, board_h_mm = board_extent_mm(cols, rows, square_mm, margin_squares)
    if board_w_mm > page_w_mm - 4 or board_h_mm > page_h_mm - 4:
        raise ValueError(
            f"{cols}x{rows} / {square_mm} mm 需要 {board_w_mm:.0f}x{board_h_mm:.0f} mm，"
            f"{page.upper()} 只有 {page_w_mm:.0f}x{page_h_mm:.0f} mm；换大纸或缩小方格"
        )

    width_px, height_px = page_px(page, dpi)
    img = Image.new("L", (width_px, height_px), 255)
    draw = ImageDraw.Draw(img)

    px = mm_to_px(square_mm, dpi)
    x0 = (width_px - mm_to_px(board_w_mm, dpi)) / 2.0
    y0 = (height_px - mm_to_px(board_h_mm, dpi)) / 2.0
    x0 += mm_to_px(margin_squares * square_mm, dpi)
    y0 += mm_to_px(margin_squares * square_mm, dpi)

    # 方格数 = 内角点 + 1；左上角为黑格，保证 9x6 内角点被检出
    for r in range(rows + 1):
        for c in range(cols + 1):
            if (r + c) % 2 == 0:
                left = x0 + c * px
                top = y0 + r * px
                draw.rectangle([round(left), round(top), round(left + px) - 1, round(top + px) - 1], fill=0)

    if ruler:
        bar_mm = 100.0
        bar_px = mm_to_px(bar_mm, dpi)
        by = height_px - mm_to_px(6.0, dpi)
        bx = mm_to_px(20.0, dpi)
        thick = max(2, round(mm_to_px(1.0, dpi)))
        draw.rectangle([round(bx), round(by), round(bx + bar_px), round(by) + thick], fill=0)
        for tick in range(0, 101, 10):
            tx = bx + mm_to_px(tick, dpi)
            h = mm_to_px(4.0 if tick % 50 else 6.0, dpi)
            draw.line([round(tx), round(by), round(tx), round(by - h)], fill=0, width=thick)
        try:
            font = ImageFont.truetype(FONT, size=max(12, round(mm_to_px(5.0, dpi))))
        except OSError:
            font = ImageFont.load_default()
        draw.text((round(bx), round(by + thick + mm_to_px(2.0, dpi))), "100 mm scale check", fill=0, font=font)

    return img


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成 1:1 可打印棋盘格标定板")
    ap.add_argument("--pattern", default="9x6", help="内角点数，如 9x6")
    ap.add_argument("--square-mm", type=float, default=30.0, help="方格边长（毫米）")
    ap.add_argument("--page", default="a3", choices=sorted(PAGES_MM))
    ap.add_argument("--margin-squares", type=float, default=1.0, help="白边宽度，单位=方格")
    ap.add_argument("--dpi", type=int, default=DPI)
    ap.add_argument("--out", required=True, help="输出路径前缀（不带扩展名）")
    args = ap.parse_args(argv)

    try:
        cols, rows = (int(v) for v in str(args.pattern).lower().split("x"))
    except ValueError:
        ap.error("--pattern 形如 9x6")
    img = render(cols, rows, args.square_mm, args.page, args.margin_squares, args.dpi)
    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)

    img.save(out.with_suffix(".png"), dpi=(args.dpi, args.dpi))
    img.convert("L").save(out.with_suffix(".pdf"), "PDF", resolution=float(args.dpi))

    board_w, board_h = board_extent_mm(cols, rows, args.square_mm, args.margin_squares)
    print(f"内角点 {cols}x{rows}，方格 {args.square_mm:g} mm，白边 {args.margin_squares:g} 格")
    print(f"整板 {board_w:.0f} x {board_h:.0f} mm（含白边），纸 {args.page.upper()}")
    print(f"输出 {out.with_suffix('.pdf')} / {out.with_suffix('.png')}")
    print("打印要求：100% / 实际大小，不要'适合页面'；打完量方格与 100 mm 比例尺")
    return 0


if __name__ == "__main__":
    sys.exit(main())
