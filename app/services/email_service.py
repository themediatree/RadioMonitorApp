"""
Email sending service.

Sends via SMTP when smtp_host is configured in .env; falls back to console
logging during development. The interface is stable — callers never change.

Configuration (.env):
    SMTP_HOST=smtp.office365.com    # or smtp.gmail.com, mail.yourisp.co.za etc
    SMTP_PORT=587
    SMTP_USERNAME=no-reply@yourdomain.co.za
    SMTP_PASSWORD=yourpassword
    SMTP_FROM=RadioMonitor <no-reply@yourdomain.co.za>
    SMTP_USE_TLS=true               # uses STARTTLS on port 587
    BASE_URL=http://192.168.1.53:8000  # used to build invite links
"""

import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import settings

logger = logging.getLogger("radiomonitor.email")


def _invite_url(token: str) -> str:
    base = settings.base_url.rstrip("/")
    return f"{base}/accept-invite?token={token}"


def _reset_url(token: str) -> str:
    base = settings.base_url.rstrip("/")
    return f"{base}/reset-password?token={token}"


def send_password_reset_email(*, to_email: str, token: str) -> None:
    """
    Send a password reset email. Falls back to console log if SMTP is not
    configured or sending fails.
    """
    url = _reset_url(token)

    if settings.smtp_host:
        try:
            _send_via_smtp(
                to_email=to_email,
                subject="Reset your NOCTIV password",
                html=_reset_html(url),
                text=_reset_text(url),
            )
            logger.info("Password reset email sent to %s", to_email)
            return
        except Exception as e:
            logger.error("SMTP send failed (%s); falling back to console log", e)

    # Console fallback.
    logger.info(
        "\n"
        "============================================================\n"
        "  PASSWORD RESET REQUESTED (SMTP not configured -- copy link)\n"
        "  To:    %s\n"
        "  Link:  %s\n"
        "  (valid for 2 hours)\n"
        "============================================================",
        to_email, url,
    )


def _reset_html(url: str) -> str:
    return f"""
<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;max-width:600px;margin:40px auto;color:#222">
  <h2 style="color:#1a1a2e">Reset your NOCTIV password</h2>
  <p>We received a request to reset your password. Click the button below to choose a new one.</p>
  <p style="margin:32px 0">
    <a href="{url}"
       style="background:#1a1a2e;color:#fff;padding:12px 24px;border-radius:6px;
              text-decoration:none;font-weight:bold">
      Reset password
    </a>
  </p>
  <p style="color:#666;font-size:0.9em">
    This link expires in 2 hours. If you didn't request this, you can safely ignore this email --
    your password will not be changed.
  </p>
  <p style="color:#666;font-size:0.85em">
    If the button doesn't work, copy this link into your browser:<br>
    <a href="{url}" style="color:#666">{url}</a>
  </p>
</body>
</html>
"""


def _reset_text(url: str) -> str:
    return (
        f"Reset your NOCTIV password\n\n"
        f"We received a request to reset your password. Use the link below to choose a new one:\n"
        f"{url}\n\n"
        f"This link expires in 2 hours. If you didn't request this, you can safely ignore this "
        f"email -- your password will not be changed.\n"
    )


def send_invitation_email(
    *,
    to_email: str,
    token: str,
    invited_by_name: str,
    user_type: str,
) -> None:
    """
    Send an invitation email. Falls back to console log if SMTP is not
    configured or sending fails (so a DB-created invite is never lost).
    """
    url = _invite_url(token)
    role = "Admin" if user_type == "subscriber_admin" else "User"

    if settings.smtp_host:
        try:
            _send_via_smtp(
                to_email=to_email,
                subject="You have been invited to RadioMonitor",
                html=_invite_html(url, invited_by_name, role),
                text=_invite_text(url, invited_by_name, role),
            )
            logger.info("Invitation email sent to %s", to_email)
            return
        except Exception as e:
            logger.error("SMTP send failed (%s); falling back to console log", e)

    # Console fallback.
    logger.info(
        "\n"
        "============================================================\n"
        "  INVITATION CREATED (SMTP not configured -- copy link)\n"
        "  To:         %s\n"
        "  Role:       %s\n"
        "  Invited by: %s\n"
        "  Link:       %s\n"
        "  (valid for 7 days)\n"
        "============================================================",
        to_email, role, invited_by_name, url,
    )


def _send_via_smtp(*, to_email: str, subject: str, html: str, text: str) -> None:
    """Send a message via SMTP using settings from config."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = to_email
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))

    if settings.smtp_use_tls:
        # STARTTLS (port 587 — standard for Office 365, Gmail, most ISPs)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
            server.ehlo()
            context = ssl.create_default_context()
            server.starttls(context=context)
            server.ehlo()
            if settings.smtp_username:
                server.login(settings.smtp_username, settings.smtp_password)
            server.sendmail(settings.smtp_from, to_email, msg.as_string())
    else:
        # SSL (port 465)
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port,
                               context=context, timeout=15) as server:
            if settings.smtp_username:
                server.login(settings.smtp_username, settings.smtp_password)
            server.sendmail(settings.smtp_from, to_email, msg.as_string())


def _invite_html(url: str, invited_by: str, role: str) -> str:
    return f"""
<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;max-width:600px;margin:40px auto;color:#222">
  <h2 style="color:#1a1a2e">You've been invited to RadioMonitor</h2>
  <p>{invited_by} has invited you to join RadioMonitor as a <strong>{role}</strong>.</p>
  <p>RadioMonitor provides proof-of-broadcast tracking for radio commercials.</p>
  <p style="margin:32px 0">
    <a href="{url}"
       style="background:#1a1a2e;color:#fff;padding:12px 24px;border-radius:6px;
              text-decoration:none;font-weight:bold">
      Accept invitation
    </a>
  </p>
  <p style="color:#666;font-size:0.9em">
    This link expires in 7 days. If you did not expect this invitation, you can
    ignore this email.
  </p>
  <p style="color:#666;font-size:0.85em">
    If the button doesn't work, copy this link into your browser:<br>
    <a href="{url}" style="color:#666">{url}</a>
  </p>
</body>
</html>
"""


def _invite_text(url: str, invited_by: str, role: str) -> str:
    return (
        f"You've been invited to RadioMonitor\n\n"
        f"{invited_by} has invited you to join RadioMonitor as a {role}.\n\n"
        f"Accept your invitation here:\n{url}\n\n"
        f"This link expires in 7 days.\n"
        f"If you did not expect this invitation, you can ignore this email.\n"
    )
