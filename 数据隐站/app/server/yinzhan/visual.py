"""视觉标记检测：红章（公章/私章）与手写签名候选。

文本检测器只能读 OCR 出来的字，抓不到「红色印泥」和「手写笔迹」。
但这两类恰恰是合同里最敏感的东西 —— 公章能证明主体身份，手写签名能证明签署人。

做法：
- 红章：印泥是彩色的，扫描文字是灰的。算 redness = R - (G+B)/2，
  真实红章 p99 在 25 以上、像素占比 2%~20%；文字区 p99 只有 5、几乎没有 >15 的像素。
  先在 16×16 块上聚合（对淡章更稳），再回到像素级收紧边界。
- 签名：没有颜色特征，靠「有墨迹但没有 OCR 文字」来猜，一律标成待确认，
  绝不自动当成已处理。

所有坐标都返回「页面像素坐标」，由调用方换算成 PDF pt。
"""
from __future__ import annotations

import numpy as np

try:  # cv2 是 rapidocr 的依赖，正常都在
    import cv2
except Exception:  # pragma: no cover
    cv2 = None


# ────────────────────────── 红章 ──────────────────────────

REDNESS_THRESHOLD = 16.0     # 低于这个基本是灰色文字/扫描噪声
RED_MIN = 80                 # 太暗的红色（黑红）不算，避免把深色块误判
BLOCK = 16                   # 块级聚合边长（像素）
BLOCK_SCORE = 0.012          # 块内红像素占比超过它才算「红区」
MIN_BLOCKS = 6               # 单个红区至少 6×6 块 ≈ 96px，约 1.2cm


def _redness(arr: np.ndarray) -> np.ndarray:
    r = arr[:, :, 0].astype(np.int16)
    g = arr[:, :, 1].astype(np.int16)
    b = arr[:, :, 2].astype(np.int16)
    return r - (g + b) // 2


def _to_gray_mask(redness: np.ndarray, arr: np.ndarray) -> np.ndarray:
    return (((redness > REDNESS_THRESHOLD) & (arr[:, :, 0] > RED_MIN))
            .astype(np.uint8))


def _merge_boxes(boxes: list[list[float]], gap: float) -> list[list[float]]:
    """把挨得很近/互相包含的框合并成一个。"""
    changed = True
    out = [list(b) for b in boxes]
    while changed:
        changed = False
        merged: list[list[float]] = []
        for b in out:
            hit = None
            for m in merged:
                if not (b[2] < m[0] - gap or m[2] < b[0] - gap or
                        b[3] < m[1] - gap or m[3] < b[1] - gap):
                    hit = m
                    break
            if hit is None:
                merged.append(b)
            else:
                hit[0] = min(hit[0], b[0]); hit[1] = min(hit[1], b[1])
                hit[2] = max(hit[2], b[2]); hit[3] = max(hit[3], b[3])
                changed = True
        out = merged
    return out


def find_seals(arr_rgb: np.ndarray, dpi: float = 200.0) -> list[tuple[float, float, float, float]]:
    """在一页渲染图上找红色印章，返回像素坐标框列表。"""
    if cv2 is None or arr_rgb is None:
        return []
    h, w = arr_rgb.shape[:2]
    if h < 64 or w < 64:
        return []
    redness = _redness(arr_rgb)
    mask = _to_gray_mask(redness, arr_rgb)

    # 1) 块级聚合：淡章的笔画很稀，逐像素找会碎成一堆小点
    hb, wb = h // BLOCK, w // BLOCK
    if hb < 4 or wb < 4:
        return []
    score = mask[:hb * BLOCK, :wb * BLOCK].reshape(hb, BLOCK, wb, BLOCK).mean(axis=(1, 3))
    gem = (score > BLOCK_SCORE).astype(np.uint8)
    # 这里千万别做开运算：淡章（复写纸/旧扫描件）笔画很稀，
    # 一旦开运算就把整个章抹没了。靠后面的尺寸过滤去噪就够。

    n, _, stats, _ = cv2.connectedComponentsWithStats(gem, 8)
    boxes: list[list[float]] = []
    # 2) 回到像素级收紧边界：只统计这个块区里的红像素。
    #    不做进一步拆分 —— 圆章和紧挨着的方章本来就该一起盖掉，
    #    硬拆会拆出一堆碎片，反而漏盖。
    for i in range(1, n):
        bx, by, bw, bh, _area = stats[i]
        if bw < MIN_BLOCKS or bh < MIN_BLOCKS:
            continue
        if bw > wb * 0.9 or bh > hb * 0.9:      # 整页泛红，多半是扫描偏色
            continue
        x0, y0 = bx * BLOCK, by * BLOCK
        x1, y1 = min(w, (bx + bw) * BLOCK), min(h, (by + bh) * BLOCK)
        sub = mask[y0:y1, x0:x1]
        ys, xs = np.nonzero(sub)
        if len(xs) < 40:
            continue
        bx0, bx1 = x0 + int(xs.min()), x0 + int(xs.max()) + 1
        by0, by1 = y0 + int(ys.min()), y0 + int(ys.max()) + 1
        # 3) 外扩 4%，把压边的那几道笔画也盖住
        pad_x, pad_y = (bx1 - bx0) * 0.04, (by1 - by0) * 0.04
        boxes.append([bx0 - pad_x, by0 - pad_y, bx1 + pad_x, by1 + pad_y])

    if not boxes:
        return []
    boxes = _merge_boxes(boxes, gap=max(6.0, dpi * 0.04))
    # 合并后可能又变太大，再过滤一次
    out = []
    for b in boxes:
        bw, bh = b[2] - b[0], b[3] - b[1]
        if bw < dpi * 0.22 or bh < dpi * 0.22:      # 小于约 5mm，不像章
            continue
        if bw > w * 0.9 or bh > h * 0.9:
            continue
        out.append((float(b[0]), float(b[1]), float(b[2]), float(b[3])))
    return out


