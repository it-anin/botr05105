"""แจ้งผลแต่ละรอบเข้า Telegram (บอทตัวเดียวกับ EDC MONITOR)

ส่งทุกรอบทั้งสำเร็จและพัง - วันไหนไม่มีข้อความเลยแปลว่าบอทไม่ได้รัน
(เครื่องปิด / Task Scheduler ไม่ทำงาน) ซึ่งเป็นการพังเงียบอีกแบบที่ log ไม่มีทางบอก

token กับ chat อยู่ใน .env (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID) ไม่อยู่ใน
settings.yaml และ token ต้องอยู่ใน logging.mask_env เพราะ error ของ urllib
พก URL ที่มี token ติดมาด้วย

ใช้ urllib ของ Python เอง ไม่เพิ่ม dependency - และห้ามโยน exception ออกไปเด็ดขาด
ส่งแจ้งเตือนไม่ได้ต้องไม่ทำให้ผลของรอบนั้นเปลี่ยน
"""

from __future__ import annotations

import json
import os
import socket
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

from .logging_setup import get_logger

log = get_logger()

API = "https://api.telegram.org/bot{token}/{method}"
TIMEOUT = 15

# Telegram จำกัดข้อความ 4096 ตัว, caption ของรูป 1024 ตัว
MAX_TEXT = 3500
MAX_ERROR = 2000
MAX_PHOTOS = 2


def _post(token: str, method: str, body: bytes, content_type: str) -> None:
    req = urllib.request.Request(
        API.format(token=token, method=method), data=body,
        headers={"Content-Type": content_type}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    if not data.get("ok"):
        raise RuntimeError(data.get("description") or data)


def _send_text(token: str, chat_id: str, text: str) -> None:
    body = json.dumps({"chat_id": chat_id, "text": text,
                       "disable_web_page_preview": True}).encode("utf-8")
    _post(token, "sendMessage", body, "application/json")


def _send_photo(token: str, chat_id: str, path: Path, caption: str) -> None:
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in (("chat_id", chat_id), ("caption", caption[:1000])):
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
            f"{value}\r\n".encode("utf-8")
        )
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; "
        f"filename=\"{path.name.encode('ascii', 'replace').decode()}\"\r\n"
        f"Content-Type: image/png\r\n\r\n".encode("utf-8")
        + path.read_bytes() + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    _post(token, "sendPhoto", b"".join(parts),
          f"multipart/form-data; boundary={boundary}")


def _fmt_size(n: int) -> str:
    return f"{n / 1_000_000:.1f} MB" if n >= 1_000_000 else f"{n / 1000:.0f} KB"


