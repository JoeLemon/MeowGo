# -*- coding: utf-8 -*-
"""可选 OCR 文本定位（pytesseract）。

不把 OCR 引擎打进安装包：体积会翻几倍，而且大多数纯图片流程用不到。
未安装时 ``available()`` 返回 False，引擎会给出明确的安装提示。
"""
from __future__ import annotations

from typing import Optional

OCR_INSTALL_HINT = (
    "未安装 OCR 组件，文字识别步骤无法运行。请执行："
    "pip install pytesseract，并安装 Tesseract-OCR"
    "（https://github.com/UB-Mannheim/tesseract/wiki，"
    "安装时勾选中文语言包）")


class OcrUnavailable(RuntimeError):
    """OCR 依赖缺失或引擎不可用。"""


def available() -> bool:
    try:
        import pytesseract  # noqa: F401
    except Exception:  # noqa: BLE001 - 缺失 / DLL 加载失败都按不可用处理
        return False
    return True


def _engine():
    try:
        import pytesseract
        from PIL import Image  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        raise OcrUnavailable(OCR_INSTALL_HINT) from exc
    return pytesseract


def find_text(image_bgr, text: str, lang: str = "chi_sim+eng",
              min_confidence: float = 40.0
              ) -> Optional[tuple[float, float]]:
    """在 BGR 图像里找一段文字，返回其行框中心 (x, y)；找不到返回 None。

    按 OCR 的行（block/par/line）聚合后做子串匹配，中文被拆成多个词也能
    命中；比较时忽略空格与大小写。
    """
    from PIL import Image

    target = "".join((text or "").split()).casefold()
    if not target:
        return None
    pytesseract = _engine()
    try:
        data = pytesseract.image_to_data(
            Image.fromarray(image_bgr[:, :, ::-1]),
            lang=lang, output_type=pytesseract.Output.DICT,
            config="--psm 6")
    except Exception as exc:  # noqa: BLE001 - 多数是 Tesseract 本体没装
        raise OcrUnavailable(
            f"{OCR_INSTALL_HINT}\n（引擎返回：{exc}）") from exc

    lines: dict[tuple[int, int, int], dict] = {}
    for index, raw in enumerate(data.get("text", [])):
        word = (raw or "").strip()
        if not word:
            continue
        try:
            confidence = float(data["conf"][index])
        except (TypeError, ValueError):
            confidence = -1.0
        if confidence < min_confidence:
            continue
        key = (int(data["block_num"][index]),
               int(data["par_num"][index]),
               int(data["line_num"][index]))
        left = int(data["left"][index])
        top = int(data["top"][index])
        width = int(data["width"][index])
        height = int(data["height"][index])
        entry = lines.setdefault(key, {"words": [], "left": left, "top": top,
                                       "right": left + width,
                                       "bottom": top + height})
        entry["words"].append(word)
        entry["left"] = min(entry["left"], left)
        entry["top"] = min(entry["top"], top)
        entry["right"] = max(entry["right"], left + width)
        entry["bottom"] = max(entry["bottom"], top + height)

    for entry in lines.values():
        joined = "".join(entry["words"]).casefold()
        if target in joined:
            return ((entry["left"] + entry["right"]) / 2,
                    (entry["top"] + entry["bottom"]) / 2)
    # 行内没命中时再按单词兜底（例如目标只包含行内一部分且被空格切开）
    for entry in lines.values():
        if any(target in word.casefold() for word in entry["words"]):
            return ((entry["left"] + entry["right"]) / 2,
                    (entry["top"] + entry["bottom"]) / 2)
    return None
