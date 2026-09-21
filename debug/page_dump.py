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
                "seq\tlabel\turl\thtml_file\n", encoding="utf-8"
            )
        logger.debug("PageDumper run_dir={}", self.run_dir)

    def save(
        self,
        driver: WebDriver,
        *,
        label: str = "",
        url: str | None = None,
    ) -> Path:
        self._seq += 1
        current_url = url or getattr(driver, "current_url", "") or ""
        path_part = urlparse(current_url).path.strip("/") or "root"
        name = f"{self._seq:04d}_{_slug(label or path_part)}.html"
        html_path = self.run_dir / name

        html = driver.page_source or ""
        html_path.write_text(html, encoding="utf-8")
        logger.debug(
            "Сохранён HTML seq={} label={!r} bytes={} → {}",
            self._seq,
            label,
            len(html),
            html_path.name,
        )

        meta_path = html_path.with_suffix(".meta.txt")
        meta_path.write_text(
            f"url: {current_url}\n"
            f"label: {label}\n"
            f"title: {getattr(driver, 'title', '')}\n"
            f"saved_at: {datetime.now(timezone.utc).isoformat()}\n",
            encoding="utf-8",
        )

        with self.index_path.open("a", encoding="utf-8") as fh:
            fh.write(f"{self._seq}\t{label}\t{current_url}\t{name}\n")

        return html_path