def _fmt_time(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d/%m/%Y %H:%M:%S")
    except (TypeError, ValueError):
        return str(iso)


def build_message(label: str, payload: dict) -> str:
    """ข้อความสรุปหนึ่งรอบจากเนื้อหาเดียวกับ logs/last_run.json"""
    status = payload.get("status")
    upload = payload.get("upload")
    if status == "ok" and upload in (None, "ok"):
        # ข้อความตอนสำเร็จแบบย่อ - ชื่อรายงานเอาจากท้ายของ label ("BOTR05105 · R05.105")
        report = label.split("·")[-1].strip() or label
        outputs = payload.get("outputs") or []
        folder = str(Path(outputs[0].get("path", "")).parent) if outputs else ""
        project = str(Path(__file__).resolve().parents[1])
        lines = [
            f"✅ ส่งออกไฟล์ {report} สำเร็จ",
            f"โดยบอทจากโฟลเดอร์ \"{project}\"",
            f"เครื่อง: {os.environ.get('COMPUTERNAME') or socket.gethostname()}",
            f"เวลา: {_fmt_time(payload.get('started_at'))} "
            f"({payload.get('duration_seconds', 0):.0f} วินาที)",
        ]
        for out in outputs:
            lines.append(f"ไฟล์: {Path(out.get('path', '')).name} "
                         f"({_fmt_size(int(out.get('size', 0)))})")
        if folder:
            lines.append(f"โฟลเดอร์ปลายทาง: \"{folder}\"")
        lines.append("สำหรับอัปเดทราคาหน้า Price โปรแกรม Anin-Label")
        lines.append("Repo: https://github.com/it-anin/botr05105")
        return "\n".join(lines)[:MAX_TEXT]
    if status == "ok":
        head = (f"⚠️ {label} export สำเร็จ แต่อัปโหลด"
                + ("ข้ามรอบ (ไฟล์ไม่ได้อัปเดต)" if upload == "skipped" else "ไม่สำเร็จ"))
    elif status == "already_running":
        head = f"⏭️ {label} ข้ามรอบนี้ - มีบอทตัวอื่นคุม ProMaxx อยู่"
    else:
        head = f"❌ {label} ล้มเหลว"

    lines = [head]
    if payload.get("alert"):
        lines.append(f"🚨 {payload['alert']}")
    lines.append(f"เครื่อง: {os.environ.get('COMPUTERNAME') or socket.gethostname()}")
    lines.append(f"เวลา: {_fmt_time(payload.get('started_at'))} "
                 f"({payload.get('duration_seconds', 0):.0f} วินาที)")
    for out in payload.get("outputs") or []:
        lines.append(f"ไฟล์: {Path(out.get('path', '')).name} "
                     f"({_fmt_size(int(out.get('size', 0)))})")
    if status not in ("ok", "already_running"):
        error = str(payload.get("error") or "ไม่ทราบสาเหตุ")
        if len(error) > MAX_ERROR:
            error = error[:MAX_ERROR] + " …"
        lines.append("")
        lines.append(f"สาเหตุ:\n{error}")
        if payload.get("log_path"):
            lines.append("")
            lines.append(f"log: {payload['log_path']}")
    return "\n".join(lines)[:MAX_TEXT]


def failure_shots(shot_dir: Path, since: datetime) -> list[Path]:
    """ภาพหลักฐานของรอบนี้ - ภาพตอนพัง (ERROR_) และภาพ DataWindow จาก dw_click_row (_dw_)"""
    try:
        found = [
            p for p in shot_dir.glob("*.png")
            if ("_ERROR_" in p.name or "_dw_" in p.name)
            and datetime.fromtimestamp(p.stat().st_mtime) >= since
        ]
    except OSError:
        return []
    return sorted(found, key=lambda p: p.stat().st_mtime)[-MAX_PHOTOS:]


def send_run_report(cfg, payload: dict, *, since: datetime | None = None) -> bool:
    """ส่งสรุปรอบนี้เข้า Telegram คืน True ถ้าส่งข้อความหลักสำเร็จ (ไม่โยน exception)"""
    try:
        if not cfg.get("notify.enabled", True):
            return False
        if payload.get("status") == "ok" and not cfg.get("notify.on_success", True):
            return False
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if not token or not chat_id:
            log.warning("ไม่ได้ส่งแจ้งเตือน Telegram - ยังไม่ได้ใส่ TELEGRAM_BOT_TOKEN / "
                        "TELEGRAM_CHAT_ID ใน .env")
            return False

        label = str(cfg.get("notify.label", "ProMaxx bot"))
        _send_text(token, chat_id, build_message(label, payload))
        log.info("ส่งแจ้งเตือน Telegram แล้ว")
    except Exception as exc:
        log.warning("ส่งแจ้งเตือน Telegram ไม่สำเร็จ: %s", exc)
        return False

    if payload.get("status") not in ("ok", "already_running") and since is not None \
            and cfg.get("notify.send_screenshot", True):
        shot_dir = cfg.resolve_path("paths.screenshots", "screenshots")
        for shot in failure_shots(shot_dir, since):
            try:
                _send_photo(token, chat_id, shot, f"{label}: {shot.name}")
            except Exception as exc:
                log.warning("ส่งภาพ %s เข้า Telegram ไม่สำเร็จ: %s", shot.name, exc)
    return True
