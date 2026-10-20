#!/usr/bin/env python3
r"""issue_form.py —— 解析「转码表」Issue 表单，供 GitHub Actions 使用。

Issue 表单（.github/ISSUE_TEMPLATE/custom-schema.md）提交后，正文是
「### 标题 + 内容」分段。本工具：

1. 按标题解析出 方案名/显示名/双拼布局；
2. 在「码表文件」段找 GitHub 附件链接（拖进表单的文件），经 host 白名单
   + DNS 解析后 IP 非内网校验 + 重定向逐跳同校验下载到本地；没有附件时
   把该段粘贴的正文写成码表文件（要求行内含 Tab 的 码<Tab>字 结构）；
3. 把 TABLE_PATH / SCHEME_NAME / DISPLAY_NAME / LAYOUT /
   WORD_TABLE_PATH / KEEP_SHORTCODES 输出为 shell 赋值行（调用方
   >> $GITHUB_ENV）。

用法：
    uv run tools/custom/issue_form.py <body.txt> <工作目录>
"""

from __future__ import annotations

import ipaddress
import re
import socket
import sys
import urllib.parse
import urllib.request
from pathlib import Path

# 附件初始链接在 github.com，302 跳转到 objects.githubusercontent.com。
# github.com 限定附件路径前缀；objects 是签名 CDN，重定向路径不保证
# 带 /user-attachments/ 前缀（files 通道会跳 github-production-* 路径），
# 故只按 host 白名单放行，IP 校验仍在。
ALLOWED_HOSTS = {
    "github.com": "/user-attachments/",
    "objects.githubusercontent.com": "",
}
# Issue 拖拽文件有两种链接形态：assets/<id>（旧）与 files/<id>/<文件名>（新）。
ATTACHMENT_URL = re.compile(
    r"https://github\.com/user-attachments/"
    r"(?:assets/[A-Za-z0-9_-]+|files/\d+/[^)\s]+)"
)

LAYOUT_OPTIONS = {
    "自动识别": "",
    "zrm（自然码）": "zrm",
    "zrm": "zrm",
    "自然码": "zrm",
    "flypy（小鹤）": "flypy",
    "flypy": "flypy",
    "小鹤": "flypy",
    "jiajia（加加）": "jiajia",
    "jiajia": "jiajia",
    "加加": "jiajia",
}


class FormError(ValueError):
    """表单不合法；str(e) 面向用户（会贴进 Issue 评论）。"""


def assert_safe_url(url: str) -> None:
    """校验 URL：https、host 白名单、路径前缀、解析 IP 非内网。"""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https":
        raise FormError(f"附件链接不安全（非 https），已拒绝下载：{url}")
    host = parsed.hostname or ""
    prefix = ALLOWED_HOSTS.get(host.lower())
    if prefix is None:
        raise FormError(f"附件链接域名不在白名单内，已拒绝下载：{url}")
    if not (parsed.path or "").startswith(prefix):
        raise FormError(f"附件链接不是 GitHub 附件路径，已拒绝下载：{url}")
    try:
        infos = socket.getaddrinfo(host, 443)
    except OSError as exc:
        raise FormError(f"附件域名无法解析（{host}）：{exc}") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_reserved
                or ip.is_link_local or ip.is_multicast or ip.is_unspecified):
            raise FormError(f"附件域名解析到内网/保留地址（{host} → {ip}），已拒绝下载")


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """重定向逐跳走同一套安全校验。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        assert_safe_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def parse_sections(body: str) -> dict[str, str]:
    """把 Issue 正文按 ### 标题切段。"""
    sections: dict[str, str] = {}
    current: str | None = None
    parts: list[str] = []
    for line in body.splitlines():
        match = re.match(r"^###\s+(.+?)\s*$", line)
        if match:
            if current is not None:
                sections[current] = "\n".join(parts).strip()
            current, parts = match.group(1), []
        elif current is not None:
            parts.append(line)
    if current is not None:
        sections[current] = "\n".join(parts).strip()
    return sections


