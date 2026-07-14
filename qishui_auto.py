import cv2
import numpy as np
import subprocess
import time
import random
import re
import os
from datetime import datetime

from rapidocr_onnxruntime import RapidOCR

# ================== 配置区 ==================
# 目标文字（按优先级：先点靠前的）
TARGETS = ["领取奖励", "继续观看", "领取成功"]
# 置信度下限
OCR_SCORE = 0.5
# 允许的轻微识别误差（去空格后做包含/近似）
FUZZY = True
POLL_INTERVAL = 1.2
CLICK_COOLDOWN = 2.0
# 未命中时是否随机上滑（默认关，避免误滑干扰排查）
ENABLE_RANDOM_SWIPE = False
SWIPE_PROB = 0.12
# 各目标允许点击的区域（相对屏幕宽高 0~1）
# 「领取奖励」「继续观看」必须在屏幕中央；「领取成功」常见右上角
TARGET_ROI = {
    "领取奖励": {"x": (0.25, 0.75), "y": (0.35, 0.70)},  # 中央
    "继续观看": {"x": (0.25, 0.75), "y": (0.35, 0.70)},  # 中央
    "领取成功": {"x": (0.0, 1.0), "y": (0.01, 0.97)},    # 几乎全屏（含右上）
}
CENTER_TARGETS = {"领取奖励", "继续观看"}
# 截图过大时缩小再 OCR，加快速度（坐标会按比例还原）
OCR_MAX_WIDTH = 720
# 每次点击保存标注截图，方便排查误点
SAVE_CLICK_DEBUG = True
# ===========================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEBUG_DIR = os.path.join(SCRIPT_DIR, "debug_clicks")
ocr_engine = None
click_seq = 0


def get_ocr():
    global ocr_engine
    if ocr_engine is None:
        print("⏳ 加载 OCR 模型...")
        ocr_engine = RapidOCR()
        print("✅ OCR 就绪")
    return ocr_engine


def adb_command(cmd):
    return subprocess.run(["adb"] + cmd, capture_output=True, text=True)


def screenshot():
    result = subprocess.run(["adb", "exec-out", "screencap", "-p"], capture_output=True)
    if result.returncode != 0 or not result.stdout:
        return None
    img = cv2.imdecode(np.frombuffer(result.stdout, np.uint8), cv2.IMREAD_COLOR)
    return img


def normalize_text(s: str) -> str:
    if not s:
        return ""
    s = s.strip().replace(" ", "").replace("\u3000", "")
    # 去掉 OCR 常见尾巴符号（如「领取成功×」）
    s = re.sub(r"[×xX✕✖×·•\|丨\[\]【】()（）\d:：]+$", "", s)
    s = re.sub(r"^[×xX✕✖·•\|丨]+", "", s)
    # 常见 OCR 混淆
    s = s.replace("领収", "领取").replace("领职", "领取").replace("领敢", "领取")
    s = s.replace("奨", "奖").replace("奬", "奖")
    s = s.replace("勵", "励")
    return s


def text_match(recognized: str, target: str) -> bool:
    a = normalize_text(recognized)
    b = normalize_text(target)
    if not a:
        return False
    if a == b or b in a or a in b:
        return True
    if not FUZZY:
        return False
    # 允许个别错字：目标字出现比例够高
    hit = sum(1 for ch in b if ch in a)
    return hit >= max(2, len(b) - 1)


def box_center(box):
    """box: 4 点 [[x,y], ...] -> (cx, cy)"""
    pts = np.array(box, dtype=np.float32)
    cx = float(pts[:, 0].mean())
    cy = float(pts[:, 1].mean())
    return cx, cy


def in_roi(cx, cy, sw, sh, target: str):
    roi = TARGET_ROI.get(target, {"x": (0.0, 1.0), "y": (0.0, 1.0)})
    x0, x1 = roi["x"]
    y0, y1 = roi["y"]
    return x0 * sw <= cx <= x1 * sw and y0 * sh <= cy <= y1 * sh


