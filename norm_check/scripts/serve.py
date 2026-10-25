# -*- coding: utf-8 -*-
"""Этап 7. Одна локальная страница: файл, запуск, ожидание, скачивание PDF.

Правила не переписывает. Кладёт выбранные файлы в uploads/<задание>/
и вызывает уже закрытые скрипты так, чтобы reports/stage2…stage6 не менялись.
Скачивается отчёт пакета из задания, не reports/stage6/report.pdf.
Кабинета и оплаты нет. Слушает только 127.0.0.1.

Запуск из корня norm_check:
  .venv\\Scripts\\python.exe scripts\\serve.py
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import threading
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import build_package_report
import build_report
import check_package
import check_sp_title
import finding_card
import read_dwg
import review_conclusions

ROOT = Path(__file__).resolve().parents[1]
UPLOADS = ROOT / "uploads"
HOST = "127.0.0.1"
PORT = 8765
MAX_BODY = 200 * 1024 * 1024
ALLOWED = {".pdf", ".dwg"}

jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()
pipeline_lock = threading.Lock()

PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Нормоконтроль</title>
<style>
  body { font-family: Arial, sans-serif; margin: 0; color: #1a1a1a; background: #f6f5f2; }
  main { max-width: 40rem; margin: 0 auto; padding: 1.5rem 1rem 3rem; }
  h1 { font-size: 1.6rem; margin: 0 0 0.4rem; }
  p { line-height: 1.45; }
  .note { color: #444; font-size: 0.95rem; }
  form { margin: 1.4rem 0; padding: 1rem; background: #fff; border: 1px solid #ddd; }
  input[type=file] { display: block; margin: 0.6rem 0 1rem; max-width: 100%; }
  button { font: inherit; padding: 0.45rem 0.9rem; }
  button:disabled { opacity: 0.6; }
  #status { min-height: 1.4rem; font-weight: bold; }
  a.download { display: inline-block; margin-top: 0.8rem; }
</style>
</head>
<body>
<main>
  <h1>Нормоконтроль</h1>
  <p class="note">Это не заключение экспертизы проектной документации. Кабинета и оплаты нет. Проверяются правила пакета: название при шифре СП, имя листа в ведомости, один шифр, строка ведомости и штамп, неположительное значение площади и нагрузки помещения, наружный диаметр на 0,01 мм от дюймового ряда, номер помещения, подпись длины участка.</p>
  <form id="form">
    <label for="files">PDF и, если есть, DWG того же комплекта</label>
    <input id="files" name="files" type="file" accept=".pdf,.dwg,application/pdf" multiple required>
    <button id="go" type="submit">Проверить</button>
  </form>
  <p id="status"></p>
  <p id="result"></p>
</main>
<script>
const form = document.getElementById("form");
const status = document.getElementById("status");
const result = document.getElementById("result");
const button = document.getElementById("go");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = document.getElementById("files");
  if (!input.files.length) {
    status.textContent = "Выберите файл.";
    return;
  }
  button.disabled = true;
  result.textContent = "";
  status.textContent = "Идёт проверка. Это может занять несколько минут.";
  const body = new FormData();
  for (const file of input.files) body.append("files", file, file.name);
  let job;
  try {
    const response = await fetch("/api/jobs", { method: "POST", body });
    job = await response.json();
    if (!response.ok) throw new Error(job.error || "Сервер не принял файл");
  } catch (error) {
    status.textContent = error.message;
    button.disabled = false;
    return;
  }
  const timer = setInterval(async () => {
    try {
      const response = await fetch("/api/jobs/" + job.id);
      const row = await response.json();
      if (row.status === "running" || row.status === "queued") return;
      clearInterval(timer);
      button.disabled = false;
      if (row.status === "done") {
        status.textContent = "Проверка закончена.";
        result.innerHTML = '<a class="download" href="/api/jobs/' + job.id + '/report.pdf" download="report.pdf">Скачать отчёт PDF</a>';
        return;
      }
      status.textContent = row.error || "Проверка не собрала отчёт.";
    } catch (error) {
      clearInterval(timer);
      button.disabled = false;
      status.textContent = "Нет ответа от страницы. Запустите её снова.";
    }
  }, 1000);
});
</script>
</body>
</html>
"""


class JobError(Exception):
    pass