# ────────────────────────── 手写签名 ──────────────────────────

SIGN_LABELS = ("代表", "签字", "签名", "签署", "经办", "法定代表人", "委托代理人", "负责人")


def find_ink_blobs(arr_rgb: np.ndarray, text_boxes: list[tuple[float, float, float, float]],
                   dpi: float = 200.0) -> list[tuple[float, float, float, float]]:
    """找「有墨迹但 OCR 没读出字」的区域 —— 手写签名的典型特征。

    text_boxes 是这一页已经识别到的文字框；把它们涂掉之后剩下的深色团块，
    如果大小和笔画密度像签名，就当成签名候选返回（上层一律标成待确认）。
    """
    if cv2 is None or arr_rgb is None:
        return []
    h, w = arr_rgb.shape[:2]
    gray = cv2.cvtColor(arr_rgb, cv2.COLOR_RGB2GRAY) if arr_rgb.shape[2] == 3 else arr_rgb
    ink = (gray < 150).astype(np.uint8) * 255

    # 去掉已知文字区域（略微外扩，避免字边残留）
    pad = max(2, int(dpi * 0.012))
    for (x0, y0, x1, y1) in text_boxes:
        cv2.rectangle(ink, (int(x0) - pad, int(y0) - pad), (int(x1) + pad, int(y1) + pad), 0, -1)

    k = max(3, int(dpi * 0.05)) | 1
    ink = cv2.morphologyEx(ink, cv2.MORPH_CLOSE,
                           cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    ink = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

    n, _, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    out: list[tuple[float, float, float, float]] = []
    edge_x, edge_y = max(4, int(w * 0.02)), max(4, int(h * 0.02))
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if bw < dpi * 0.25 or bh < dpi * 0.12:          # 太小，可能是标点/噪点
            continue
        if bw > w * 0.6 or bh > h * 0.25:               # 太大，可能是页边/表格线
            continue
        if x < edge_x or y < edge_y or x + bw > w - edge_x or y + bh > h - edge_y:
            continue                                     # 贴边的一律不要
        if (y + bh / 2.0) < h * 0.30:
            continue                                     # 页眉区域的 logo/标题不要
        fill = area / float(bw * bh)
        if fill < 0.04 or fill > 0.75:                   # 太空=噪点，太实=色块
            continue
        out.append((float(x), float(y), float(x + bw), float(y + bh)))
    return _merge_boxes([list(b) for b in out], gap=dpi * 0.03)


# ────────────────────────── 一页的总分析 ──────────────────────────

def analyze_page(arr_rgb: np.ndarray, dpi: float,
                 text_boxes_px: list[tuple[float, float, float, float]] | None = None
                 ) -> list[tuple[str, tuple[float, float, float, float]]]:
    """返回 [(kind, box_px)]，kind 是 seal / signature。

    text_boxes_px 是本页 OCR 出来的文字框（像素坐标），用来把「已经读出来的字」
    从墨迹图里抠掉，剩下的才可能是手写。
    """
    marks: list[tuple[str, tuple[float, float, float, float]]] = []
    seals = find_seals(arr_rgb, dpi)
    for b in seals:
        marks.append(("seal", b))
    if text_boxes_px is not None:
        for b in find_ink_blobs(arr_rgb, text_boxes_px, dpi):
            # 落在印章框里的墨迹不用重复标
            cx, cy = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
            if any(s[0] <= cx <= s[2] and s[1] <= cy <= s[3] for s in seals):
                continue
            marks.append(("signature", b))
    return marks