def fetch_attachment(url: str, target_dir: Path, index: int) -> Path:
    """下载 GitHub 附件；返回本地路径。"""
    assert_safe_url(url)
    opener = urllib.request.build_opener(_SafeRedirectHandler())
    suffix = Path(urllib.parse.urlsplit(url).path).suffix or ""
    target = target_dir / f"table-{index}{suffix}"
    try:
        with opener.open(url, timeout=180) as resp, target.open("wb") as out:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)
    except urllib.error.URLError as exc:
        raise FormError(
            f"附件下载失败（{url}）：{exc}\n"
            f"请编辑这个 Issue 重新拖一次码表文件（会生成新的附件链接）后保存。"
        ) from None
    return target


def resolve_file_section(
    section_text: str, body: str, workdir: Path, name: str, index: int,
) -> Path:
    """把表单某一段落解析成本地文件：段内附件优先，其次全文第 index 个
    附件（拖错框时的宽容），最后尝试粘贴文本。"""
    section_urls = ATTACHMENT_URL.findall(section_text)
    if section_urls:
        return fetch_attachment(section_urls[0], workdir, index)
    all_urls = ATTACHMENT_URL.findall(body)
    if len(all_urls) >= index:
        return fetch_attachment(all_urls[index - 1], workdir, index)
    pasted = "\n".join(
        line for line in section_text.splitlines()
        if not line.startswith("<!--") and not line.startswith("[") and "\t" in line
    )
    if not pasted:
        raise FormError(
            f"「{name}」框里没有看到附件，也没有可识别的文本。\n"
            f"请重新编辑这个 Issue：把文件直接拖进该框（会自动变成附件链接）；"
            f"内容不大时也可以整段粘贴，每行需含 Tab 分隔。"
        )
    target = workdir / f"pasted-{index}.txt"
    target.write_text(pasted + "\n", encoding="utf-8")
    return target


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    body = Path(sys.argv[1]).read_text(encoding="utf-8")
    workdir = Path(sys.argv[2])
    workdir.mkdir(parents=True, exist_ok=True)
    sections = parse_sections(body)

    def field(label: str, default: str = "") -> str:
        return sections.get(label, "").strip() or default

    table_section = field("码表文件")
    table_path = resolve_file_section(table_section, body, workdir, "码表文件", 1)

    scheme_name = field("方案名")
    if not re.fullmatch(r"[a-z][a-z0-9]*", scheme_name):
        raise FormError(
            f"方案名「{scheme_name}」不合法：需要小写英文字母开头、仅小写字母与数字"
            f"（如 mycode），请编辑 Issue 修正后重跑。"
        )
    display_name = field("显示名")
    layout_label = field("双拼布局", "自动识别")
    try:
        layout = LAYOUT_OPTIONS[layout_label]
    except KeyError:
        layout = ""

    word_source = field("词库来源", "使用魔虎词库")
    word_table_path = ""
    if word_source == "使用码表自带词库":
        word_section = field("词库文件")
        if ATTACHMENT_URL.search(word_section) or "\t" in word_section:
            word_table_path = str(resolve_file_section(
                word_section, body, workdir, "词库文件", 2))
        else:
            # 词库文件留空：词条从码表文件本身提取（字词混排的码表）
            word_table_path = str(table_path)
    keep_shortcodes = "true" if field("简码来源", "魔虎推算") == "码表自带" else "false"

    print(f"TABLE_PATH={table_path}")
    print(f"SCHEME_NAME={scheme_name}")
    print(f"DISPLAY_NAME={display_name}")
    print(f"LAYOUT={layout}")
    print(f"WORD_TABLE_PATH={word_table_path}")
    print(f"KEEP_SHORTCODES={keep_shortcodes}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FormError as exc:
        print(f"表单解析失败：{exc}", file=sys.stderr)
        raise SystemExit(3)