def safe_name(name: str) -> str:
    base = Path(name).name
    if not base or base != name or base.startswith("."):
        raise JobError("Имя файла не подходит")
    if Path(base).suffix.lower() not in ALLOWED:
        raise JobError("Нужен PDF или DWG")
    return base


def filename_from_part(header: str) -> str:
    starred = re.search(r"filename\*=UTF-8''([^;\r\n]+)", header, re.IGNORECASE)
    if starred:
        return unquote(starred.group(1))
    quoted = re.search(r'filename="([^"]*)"', header)
    if quoted:
        return quoted.group(1)
    plain = re.search(r"filename=([^;\r\n]+)", header)
    if plain:
        return plain.group(1).strip()
    raise JobError("В загрузке нет имени файла")


def parse_files(body: bytes, content_type: str) -> list[tuple[str, bytes]]:
    match = re.search(r"boundary=([^;]+)", content_type or "", re.IGNORECASE)
    if not match:
        raise JobError("Ожидалась форма с файлом")
    boundary = match.group(1).strip().strip('"').encode("utf-8")
    found = []
    for part in body.split(b"--" + boundary):
        if not part or part in (b"--", b"--\r\n") or part.startswith(b"--"):
            continue
        head, sep, data = part.partition(b"\r\n\r\n")
        if not sep:
            continue
        if data.endswith(b"\r\n"):
            data = data[:-2]
        header = head.decode("utf-8", "replace")
        if "filename=" not in header.lower():
            continue
        found.append((filename_from_part(header), data))
    if not found:
        raise JobError("Выберите файл")
    return found


def sibling_dwgs(pdf_bytes: bytes) -> list[Path]:
    """DWG рядом с тем же PDF в inbox. Шифр и имя файла в правило не входят."""
    digest = hashlib.sha256(pdf_bytes).digest()
    inbox = ROOT / "inbox"
    if not inbox.is_dir():
        return []
    for pdf in sorted(inbox.glob("*.pdf")):
        if hashlib.sha256(pdf.read_bytes()).digest() == digest:
            return sorted(path for path in inbox.glob("*.dwg") if path.is_file())
    return []


def save_upload(job_id: str, files: list[tuple[str, bytes]]) -> Path:
    inbox = UPLOADS / job_id / "in"
    inbox.mkdir(parents=True)
    seen: set[str] = set()
    pdfs: list[tuple[str, bytes]] = []
    has_dwg = False
    for raw_name, data in files:
        name = safe_name(raw_name)
        if name in seen:
            raise JobError("Два файла с одним именем")
        seen.add(name)
        if not data:
            raise JobError("Пустой файл")
        (inbox / name).write_bytes(data)
        if name.lower().endswith(".pdf"):
            pdfs.append((name, data))
        else:
            has_dwg = True
    if len(pdfs) != 1:
        raise JobError("Нужен один PDF: правило читает страницу и вырезает участок")
    if not has_dwg:
        for path in sibling_dwgs(pdfs[0][1]):
            target = inbox / path.name
            if target.exists():
                continue
            target.write_bytes(path.read_bytes())
    return inbox


def bind(module, name: str, value: Path, saved: list) -> None:
    saved.append((module, name, getattr(module, name)))
    setattr(module, name, value)


