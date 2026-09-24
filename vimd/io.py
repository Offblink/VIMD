"""文件读写、文件锁与 HTML 导出。"""
import os

import markdown

# 读取时尝试的编码顺序
READ_ENCODINGS = ['utf-8', 'gbk', 'gb2312', 'gb18030', 'big5', 'latin-1']

EXPORT_HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>VIMD 导出</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
            line-height: 1.6;
            color: #24292e;
            background-color: #ffffff;
            padding: 20px;
            max-width: 800px;
            margin: 0 auto;
        }}
        h1, h2, h3, h4, h5, h6 {{
            margin-top: 24px;
            margin-bottom: 16px;
            font-weight: 600;
            line-height: 1.25;
        }}
        h1 {{ font-size: 2em; border-bottom: 1px solid #eaecef; padding-bottom: 0.3em; }}
        h2 {{ font-size: 1.5em; border-bottom: 1px solid #eaecef; padding-bottom: 0.3em; }}
        h3 {{ font-size: 1.25em; }}
        code {{
            background-color: rgba(27,31,35,0.05);
            border-radius: 3px;
            padding: 0.2em 0.4em;
            font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, Courier, monospace;
        }}
        pre {{
            background-color: #f6f8fa;
            border-radius: 3px;
            padding: 16px;
            overflow: auto;
        }}
        blockquote {{
            border-left: 4px solid #dfe2e5;
            padding-left: 16px;
            color: #6a737d;
            margin-left: 0;
        }}
        table {{
            border-collapse: collapse;
            width: 100%;
        }}
        th, td {{
            border: 1px solid #dfe2e5;
            padding: 6px 13px;
        }}
        th {{
            background-color: #f6f8fa;
        }}
        a {{ color: #0366d6; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        img {{ max-width: 100%; }}
        br {{
            display: block;
            content: "";
            margin-top: 0.5em;
        }}
    </style>
</head>
<body>
    {html_content}
</body>
</html>"""


def read_text_file(file_path):
    """读取文本文件，自动尝试多种编码；全部失败时以 UTF-8 忽略错误兜底。"""
    content = None
    for encoding in READ_ENCODINGS:
        try:
            with open(file_path, 'r', encoding=encoding) as file:
                content = file.read()
            break
        except UnicodeDecodeError:
            continue
    if content is None:
        with open(file_path, 'rb') as file:
            content = file.read().decode('utf-8', errors='ignore')
    return content


def write_text_file(file_path, text):
    """以 UTF-8 写入文本文件。"""
    with open(file_path, 'w', encoding='utf-8') as file:
        file.write(text)


class FileGuard:
    """打开期间锁住文件，让外部程序无法移动/删除/重命名它。

    原理：Windows 上只要有一份句柄没有授予 `FILE_SHARE_DELETE`，
    `MoveFile`/`DeleteFile`（也就是资源管理器的移动、改名、删除）就会
    以 ERROR_SHARING_VIOLATION 失败。这里刻意只授予 SHARE_READ|SHARE_WRITE：

      - 不给 SHARE_DELETE -> 移动/改名/删除被挡住
      - 给 SHARE_WRITE     -> 自己的「保存」（另开句柄写入）不受影响
      - 给 SHARE_READ      -> 别人仍可只读打开，不被误伤

    只读属性 / 独占程序（比如被 Word 独占的文件）导致拿不到句柄时，
    本类静默降级为"不加锁"，不打断打开文件的主流程。
    非 Windows 平台是空操作。
    """

    GENERIC_READ = 0x80000000
    SHARE_READ_WRITE = 0x00000001 | 0x00000002
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x80
    INVALID_HANDLE = -1

    def __init__(self):
        self._handle = None
        self._path = None
        self._create_file = None
        self._close_handle = None
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel32.CreateFileW.restype = wintypes.HANDLE
            kernel32.CreateFileW.argtypes = [
                wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
            ]
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            self._create_file = kernel32.CreateFileW
            self._close_handle = kernel32.CloseHandle

    def acquire(self, file_path):
        """锁住 file_path；先前锁住的文件自动释放。拿不到句柄就只记录路径。"""
        self.release()
        self._path = file_path
        if self._create_file is None:
            return
        handle = self._create_file(
            file_path, self.GENERIC_READ, self.SHARE_READ_WRITE,
            None, self.OPEN_EXISTING, self.FILE_ATTRIBUTE_NORMAL, None,
        )
        self._handle = None if handle == self.INVALID_HANDLE else handle

    def release(self):
        """释放锁（取消对文件的独占，之后可正常移动/删除/改名）。"""
        if self._handle is not None and self._close_handle is not None:
            self._close_handle(self._handle)
        self._handle = None
        self._path = None

    @property
    def path(self):
        return self._path


def render_export_html(markdown_text):
    """把 Markdown 渲染成完整 HTML 文档。"""
    html_content = markdown.markdown(
        markdown_text,
        extensions=['extra', 'codehilite', 'toc'],
    )
    return EXPORT_HTML_TEMPLATE.format(html_content=html_content)
