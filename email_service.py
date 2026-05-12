"""
email_service.py — GT Scout Bot
Serviço de email separado. Padrão do fifa25-bot.
"""

import os
import io
import smtplib
import logging
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders

import pytz

logger = logging.getLogger(__name__)
BR_TZ = pytz.timezone("America/Sao_Paulo")


class EmailService:

    def __init__(self):
        self.user     = os.getenv("EMAIL_USER", "")
        self.password = os.getenv("EMAIL_PASSWORD", "")
        self.smtp_srv = os.getenv("EMAIL_SMTP_SERVER", "smtp.gmail.com")
        self.smtp_port= int(os.getenv("EMAIL_SMTP_PORT", 587))
        self.recipient= os.getenv("EMAIL_RECIPIENT", self.user)
        self.enabled  = bool(self.user and self.password)

        if not self.enabled:
            logger.warning("[Email] EMAIL_USER ou EMAIL_PASSWORD não configurados.")

    def send_report(self, xlsx_bytes: bytes, filename: str, total_matches: int) -> bool:
        """Envia relatório semanal com planilha em anexo."""
        if not self.enabled:
            logger.warning("[Email] Email não configurado — relatório não enviado.")
            return False

        now_str = datetime.now(BR_TZ).strftime("%d/%m/%Y %H:%M")
        try:
            msg = MIMEMultipart()
            msg["From"]    = self.user
            msg["To"]      = self.recipient
            msg["Subject"] = f"📊 GT Scout — Relatório Semanal ({now_str})"

            body = (
                f"Relatório GT Scout\n\n"
                f"Data: {now_str}\n"
                f"Total de partidas: {total_matches}\n\n"
                "GT Scout Bot 🤖"
            )
            msg.attach(MIMEText(body, "plain"))

            part = MIMEBase("application", "octet-stream")
            part.set_payload(xlsx_bytes)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
            msg.attach(part)

            with smtplib.SMTP(self.smtp_srv, self.smtp_port) as server:
                server.ehlo()
                server.starttls()
                server.login(self.user, self.password)
                server.sendmail(self.user, self.recipient, msg.as_string())

            logger.info(f"[Email] Relatório enviado para {self.recipient}.")
            return True

        except Exception as e:
            logger.error(f"[Email] Erro ao enviar: {e}")
            return False
