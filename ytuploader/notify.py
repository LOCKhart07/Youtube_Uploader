"""Optional email summary at the end of a run. Secrets come from .env."""

from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .config import NotifyConfig


def build_report_html(uploaded: int, distribution: list[dict], irregular: list) -> str:
    rows = "".join(
        f"<tr><td>{d['playlist']}</td><td>{d['uploaded']}</td>"
        f"<td>{d['remaining']}</td><td>{d['total']}</td></tr>"
        for d in distribution
    )
    tot_up = sum(d["uploaded"] for d in distribution)
    tot_rem = sum(d["remaining"] for d in distribution)
    tot_all = sum(d["total"] for d in distribution)

    html = [f"<h3>YouTube uploader run finished</h3>"]
    html.append(f"<p>Uploaded <b>{uploaded}</b> videos this session.</p>")
    if irregular:
        html.append(
            f"<p><b>{len(irregular)}</b> videos are in an improper state "
            "(marked uploaded but missing a YouTube id).</p>"
        )
    html.append("<table border='1' cellpadding='4'>")
    html.append("<tr><th>Playlist</th><th>Uploaded</th><th>Remaining</th><th>Total</th></tr>")
    html.append(rows)
    html.append(
        f"<tr><td><b>TOTAL</b></td><td><b>{tot_up}</b></td>"
        f"<td><b>{tot_rem}</b></td><td><b>{tot_all}</b></td></tr>"
    )
    html.append("</table>")
    return "".join(html)


def send_report(cfg: NotifyConfig, subject: str, html_body: str) -> None:
    if not cfg.enabled:
        return
    if not cfg.password:
        print("[notify] enabled but GMAIL_APP_PASSWORD is empty - skipping email.")
        return

    msg = MIMEMultipart()
    msg["From"] = cfg.from_addr
    msg["To"] = cfg.to_addr
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port) as server:
        server.starttls()
        server.login(cfg.from_addr, cfg.password)
        server.sendmail(cfg.from_addr, cfg.to_addr, msg.as_string())