def pos_pct(cx, cy, sw, sh):
    return cx / sw * 100.0, cy / sh * 100.0


def save_click_debug(screen, x, y, target, text, score, seq, ts):
    """保存带点击标记的截图，方便对照误点"""
    if not SAVE_CLICK_DEBUG:
        return None
    os.makedirs(DEBUG_DIR, exist_ok=True)
    vis = screen.copy()
    # 大红十字 + 圆，醒目
    cv2.drawMarker(vis, (x, y), (0, 0, 255), markerType=cv2.MARKER_CROSS, markerSize=60, thickness=3)
    cv2.circle(vis, (x, y), 28, (0, 0, 255), 3)
    cv2.circle(vis, (x, y), 6, (0, 255, 255), -1)
    # 画该目标 ROI 框
    sh, sw = screen.shape[:2]
    roi = TARGET_ROI.get(target, {"x": (0.0, 1.0), "y": (0.0, 1.0)})
    x0, y0 = int(roi["x"][0] * sw), int(roi["y"][0] * sh)
    x1, y1 = int(roi["x"][1] * sw), int(roi["y"][1] * sh)
    cv2.rectangle(vis, (x0, y0), (x1, y1), (0, 255, 0), 2)
    label = f"#{seq} {target} | OCR:{text} | {score:.3f} | ({x},{y})"
    cv2.rectangle(vis, (0, 0), (min(sw, 20 + 14 * len(label)), 48), (0, 0, 0), -1)
    cv2.putText(vis, label, (10, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
    safe_target = re.sub(r"[^\w\u4e00-\u9fff]+", "_", target)
    path = os.path.join(DEBUG_DIR, f"{ts}_{seq:04d}_{safe_target}_{x}_{y}.png")
    cv2.imwrite(path, vis)
    return path


def log_click(screen, target, text, score, x, y, candidates):
    """点击时打醒目日志 + 存调试图"""
    global click_seq
    click_seq += 1
    sh, sw = screen.shape[:2]
    px, py = pos_pct(x, y, sw, sh)
    roi = TARGET_ROI.get(target, {"x": (0.0, 1.0), "y": (0.0, 1.0)})
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    now = datetime.now().strftime("%H:%M:%S")

    print()
    print("=" * 60)
    print(f"  >>> 点击 #{click_seq}  [{now}]  <<<")
    print("=" * 60)
    print(f"  目标关键字 : 「{target}」")
    print(f"  OCR 原文   : 「{text}」")
    print(f"  归一化文本 : 「{normalize_text(text)}」")
    print(f"  置信度     : {score:.3f}  (阈值>={OCR_SCORE})")
    print(f"  点击坐标   : ({x}, {y})   屏幕={sw}x{sh}")
    print(f"  相对位置   : 水平 {px:.1f}%  垂直 {py:.1f}%  (左上=0%, 右下=100%)")
    print(f"  允许 ROI   : x={roi['x']}  y={roi['y']}")
    print(f"  候选数量   : {len(candidates)}")
    for i, (sc, cx, cy, t) in enumerate(candidates[:8], 1):
        mark = " <-- 实际点击" if i == 1 else ""
        cpx, cpy = pos_pct(cx, cy, sw, sh)
        print(
            f"    [{i}] 「{t}」 score={sc:.3f} "
            f"@({int(cx)},{int(cy)}) ~({cpx:.1f}%,{cpy:.1f}%){mark}"
        )
    path = save_click_debug(screen, x, y, target, text, score, click_seq, ts)
    if path:
        print(f"  调试截图   : {path}")
    print("=" * 60)
    print()


def run_ocr(screen):
    """返回 [(text, score, cx, cy), ...]，坐标为原图像素"""
    h, w = screen.shape[:2]
    scale = 1.0
    img = screen
    if w > OCR_MAX_WIDTH:
        scale = OCR_MAX_WIDTH / float(w)
        img = cv2.resize(screen, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

    result, _ = get_ocr()(img)
    items = []
    if not result:
        return items

    inv = 1.0 / scale
    for row in result:
        # row: [box, text, score]
        if not row or len(row) < 3:
            continue
        box, text, score = row[0], row[1], float(row[2])
        if score < OCR_SCORE:
            continue
        cx, cy = box_center(box)
        cx *= inv
        cy *= inv
        items.append((str(text), score, cx, cy))
    return items


def find_and_click(screen):
    """识别目标文字并点击。命中返回目标字符串，未命中返回 None"""
    sh, sw = screen.shape[:2]
    items = run_ocr(screen)

    if not items:
        print("· OCR 无结果")
        return None

    # 调试：打印本帧识别到的中文相关片段
    preview = [f"{t}({s:.2f})@({int(x)},{int(y)})" for t, s, x, y in items if re.search(r"[\u4e00-\u9fff]", t)]
    if preview:
        print("· 识别:", " | ".join(preview[:12]))

    for target in TARGETS:
        candidates = []
        for text, score, cx, cy in items:
            if not text_match(text, target):
                continue
            if not in_roi(cx, cy, sw, sh, target):
                px, py = pos_pct(cx, cy, sw, sh)
                print(
                    f"· 跳过非目标区域「{target}」: 「{text}」 "
                    f"@({int(cx)},{int(cy)}) ~({px:.1f}%,{py:.1f}%)"
                )
                continue
            candidates.append((score, cx, cy, text))

        if not candidates:
            continue

        if target in CENTER_TARGETS:
            # 中央目标：置信度优先，其次更靠屏幕正中
            mid_x, mid_y = sw * 0.5, sh * 0.5
            candidates.sort(
                key=lambda c: (-c[0], (c[1] - mid_x) ** 2 + (c[2] - mid_y) ** 2)
            )
        else:
            # 「领取成功」等：置信度优先；同分偏右上
            candidates.sort(key=lambda c: (-c[0], c[2], -c[1]))

        score, cx, cy, text = candidates[0]
        x, y = int(round(cx)), int(round(cy))
        log_click(screen, target, text, score, x, y, candidates)
        adb_command(["shell", "input", "tap", str(x), str(y)])
        return target

    print("· 未找到:", " / ".join(TARGETS))
    return None


# 点完这些目标后不进入冷却，立刻下一轮识别
NO_COOLDOWN_TARGETS = {"领取成功"}

print("🚀 汽水音乐自动看广告脚本启动（OCR 文字识别）")
print(f"   目标: {TARGETS}  置信度>={OCR_SCORE}  轮询={POLL_INTERVAL}s")
print(f"   点击调试图目录: {DEBUG_DIR}")
print(f"   无冷却目标: {sorted(NO_COOLDOWN_TARGETS)}")

while True:
    try:
        screen = screenshot()
        if screen is None:
            print("⚠️ 截图失败，检查 adb 连接")
            time.sleep(3)
            continue

        clicked = find_and_click(screen)
        if clicked:
            if clicked in NO_COOLDOWN_TARGETS:
                print(f"⚡ 「{clicked}」已点，跳过等待，立即继续识别")
                continue
            time.sleep(CLICK_COOLDOWN)
            continue

        if ENABLE_RANDOM_SWIPE and random.random() < SWIPE_PROB:
            h, w = screen.shape[:2]
            x = w // 2
            y1 = int(h * 0.55)
            y2 = int(h * 0.40)
            print(f"↕ 随机上滑 ({x},{y1}) -> ({x},{y2})")
            adb_command(["shell", "input", "swipe", str(x), str(y1), str(x), str(y2), "350"])

        time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\n已停止")
        break
    except Exception as e:
        print(f"⚠️ 错误: {e}")
        time.sleep(5)
