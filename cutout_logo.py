# -*- coding: utf-8 -*-
"""
MeowGo logo 抠图脚本
- 输入: meowgo_logo_raw.png (纯白背景)
- 输出: meowgo_logo_transparent.png (透明背景 PNG, 已裁剪至主体)
- 同时打印 logo 主色 (黑色文字 / 橙色点缀 / 背景) 的精确 RGB, 供视觉规范取色
原理: 基于与纯白背景的颜色距离生成 alpha, 对边缘做 unpremultiply 去白边
"""
import numpy as np
from PIL import Image

SRC = r"D:\Project\auto-tools\python_auto\meowgo_logo_raw.png"
DST = r"D:\Project\auto-tools\python_auto\meowgo_logo_transparent.png"

T1, T2 = 22.0, 50.0  # 颜色距离阈值: <T1 全透明, >T2 全保留, 之间线性过渡(羽化)

img = Image.open(SRC).convert("RGBA")
arr = np.array(img).astype(np.float32)
r, g, b, a = arr[..., 0], arr[..., 1], arr[..., 2], arr[..., 3]

dist = np.sqrt((r - 255.0) ** 2 + (g - 255.0) ** 2 + (b - 255.0) ** 2)
alpha = np.clip((dist - T1) / (T2 - T1), 0.0, 1.0) * (a / 255.0)

# 去白边: pixel = fg * alpha + 255 * (1 - alpha)  =>  fg = (pixel - 255*(1-alpha)) / alpha
alpha_safe = np.maximum(alpha, 1e-6)
factor = (1.0 - alpha) * 255.0
r_new = np.clip((r - factor) / alpha_safe, 0, 255)
g_new = np.clip((g - factor) / alpha_safe, 0, 255)
b_new = np.clip((b - factor) / alpha_safe, 0, 255)
# 全透明像素的颜色归零, 避免透明黑点
keep = alpha > 0.003
r_new = np.where(keep, r_new, 0.0)
g_new = np.where(keep, g_new, 0.0)
b_new = np.where(keep, b_new, 0.0)

out = np.dstack([r_new, g_new, b_new, alpha * 255.0]).astype(np.uint8)
result = Image.fromarray(out, "RGBA")

# 裁剪到主体边界 (加 2% padding)
ys, xs = np.where(alpha > 0.004)
pad_x = int((xs.max() - xs.min()) * 0.02) + 2
pad_y = int((ys.max() - ys.min()) * 0.02) + 2
x0, x1 = max(0, xs.min() - pad_x), min(result.width, xs.max() + pad_x)
y0, y1 = max(0, ys.min() - pad_y), min(result.height, ys.max() + pad_y)
result = result.crop((x0, y0, x1, y1))
result.save(DST)

# ---- 取色: 统计主体区域的颜色聚类 ----
px = np.array(result.convert("RGBA")).astype(np.float32)
m = px[..., 3] > 128
body = px[m][:, :3]
# 黑色文字: 亮度 < 120 的像素取中位
dark = body[body.mean(axis=1) < 120]
# 橙色点缀: 红色通道明显高于蓝/绿 且 亮度中等
warm = body[(body[:, 0] > 150) & (body[:, 1] > 60) & (body[:, 1] < 220) & (body[:, 2] < 140)]
def rgb_of(p):
    return tuple(int(round(v)) for v in np.median(p, axis=0)) if len(p) else None

print("主体尺寸:", result.size)
print("黑色主体 RGB:", rgb_of(dark))
print("橙色点缀 RGB:", rgb_of(warm))
print("输出:", DST)
