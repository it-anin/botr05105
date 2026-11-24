"""หาแถวใน DataWindow จากหน้าตาข้อความ แทนการนับเลขแถว

รายการรายงานทางซ้ายของ ProMaxx เพิ่ม/ลดแถวเองได้ (เช่น 26/09 มีแถว R16.1
โผล่เพิ่มที่แถว 3 ทำให้ทุกแถวข้างล่างเลื่อนลง บอทที่กดตามเลขแถวจึงไปกด R04.4
แทน R05.1) จึงต้องหาแถวจาก "ภาพข้อความ" ของแถวนั้นเองทุกครั้งก่อนกด

เทียบเฉพาะ "หมึก" (pixel สีเข้ม) ไม่เทียบสีพื้น จึงไม่สนว่าแถวเป็นพื้นฟ้า
พื้นขาว หรือกำลังถูกชี้อยู่ - ใช้ Pillow ล้วน (exe ตัด numpy ออกไปแล้ว)
"""

from __future__ import annotations

from PIL import Image, ImageChops

# pixel ที่เข้มกว่านี้นับเป็นตัวหนังสือ - พื้นฟ้าของแถวกลุ่ม (~215) และพื้นขาว
# ของแถวลูกสว่างกว่านี้มาก ส่วนตัวหนังสือดำ/น้ำเงินเข้มกว่านี้มาก
INK_THRESHOLD = 150

# สัดส่วน pixel ที่ไม่ตรงต่อจำนวนหมึกของภาพอ้างอิง - ตัวจริงได้ ~0
# ข้อความอื่นได้เกิน 0.5 แทบทุกกรณี
DEFAULT_MAX_MISMATCH = 0.15

_INK_LUT = [255 if v < INK_THRESHOLD else 0 for v in range(256)]


class RowFindError(Exception):
    pass


class RowNotFound(RowFindError):
    """ไม่มีแถวไหนหน้าตาเหมือนภาพอ้างอิง"""


class RowAmbiguous(RowFindError):
    """มีหลายแถวหน้าตาเหมือนภาพอ้างอิง - กดไม่ได้เพราะไม่รู้ว่าตัวไหน"""


def ink_mask(img: Image.Image) -> Image.Image:
    """แปลงภาพเป็นหน้ากากตัวหนังสือ (หมึก = 255, พื้น = 0)"""
    return img.convert("L").point(_INK_LUT)


def find_row(dw_img: Image.Image, ref_img: Image.Image, x: int, *,
             x_slack: int = 4,
             max_mismatch: float = DEFAULT_MAX_MISMATCH) -> tuple[int, int, float]:
    """หาตำแหน่งของภาพอ้างอิงในภาพ DataWindow

    x คือขอบซ้ายของภาพอ้างอิงในภาพ DataWindow (ตอนที่ตัดมา) จะค้นแค่ x ± x_slack
    แต่ค้นแกน y ตลอดความสูง เพราะแถวเลื่อนขึ้นลงได้

    คืน (x, y มุมบนซ้ายที่เจอ, คะแนน) - โยน RowNotFound / RowAmbiguous
    ถ้าไม่เจอหรือเจอมากกว่าหนึ่งที่
    """
    hay = ink_mask(dw_img)
    ref = ink_mask(ref_img)
    ref_w, ref_h = ref.size
    hay_w, hay_h = hay.size
    ref_ink = ref.histogram()[255]
    if ref_ink == 0:
        raise RowFindError("ภาพอ้างอิงไม่มีตัวหนังสือเลย (ตัดภาพผิดที่?)")
    if ref_w > hay_w or ref_h > hay_h:
        raise RowFindError(
            f"ภาพอ้างอิงขนาด {ref.size} ใหญ่กว่าภาพ DataWindow {hay.size}"
        )

    x_lo = max(0, x - x_slack)
    x_hi = min(hay_w - ref_w, x + x_slack)
    if x_hi < x_lo:
        raise RowFindError(
            f"x={x} ของภาพอ้างอิงกว้าง {ref_w} ล้นขอบ DataWindow กว้าง {hay_w}"
        )

    best = (1.0e9, x, 0)
    hits: list[tuple[float, int, int]] = []
    for yy in range(0, hay_h - ref_h + 1):
        for xx in range(x_lo, x_hi + 1):
            window = hay.crop((xx, yy, xx + ref_w, yy + ref_h))
            diff = ImageChops.difference(window, ref).histogram()[255]
            score = diff / ref_ink
            if score < best[0]:
                best = (score, xx, yy)
            if score <= max_mismatch:
                hits.append((score, xx, yy))

    if not hits:
        raise RowNotFound(
            f"ไม่เจอแถวที่หน้าตาเหมือนภาพอ้างอิง (ใกล้สุดที่ y={best[2]} "
            f"คะแนน {best[0]:.2f} ต้องไม่เกิน {max_mismatch:.2f})"
        )

    # จุดที่ห่างกันไม่ถึงครึ่งความสูงภาพอ้างอิง ถือเป็นแถวเดียวกัน
    groups: list[tuple[float, int, int]] = []
    last_y = None
    for hit in sorted(hits, key=lambda h: h[2]):
        if last_y is not None and hit[2] - last_y < ref_h / 2:
            if hit[0] < groups[-1][0]:
                groups[-1] = hit
        else:
            groups.append(hit)
        last_y = hit[2]

    if len(groups) > 1:
        where = ", ".join(f"y={g[2]} (คะแนน {g[0]:.2f})" for g in groups)
        raise RowAmbiguous(f"เจอแถวที่หน้าตาเหมือนภาพอ้างอิง {len(groups)} ที่: {where}")

    score, xx, yy = groups[0]
    return xx, yy, score
