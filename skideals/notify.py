"""Windows 桌面通知（不需要装任何库：直接调用系统自带的 PowerShell + Windows 通知 API）。"""
from __future__ import annotations

import base64
import subprocess
import sys
from xml.sax.saxutils import escape

# 借用 PowerShell 自己的 AppUserModelID，这样不用注册应用也能弹通知
_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"


def toast(title: str, message: str) -> bool:
    if sys.platform != "win32":
        return False
    xml = (f"<toast><visual><binding template='ToastGeneric'><text>{escape(title)}</text>"
           f"<text>{escape(message)}</text></binding></visual></toast>").replace('"', "&quot;")
    script = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml("{xml}")
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{_APP_ID}').Show($toast)
"""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                       timeout=20, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True
    except Exception:
        return False
