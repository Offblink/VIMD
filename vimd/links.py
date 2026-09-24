"""预览页链接/图片路径的纯逻辑（与 Qt 无关）。

- classify_navigation_url: 导航请求分类（预览/锚点/本地文件/外部链接）
- local_path_from_navigation: 导航 URL → 本地路径
- _fix_local_paths: 修正生成 HTML 中的本地路径引用
"""
import os
import re
from urllib.parse import urlparse, unquote


def classify_navigation_url(url_str, current_doc_url=""):
    """对预览页中的导航请求分类。

    返回:
        'preview' - 预览自身内容（空或 data:），放行
        'anchor'  - 同文档锚点跳转（#xxx），放行
        'file'    - 本地文件链接，交给系统默认程序打开
        'web'     - 外部链接（http/https/mailto 等），交给默认浏览器
    """
    if url_str == "" or url_str.startswith("data:"):
        return "preview"
    if "#" in url_str:
        base_part = url_str.split("#", 1)[0]
        if current_doc_url and base_part == current_doc_url.split("#", 1)[0]:
            return "anchor"
    scheme = url_str.split(":", 1)[0].lower() if ":" in url_str else ""
    if scheme == "file":
        return "file"
    if re.match(r'^[a-zA-Z]:[\\/]', url_str):
        # 原始 HTML 中形如 <a href="C:\x.md"> 的链接被 Chromium 误判为 scheme
        return "file"
    return "web"


def local_path_from_navigation(url_str):
    """把导航 URL 解析为本地文件路径（Windows）。"""
    if re.match(r'^[a-zA-Z]:[\\/]', url_str):
        return os.path.normpath(url_str)
    if url_str.lower().startswith("file:"):
        parsed = urlparse(url_str)
        path = unquote(parsed.path)
        if parsed.netloc and parsed.netloc.lower() not in ("", "localhost"):
            # UNC 路径 file://server/share/x
            return os.path.normpath("//" + parsed.netloc + path)
        if re.match(r'^/[a-zA-Z]:', path):
            path = path[1:]  # /C:/x → C:/x
        return os.path.normpath(path)
    return None


_LINK_ATTR_RE = re.compile(r'(<a\b[^>]*?\bhref=")([^"]*)(")', re.IGNORECASE)
_IMG_ATTR_RE = re.compile(r'(<img\b[^>]*?\bsrc=")([^"]*)(")', re.IGNORECASE)


def _fix_local_paths(html_content):
    r"""修正生成 HTML 中的本地路径引用。

    - C:\... 或 C:/... 形式的绝对路径 → file:///C:/...（否则被浏览器当成 scheme）
    - 相对路径中的反斜杠 → 正斜杠（交给 <base> 基准目录解析）
    - 网址（http/https 等）与已有 file: 前缀的路径保持原样
    """
    def fix_attrs(attr_re, content):
        def repl(m):
            prefix, value, quote = m.group(1), m.group(2), m.group(3)
            drive = re.match(r'^([a-zA-Z]):[\\/](.*)$', value)
            if drive:
                value = "file:///" + drive.group(1) + ":/" + drive.group(2).replace("\\", "/")
            elif "\\" in value and not re.match(r'^[a-zA-Z][a-zA-Z0-9+.\-]*:', value):
                value = value.replace("\\", "/")
            return prefix + value + quote
        return attr_re.sub(repl, content)
    return fix_attrs(_LINK_ATTR_RE, fix_attrs(_IMG_ATTR_RE, html_content))
