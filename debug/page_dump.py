"""Дамп HTML страниц для отладки селекторов."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from loguru import logger
from selenium.webdriver.remote.webdriver import WebDriver

from config import DEBUG_PAGES_DIR


def _slug(text: str, *, max_len: int = 80) -> str:
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[^a-z0-9а-я]+", "_", text, flags=re.IGNORECASE)
    text = re.sub(r"_+", "_", text).strip("_")
    return (text or "page")[:max_len]


class PageDumper:
    """В debug-режиме сохраняет page_source после каждого перехода."""

    def __init__(self, root: Path | None = None, *, run_id: str | None = None) -> None:
        stamp = run_id or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.run_dir = (root or DEBUG_PAGES_DIR) / stamp
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._seq = 0
        self.index_path = self.run_dir / "index.tsv"
        if not self.index_path.exists():
            self.index_path.write_text(
                "seq\tchat_id\tlabel\turl\thtml_file\n", encoding="utf-8"
            )
        logger.debug("PageDumper run_dir={}", self.run_dir)

    def save(
        self,
        driver: WebDriver,
        *,
        label: str = "",
        url: str | None = None,
        chat_id: str | None = None,
    ) -> Path:
        self._seq += 1
        current_url = url or getattr(driver, "current_url", "") or ""
        # chat_id из аргумента или из /chat/<id> в URL
        cid = (chat_id or "").strip() or _chat_id_from_url(current_url)
        path_part = urlparse(current_url).path.strip("/") or "root"
        base_label = label or path_part
        if cid and cid not in base_label:
            base_label = f"chat_{cid}_{base_label}"
        elif cid and not base_label.startswith("chat_"):
            base_label = f"chat_{cid}_{base_label}"
        name = f"{self._seq:04d}_{_slug(base_label)}.html"
        html_path = self.run_dir / name

        html = driver.page_source or ""
        stamp = datetime.now(timezone.utc).isoformat()
        header = (
            f"<!-- hh_autoapply dump\n"
            f"     chat_id: {cid or '—'}\n"
            f"     label: {label}\n"
            f"     url: {current_url}\n"
            f"     saved_at: {stamp}\n"
            f"-->\n"
        )
        html_path.write_text(header + html, encoding="utf-8")
        logger.info(
            "HTML dump chat_id={} label={!r} → {}",
            cid or "—",
            label,
            html_path,
        )

        meta_path = html_path.with_suffix(".meta.txt")
        meta_path.write_text(
            f"chat_id: {cid or ''}\n"
            f"url: {current_url}\n"
            f"label: {label}\n"
            f"title: {getattr(driver, 'title', '')}\n"
            f"saved_at: {stamp}\n",
            encoding="utf-8",
        )

        with self.index_path.open("a", encoding="utf-8") as fh:
            fh.write(f"{self._seq}\t{cid or ''}\t{label}\t{current_url}\t{name}\n")

        return html_path


def _chat_id_from_url(url: str) -> str | None:
    m = re.search(r"/chat/(\d+)", url or "")
    return m.group(1) if m else None
