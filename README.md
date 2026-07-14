# 汽水音乐自动看广告

适用 app 版本：`19.1.0`

## 安装

1. 安装 Python 3.9+，并安装 ADB（加入 PATH）
2. 手机开启 USB 调试，连接电脑，确认：

```bash
adb devices
```

3. 安装依赖：

```bash
cd qishui
pip install -r requirements.txt
```

## 使用

1. 手机打开汽水音乐，进入看广告相关页面
2. 启动脚本：

```bash
python qishui_auto.py
```

3. 停止：终端按 `Ctrl + C`
