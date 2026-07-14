@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 汽水音乐自动看广告

echo ========================================
echo   汽水音乐自动看广告
echo ========================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    where py >nul 2>&1
    if errorlevel 1 (
        echo [错误] 未找到 Python，请先安装 Python 3.9+ 并勾选 Add to PATH
        pause
        exit /b 1
    )
    set "PY=py -3"
) else (
    set "PY=python"
)

where adb >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 ADB，请安装 platform-tools 并加入 PATH
    pause
    exit /b 1
)

echo [检查] ADB 设备...
adb devices
echo.

%PY% -c "import cv2, numpy, rapidocr_onnxruntime" >nul 2>&1
if errorlevel 1 (
    echo [安装] 缺少依赖，正在 pip install -r requirements.txt ...
    %PY% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [错误] 依赖安装失败
        pause
        exit /b 1
    )
    echo.
)

echo [启动] qishui_auto.py
echo 停止请按 Ctrl + C
echo.
%PY% qishui_auto.py

echo.
echo 脚本已退出。
pause
