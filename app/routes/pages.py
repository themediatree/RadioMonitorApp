"""
Public-facing pages.

    GET  /          Landing page (NOCTIV)
    GET  /about     About (placeholder)
    POST /enquiry   Contact form submission
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.templating import templates

router = APIRouter(tags=["pages"])
logger = logging.getLogger("radiomonitor.pages")


@router.get("/health")
def health_check():
    return JSONResponse({"status": "ok"})


@router.get("/", response_class=HTMLResponse)
def landing(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="landing.html",
        context={"enquiry_sent": False, "enquiry_error": False, "form": {}},
    )


@router.post("/enquiry")
async def enquiry_submit(
    request: Request,
    first_name: Annotated[str, Form()] = "",
    last_name: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
    company: Annotated[str, Form()] = "",
    interest: Annotated[str, Form()] = "",
    message: Annotated[str, Form()] = "",
):
    form_data = {
        "first_name": first_name.strip(),
        "last_name": last_name.strip(),
        "email": email.strip(),
        "company": company.strip(),
        "interest": interest,
        "message": message.strip(),
    }

    # Validate minimum required fields
    if not all([form_data["first_name"], form_data["email"], form_data["company"]]):
        return templates.TemplateResponse(
            request=request,
            name="landing.html",
            context={"enquiry_sent": False, "enquiry_error": True, "form": form_data},
            status_code=400,
        )

    # Validate email format
    from app.utils.email_validator import validate_email, EmailValidationError
    try:
        form_data["email"] = validate_email(form_data["email"])
    except EmailValidationError:
        return templates.TemplateResponse(
            request=request,
            name="landing.html",
            context={"enquiry_sent": False, "enquiry_error": True, "form": form_data},
            status_code=400,
        )

    # Send notification email to the NOCTIV team
    _send_enquiry_notification(form_data)

    # Log to audit trail regardless of email success
    logger.info(
        "New enquiry: name=%s %s email=%s company=%s interest=%s",
        form_data["first_name"], form_data["last_name"],
        form_data["email"], form_data["company"], form_data["interest"],
    )

    return templates.TemplateResponse(
        request=request,
        name="landing.html",
        context={"enquiry_sent": True, "enquiry_error": False, "form": {}},
    )


def _send_enquiry_notification(form: dict) -> None:
    """Email the NOCTIV team about a new enquiry. Fails silently."""
    from app.config import settings
    if not settings.smtp_host:
        logger.info(
            "\n"
            "=== NEW ENQUIRY (SMTP not configured) ===\n"
            "  Name:     %s %s\n"
            "  Email:    %s\n"
            "  Company:  %s\n"
            "  Interest: %s\n"
            "  Message:  %s\n"
            "=========================================",
            form["first_name"], form["last_name"],
            form["email"], form["company"],
            form["interest"], form["message"],
        )
        return

    try:
        import smtplib, ssl
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        subject = f"New NOCTIV enquiry — {form['company']} ({form['interest']})"
        body_text = (
            f"New enquiry from the NOCTIV landing page\n\n"
            f"Name:     {form['first_name']} {form['last_name']}\n"
            f"Email:    {form['email']}\n"
            f"Company:  {form['company']}\n"
            f"Interest: {form['interest']}\n\n"
            f"Message:\n{form['message'] or '(none)'}\n"
        )
        body_html = f"""
<html><body style="font-family:Arial,sans-serif;color:#222;max-width:600px">
<h2>New NOCTIV enquiry</h2>
<table style="border-collapse:collapse;width:100%">
  <tr><td style="padding:6px 12px;background:#f5f5f5;font-weight:600;width:120px">Name</td>
      <td style="padding:6px 12px">{form['first_name']} {form['last_name']}</td></tr>
  <tr><td style="padding:6px 12px;font-weight:600">Email</td>
      <td style="padding:6px 12px"><a href="mailto:{form['email']}">{form['email']}</a></td></tr>
  <tr><td style="padding:6px 12px;background:#f5f5f5;font-weight:600">Company</td>
      <td style="padding:6px 12px;background:#f5f5f5">{form['company']}</td></tr>
  <tr><td style="padding:6px 12px;font-weight:600">Interest</td>
      <td style="padding:6px 12px">{form['interest']}</td></tr>
</table>
{"<h3>Message</h3><p>" + form['message'] + "</p>" if form['message'] else ""}
<hr style="margin:2rem 0;border:none;border-top:1px solid #ddd">
<p style="color:#666;font-size:0.85em">Sent from the NOCTIV landing page.</p>
</body></html>"""

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.smtp_from
        msg["To"] = settings.smtp_username  # notify the team inbox
        msg["Reply-To"] = form["email"]     # reply goes directly to enquirer
        msg.attach(MIMEText(body_text, "plain"))
        msg.attach(MIMEText(body_html, "html"))

        if settings.smtp_use_tls:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as s:
                s.ehlo(); s.starttls(context=ssl.create_default_context()); s.ehlo()
                if settings.smtp_username:
                    s.login(settings.smtp_username, settings.smtp_password)
                s.sendmail(settings.smtp_from, settings.smtp_username, msg.as_string())
        else:
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port,
                                   context=ssl.create_default_context(), timeout=15) as s:
                if settings.smtp_username:
                    s.login(settings.smtp_username, settings.smtp_password)
                s.sendmail(settings.smtp_from, settings.smtp_username, msg.as_string())

        logger.info("Enquiry notification sent to %s", settings.smtp_username)

    except Exception as e:
        logger.error("Failed to send enquiry notification: %s", e)
