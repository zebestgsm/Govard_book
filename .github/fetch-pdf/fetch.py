#!/usr/bin/env python3
"""Скачивает PDF по ссылке и добавляет его в репозиторий.

Поддерживаются публичные ссылки Яндекс Диска, Google Drive, Dropbox,
Облака Mail.ru, OneDrive, а также прямые ссылки на файл.

Файлы до 95 МБ коммитятся прямо в репозиторий. Более крупные файлы
прикрепляются к релизу (GitHub не принимает в git файлы больше 100 МБ).

Параметры берутся из переменных окружения INPUT_URL / INPUT_NAME
(ручной запуск во вкладке Actions) или из файла request.txt рядом со скриптом.
"""
from __future__ import annotations

import base64
import email.message
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

REQUEST_FILE = pathlib.Path(__file__).with_name("request.txt")
MAX_GIT_BYTES = 95 * 1024 * 1024
RELEASE_TAG = "book"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


# ---------------------------------------------------------------- utilities
def log(message: str) -> None:
    print(message, flush=True)


def summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")


def fail(message: str) -> None:
    print(f"::error::{message}", flush=True)
    summary(f"### Не получилось\n\n{message}\n")
    sys.exit(1)


def human_size(num: int) -> str:
    return f"{num / (1024 * 1024):.1f} МБ"


def read_request() -> dict[str, str]:
    config: dict[str, str] = {}
    if REQUEST_FILE.exists():
        for raw in REQUEST_FILE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            config[key.strip().lower()] = value.strip()
    return config


def safe_name(name: str | None) -> str:
    name = (name or "").replace("\\", "/").split("/")[-1]
    name = re.sub(r'[\x00-\x1f<>:"|?*]', "_", name).strip(" .")
    if not name:
        name = "book"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return name


def is_pdf_name(name: str, mime: str = "") -> bool:
    return name.lower().endswith(".pdf") or mime == "application/pdf"


def run(*cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    log("$ " + " ".join(cmd))
    return subprocess.run(cmd, check=check)


# --------------------------------------------------------------------- http
def http_error_text(exc: urllib.error.HTTPError) -> str:
    if exc.code in (401, 403):
        return (f"нет доступа к файлу (HTTP {exc.code}). Откройте доступ "
                "«для всех, у кого есть ссылка».")
    if exc.code == 404:
        return "файл по ссылке не найден (HTTP 404). Проверьте ссылку."
    return f"сервер ответил ошибкой HTTP {exc.code}."


def get_json(url: str) -> dict:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def filename_from_headers(headers) -> str | None:
    disposition = headers.get("Content-Disposition")
    if not disposition:
        return None
    message = email.message.Message()
    message["content-disposition"] = disposition
    return message.get_filename()


def download(url: str, dest_dir: pathlib.Path,
             fallback_name: str | None = None) -> pathlib.Path:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        tmp = dest_dir / f".part-{attempt}"
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=120) as response:
                name = filename_from_headers(response.headers) or fallback_name
                if not name:
                    path = urllib.parse.urlparse(response.geturl()).path
                    name = urllib.parse.unquote(pathlib.PurePosixPath(path).name)
                with open(tmp, "wb") as fh:
                    shutil.copyfileobj(response, fh, length=1024 * 1024)
            target = dest_dir / safe_name(name)
            counter = 2
            while target.exists():
                target = dest_dir / f"{target.stem}-{counter}{target.suffix}"
                counter += 1
            tmp.replace(target)
            log(f"Скачано: {target.name} ({human_size(target.stat().st_size)})")
            return target
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code in (401, 403, 404):
                fail("Не удалось скачать: " + http_error_text(exc))
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last_error = exc
        log(f"Попытка {attempt} не удалась: {last_error}")
        time.sleep(5 * attempt)
    if isinstance(last_error, urllib.error.HTTPError):
        fail("Не удалось скачать: " + http_error_text(last_error))
    fail(f"Не удалось скачать файл: {last_error}")
    raise AssertionError  # unreachable


