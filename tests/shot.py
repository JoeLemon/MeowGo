"""按窗口标题抓图：优先抓 MeowGo 主窗口，保存到指定路径。"""
import sys
import time

try:
    import pyautogui
except ImportError:
    raise SystemExit("需要 pyautogui")


def find_window(title_part="MeowGo"):
    try:
        import pygetwindow as gw
        wins = [w for w in gw.getAllWindows() if title_part.lower() in (w.title or "").lower()]
        if wins:
            return wins[0]
    except Exception:
        pass
    return None


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "tests/ui_shot.png"
    crop = sys.argv[2] if len(sys.argv) > 2 else None  # "x,y,w,h"
    time.sleep(1.0)
    win = find_window()
    if win is None:
        shot = pyautogui.screenshot()
    else:
        try:
            win.activate()
        except Exception:
            pass
        time.sleep(0.6)
        shot = pyautogui.screenshot(region=(win.left, win.top, win.width, win.height))
    if crop:
        x, y, w, h = [int(v) for v in crop.split(",")]
        shot = shot.crop((x, y, x + w, y + h))
    shot.save(out)
    print(f"saved {out} size={shot.size} win={'yes' if win else 'no'}")


if __name__ == "__main__":
    main()
