@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -c "import pyautogui, PIL, xlrd, openpyxl, pyperclip, cv2" 2>nul
if errorlevel 1 (
    echo 检测到缺少依赖，正在自动安装...
    python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
)
start "" pythonw "start-app.py"