def run_pipeline(job_id: str) -> None:
    job = UPLOADS / job_id
    inbox = job / "in"
    lines = job / "lines"
    stage4 = job / "stage4"
    stage5 = job / "stage5"
    stage6 = job / "stage6"
    stage8 = job / "stage8"
    lines.mkdir(parents=True, exist_ok=True)
    saved: list = []
    old_argv = sys.argv
    try:
        bind(read_dwg, "INBOX", inbox, saved)
        bind(read_dwg, "OUT_DIR", lines, saved)
        bind(check_sp_title, "INBOX", inbox, saved)
        bind(check_sp_title, "LINES_DIR", lines, saved)
        bind(check_sp_title, "OUT_DIR", stage4, saved)
        bind(finding_card, "INBOX", inbox, saved)
        bind(finding_card, "LINES_DIR", lines, saved)
        bind(finding_card, "OUT_DIR", stage5, saved)
        bind(review_conclusions, "INBOX", inbox, saved)
        bind(review_conclusions, "LINES_DIR", lines, saved)
        bind(review_conclusions, "CARD_PATH", stage5 / "cards.json", saved)
        bind(review_conclusions, "OUT_PATH", stage5 / "review.json", saved)
        bind(build_report, "CARD_PATH", stage5 / "cards.json", saved)
        bind(build_report, "CARD_REVIEW", stage5 / "review.json", saved)
        bind(build_report, "OUT_DIR", stage6, saved)
        bind(build_report, "PDF_PATH", stage6 / "report.pdf", saved)
        bind(check_package, "INBOX", inbox, saved)
        bind(check_package, "LINES_DIR", lines, saved)
        bind(check_package, "OUT_DIR", stage8, saved)
        bind(build_package_report, "INBOX", inbox, saved)
        bind(build_package_report, "CARD_PATH", stage5 / "cards.json", saved)
        bind(build_package_report, "CARD_REVIEW", stage5 / "review.json", saved)
        bind(build_package_report, "PACKAGE_PATH", stage8 / "package.json", saved)
        bind(build_package_report, "OUT_DIR", stage8, saved)
        bind(build_package_report, "PDF_PATH", stage8 / "report.pdf", saved)
        if any(inbox.glob("*.dwg")):
            sys.argv = ["read_dwg.py"]
            read_dwg.main()
        check_sp_title.main()
        finding_card.main()
        payload = json.loads((stage5 / "cards.json").read_text(encoding="utf-8"))
        cards = payload.get("cards") or []
        if len(cards) > 1:
            raise JobError(
                f"Правило собрало карточек: {len(cards)}. "
                "Отчёт с замечанием собирается по одной карточке."
            )
        if len(cards) == 1:
            if review_conclusions.main() != 0:
                raise JobError("Внутренняя сверка карточки не прошла, отчёт не собран")
            if build_report.main() != 0:
                raise JobError("Внутренняя сверка отчёта не прошла")
        if check_package.main() != 0:
            raise JobError("Пакет правил не сошёлся")
        if build_package_report.main() != 0:
            raise JobError("Внутренняя сверка отчёта не прошла")
        report = stage8 / "report.pdf"
        if not report.is_file():
            raise JobError("PDF отчёта не записан")
    finally:
        sys.argv = old_argv
        for module, name, value in reversed(saved):
            setattr(module, name, value)


def work(job_id: str) -> None:
    with jobs_lock:
        jobs[job_id]["status"] = "running"
    try:
        with pipeline_lock:
            run_pipeline(job_id)
    except JobError as error:
        with jobs_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = str(error)
        return
    except SystemExit as error:
        text = str(error) if str(error) else "Проверка остановилась"
        with jobs_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = text
        return
    except Exception:
        traceback.print_exc()
        with jobs_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = "Проверка остановилась с ошибкой. Смотрите окно, где запущена страница."
        return
    with jobs_lock:
        jobs[job_id]["status"] = "done"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} {fmt % args}")

    def send_json(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            raw = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        job_match = re.fullmatch(r"/api/jobs/([0-9a-f]{32})", path)
        if job_match:
            job_id = job_match.group(1)
            with jobs_lock:
                row = jobs.get(job_id)
            if row is None:
                self.send_json(404, {"error": "Задание не найдено"})
                return
            self.send_json(200, {"id": job_id, "status": row["status"], "error": row.get("error")})
            return
        file_match = re.fullmatch(r"/api/jobs/([0-9a-f]{32})/report\.pdf", path)
        if file_match:
            job_id = file_match.group(1)
            with jobs_lock:
                row = jobs.get(job_id)
            report = UPLOADS / job_id / "stage8" / "report.pdf"
            if row is None or row.get("status") != "done" or not report.is_file():
                self.send_json(404, {"error": "Отчёт ещё не готов"})
                return
            data = report.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Disposition", 'attachment; filename="report.pdf"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_json(404, {"error": "Нет такой страницы"})

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/jobs":
            self.send_json(404, {"error": "Нет такой страницы"})
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0 or length > MAX_BODY:
            self.send_json(413, {"error": "Файл слишком большой или пустой"})
            return
        body = self.rfile.read(length)
        job_id = uuid.uuid4().hex
        try:
            files = parse_files(body, self.headers.get("Content-Type", ""))
            save_upload(job_id, files)
        except JobError as error:
            self.send_json(400, {"error": str(error)})
            return
        with jobs_lock:
            jobs[job_id] = {"status": "queued"}
        threading.Thread(target=work, args=(job_id,), daemon=True).start()
        self.send_json(200, {"id": job_id, "status": "queued"})


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    UPLOADS.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, port), Handler)
    print(f"http://{HOST}:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