# ---------------------------------------------------------------- providers
def from_yandex(url: str, workdir: pathlib.Path) -> list[pathlib.Path]:
    api = "https://cloud-api.yandex.net/v1/disk/public/resources"
    try:
        meta = get_json(f"{api}?{urllib.parse.urlencode({'public_key': url, 'limit': 1000})}")
    except urllib.error.HTTPError as exc:
        fail("Яндекс Диск не отдал файл: " + http_error_text(exc))
    if meta.get("type") == "file":
        items = [meta]
    else:
        items = [item for item in meta.get("_embedded", {}).get("items", [])
                 if item.get("type") == "file"
                 and is_pdf_name(item.get("name", ""), item.get("mime_type", ""))]
        if not items:
            fail("В папке на Яндекс Диске нет PDF-файлов.")
    files = []
    for item in items:
        direct = item.get("file")
        if not direct:
            params = {"public_key": url}
            if meta.get("type") != "file":
                params["path"] = item["path"]
            direct = get_json(f"{api}/download?{urllib.parse.urlencode(params)}")["href"]
        files.append(download(direct, workdir, fallback_name=item.get("name")))
    return files


def from_google_drive(url: str, workdir: pathlib.Path) -> list[pathlib.Path]:
    import gdown  # installed by the workflow

    outdir = workdir / "gdrive"
    outdir.mkdir()
    try:
        if "/folders/" in url:
            gdown.download_folder(url, output=str(outdir), quiet=False)
        else:
            gdown.download(url, output=str(outdir) + os.sep, quiet=False, fuzzy=True)
    except Exception as exc:  # gdown raises many different exceptions
        log(f"gdown: {exc}")
    files = sorted(p for p in outdir.rglob("*") if p.is_file())
    if not files:
        fail("Google Drive не отдал файл. Проверьте, что доступ открыт: "
             "«Все, у кого есть ссылка».")
    return files


def from_dropbox(url: str, workdir: pathlib.Path) -> list[pathlib.Path]:
    parts = urllib.parse.urlparse(url)
    query = dict(urllib.parse.parse_qsl(parts.query))
    query["dl"] = "1"
    direct = urllib.parse.urlunparse(parts._replace(query=urllib.parse.urlencode(query)))
    return [download(direct, workdir)]


def from_mailru(url: str, workdir: pathlib.Path) -> list[pathlib.Path]:
    match = re.search(r"/public/(.+)", urllib.parse.urlparse(url).path)
    if not match:
        fail("Нужна публичная ссылка на Облако Mail.ru вида https://cloud.mail.ru/public/...")
    weblink = match.group(1).strip("/")
    base = None
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        page = urllib.request.urlopen(request, timeout=60).read().decode("utf-8", "replace")
        found = re.search(r'"weblink_get"\s*:\s*\[?\s*\{[^}]*?"url"\s*:\s*"([^"]+)"', page)
        if found:
            base = found.group(1).replace("\\/", "/")
    except Exception as exc:
        log(f"Не удалось прочитать страницу Облака: {exc}")
    if not base:
        try:
            base = get_json("https://cloud.mail.ru/api/v2/dispatcher")["body"]["weblink_get"][0]["url"]
        except Exception as exc:
            fail(f"Облако Mail.ru не отдало файл: {exc}")
    return [download(f"{base.rstrip('/')}/{weblink}", workdir)]


def from_onedrive(url: str, workdir: pathlib.Path) -> list[pathlib.Path]:
    token = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
    return [download(f"https://api.onedrive.com/v1.0/shares/u!{token}/root/content", workdir)]


def fetch(url: str, workdir: pathlib.Path) -> list[pathlib.Path]:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host == "yadi.sk" or ("yandex." in host and "disk" in host):
        log("Источник: Яндекс Диск")
        return from_yandex(url, workdir)
    if host in ("drive.google.com", "docs.google.com"):
        log("Источник: Google Drive")
        return from_google_drive(url, workdir)
    if host.endswith("dropbox.com"):
        log("Источник: Dropbox")
        return from_dropbox(url, workdir)
    if host == "cloud.mail.ru":
        log("Источник: Облако Mail.ru")
        return from_mailru(url, workdir)
    if host in ("1drv.ms", "onedrive.live.com"):
        log("Источник: OneDrive")
        return from_onedrive(url, workdir)
    log("Источник: прямая ссылка")
    return [download(url, workdir)]


def check_pdf(path: pathlib.Path) -> None:
    with open(path, "rb") as fh:
        head = fh.read(2048)
    if b"%PDF-" in head[:1024]:
        return
    lowered = head.lower()
    if b"<html" in lowered or b"<!doctype" in lowered:
        fail("По ссылке открылась веб-страница, а не PDF-файл. Проверьте, что доступ "
             "к файлу открыт «для всех, у кого есть ссылка».")
    fail(f"Файл «{path.name}» не похож на PDF.")


# --------------------------------------------------------------------- main
def main() -> None:
    config = read_request()
    url_from_input = os.environ.get("INPUT_URL", "").strip()
    url = url_from_input or config.get("url", "")
    if not url:
        log("Ссылка не указана — скачивать нечего.")
        summary("Ссылка не указана — скачивать нечего.")
        return
    custom_name = os.environ.get("INPUT_NAME", "").strip() or config.get("name", "")
    target_dir = pathlib.Path(config.get("dir", "").strip().strip("/") or ".")
    dry_run = config.get("dry_run", "").lower() in ("1", "true", "yes", "да")

    workdir = pathlib.Path(tempfile.mkdtemp(prefix="pdf-"))
    files = fetch(url, workdir)
    for path in files:
        check_pdf(path)
    if custom_name and len(files) == 1:
        renamed = files[0].with_name(safe_name(custom_name))
        files[0].rename(renamed)
        files = [renamed]

    lines = []
    if dry_run:
        for path in files:
            lines.append(f"- `{path.name}` — {human_size(path.stat().st_size)} (проверка, без сохранения)")
        summary("### Проверка ссылки прошла успешно\n\n" + "\n".join(lines))
        log("\n".join(lines))
        return

    branch = os.environ.get("GITHUB_REF_NAME", "main")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    committed = []
    for path in files:
        size = path.stat().st_size
        if size <= MAX_GIT_BYTES:
            target_dir.mkdir(parents=True, exist_ok=True)
            destination = target_dir / path.name
            shutil.copyfile(path, destination)
            run("git", "add", "--", str(destination))
            committed.append(destination)
            lines.append(f"- `{destination.as_posix()}` — {human_size(size)}, добавлен в репозиторий")
        else:
            if run("gh", "release", "view", RELEASE_TAG, check=False).returncode != 0:
                run("gh", "release", "create", RELEASE_TAG, "--target", branch,
                    "--title", "Книга (PDF)", "--notes", "PDF-файл книги.")
            run("gh", "release", "upload", RELEASE_TAG, str(path), "--clobber")
            lines.append(f"- `{path.name}` — {human_size(size)}, прикреплён к релизу: "
                         f"{server}/{repo}/releases/tag/{RELEASE_TAG}")

    if not url_from_input and REQUEST_FILE.exists():
        # Убираем ссылку из заявки, чтобы она не оставалась в репозитории.
        text = REQUEST_FILE.read_text(encoding="utf-8")
        cleaned = re.sub(r"(?m)^(\s*url\s*=).*$", r"\1", text)
        if cleaned != text:
            REQUEST_FILE.write_text(cleaned, encoding="utf-8")
            run("git", "add", "--", str(REQUEST_FILE))

    if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode != 0:
        run("git", "config", "user.name", "github-actions[bot]")
        run("git", "config", "user.email",
            "41898333+github-actions[bot]@users.noreply.github.com")
        names = ", ".join(p.name for p in committed) or "ссылка обработана"
        run("git", "commit", "-m", f"Добавлен PDF: {names}")
        run("git", "pull", "--rebase", "origin", branch)
        run("git", "push", "origin", f"HEAD:{branch}")

    summary("### Готово\n\n" + "\n".join(lines))
    log("\n".join(lines))


if __name__ == "__main__":
    main()
