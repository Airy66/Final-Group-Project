"""Shared Brevo Transactional Email HTTPS delivery for application messages."""

from datetime import datetime, timezone
from html import escape
import os
from zoneinfo import ZoneInfo

import requests


BREVO_TRANSACTIONAL_EMAIL_URL = "https://api.brevo.com/v3/smtp/email"
BREVO_REQUEST_TIMEOUT_SECONDS = 15
TRUE_VALUES = {"1", "true", "yes", "on"}


PLAN_CAPABILITIES = {
    "basic": (
        "Search and compare marketplace listings",
        "Review comparable price evidence",
    ),
    "premium": (
        "Basic capabilities",
        "Saved Research",
        "Watchlist monitoring",
        "Analytics and forecasting",
    ),
    "professional": (
        "Premium capabilities",
        "Source Audit",
        "Activity Logs",
        "Professional export tools",
    ),
}


class EmailServiceError(RuntimeError):
    """Base exception with a safe message suitable for operational handling."""


class EmailConfigurationError(EmailServiceError):
    """Raised when required non-secret mail configuration is incomplete."""


class EmailDeliveryError(EmailServiceError):
    """Raised when Brevo does not accept or complete a delivery request."""

    def __init__(self, message, code="mail_delivery_failed"):
        super().__init__(message)
        self.code = code


def _env_bool(name, default=False):
    fallback = "true" if default else "false"
    return str(os.getenv(name, fallback)).strip().lower() in TRUE_VALUES


class EmailService:
    """Build application emails and deliver them through Brevo's HTTPS API."""

    subject = "Reset your Precision Curator password"
    welcome_subject = "Welcome to Precision Curator"

    def __init__(self, provider=None, enabled=None):
        self.provider = (provider or os.getenv("MAIL_PROVIDER", "brevo_api")).strip().lower()
        self.enabled = _env_bool("MAIL_ENABLED", False) if enabled is None else bool(enabled)

    def send_password_reset(self, recipient, reset_url, ttl_minutes):
        return self._deliver(
            recipient,
            self.subject,
            self._text_body(reset_url, ttl_minutes),
            self._html_body(reset_url, ttl_minutes),
        )

    def send_registration_welcome(self, recipient, display_name, login_url, membership_tier="basic"):
        """Send a registration confirmation after the account is persisted."""
        return self._deliver(
            recipient,
            self.welcome_subject,
            self._welcome_text_body(display_name, login_url, membership_tier),
            self._welcome_html_body(display_name, login_url, membership_tier),
        )

    def send_price_alert(self, recipient, monitor_name, direction, threshold, previous_price,
                         current_price, change_percent, snapshot_time, monitor_url):
        subject = f"Precision Curator price alert: {monitor_name} changed {abs(change_percent):.2f}%"
        text_body, html_body = self._price_alert_bodies(
            monitor_name, direction, threshold, previous_price, current_price,
            change_percent, snapshot_time, monitor_url, is_test=False,
        )
        return self._deliver(recipient, subject, text_body, html_body)

    def send_price_alert_test(self, recipient, monitor_name, monitor_url):
        subject = f"Precision Curator price alert test: {monitor_name}"
        text_body, html_body = self._price_alert_bodies(
            monitor_name, "either", 5, None, None, None, datetime.now(timezone.utc),
            monitor_url, is_test=True,
        )
        return self._deliver(recipient, subject, text_body, html_body)

    def _configuration(self):
        if not self.enabled:
            raise EmailConfigurationError("Email delivery is disabled by MAIL_ENABLED.")
        if self.provider != "brevo_api":
            raise EmailConfigurationError("MAIL_PROVIDER must be 'brevo_api'.")
        api_key = os.getenv("BREVO_API_KEY", "").strip()
        sender_email = os.getenv("BREVO_SENDER_EMAIL", "").strip()
        sender_name = os.getenv("BREVO_SENDER_NAME", "").strip()
        missing = []
        if not api_key:
            missing.append("BREVO_API_KEY")
        if not sender_email:
            missing.append("BREVO_SENDER_EMAIL")
        if not sender_name:
            missing.append("BREVO_SENDER_NAME")
        if missing:
            raise EmailConfigurationError(
                "Brevo email configuration is incomplete: " + ", ".join(missing) + "."
            )
        return api_key, sender_email, sender_name

    def _deliver(self, recipient, subject, text_body, html_body):
        api_key, sender_email, sender_name = self._configuration()
        recipient = str(recipient or "").strip()
        if not recipient:
            raise EmailConfigurationError("Email recipient is required.")
        payload = {
            "sender": {"email": sender_email, "name": sender_name},
            "to": [{"email": recipient}],
            "subject": str(subject),
            "textContent": str(text_body),
            "htmlContent": str(html_body),
        }
        try:
            response = requests.post(
                BREVO_TRANSACTIONAL_EMAIL_URL,
                headers={"api-key": api_key, "content-type": "application/json"},
                json=payload,
                timeout=BREVO_REQUEST_TIMEOUT_SECONDS,
            )
        except requests.Timeout as exc:
            raise EmailDeliveryError("Brevo email delivery timed out.", code="delivery_timeout") from exc
        except requests.RequestException as exc:
            raise EmailDeliveryError("Brevo email delivery could not connect.", code="delivery_connection_failed") from exc
        if not 200 <= response.status_code < 300:
            raise EmailDeliveryError(
                f"Brevo email delivery was rejected with HTTP {response.status_code}.",
                code="provider_rejected",
            )
        try:
            message_id = str(response.json().get("messageId") or "").strip()
        except (TypeError, ValueError):
            message_id = ""
        if not message_id:
            raise EmailDeliveryError("Brevo email delivery returned no message identifier.")
        return message_id

    @staticmethod
    def _email_html(heading, explanation, action_label, action_url, details_html="", notice_html="", show_fallback_url=False):
        safe_url = escape(str(action_url), quote=True)
        fallback = ""
        if show_fallback_url:
            fallback = f"""
      <p style="margin:0 0 6px;font-size:13px;line-height:1.55;color:#64748b">Button not working? Copy and paste this link into your browser:</p>
      <p style="margin:0;overflow-wrap:anywhere;word-break:break-all;font-size:13px;line-height:1.55"><a href="{safe_url}" style="color:#2563eb">{safe_url}</a></p>"""
        return f"""<!doctype html>
<html lang="en"><body style="margin:0;background:#f4f7fb;font-family:Arial,sans-serif;color:#172033">
<div style="width:100%;padding:24px 12px;box-sizing:border-box">
  <div style="max-width:600px;margin:0 auto;border:1px solid #dbe4ef;border-radius:12px;background:#ffffff;overflow:hidden">
    <div style="padding:18px 28px;border-bottom:1px solid #e5eaf1;background:#f8fbff;color:#1d4ed8;font-size:14px;font-weight:700;letter-spacing:.04em">Precision Curator</div>
    <div style="padding:28px">
      {notice_html}
      <h1 style="margin:0 0 14px;font-size:26px;line-height:1.25;color:#0f172a">{escape(str(heading))}</h1>
      <p style="margin:0 0 20px;line-height:1.65;color:#475569">{explanation}</p>
      {details_html}
      <p style="margin:24px 0"><a href="{safe_url}" style="display:inline-block;border-radius:8px;background:#2563eb;padding:13px 20px;color:#ffffff;font-weight:700;text-decoration:none">{escape(str(action_label))}</a></p>
      {fallback}
    </div>
    <div style="padding:18px 28px;border-top:1px solid #e5eaf1;background:#f8fafc;font-size:12px;line-height:1.6;color:#64748b">Precision Curator provides decision-support information from collected marketplace data. Verify current listing details before acting.</div>
  </div>
</div></body></html>"""

    def _price_alert_bodies(self, monitor_name, direction, threshold, previous_price,
                            current_price, change_percent, snapshot_time, monitor_url, is_test=False):
        label = "This is a test notification. No marketplace threshold was triggered." if is_test else "A configured price threshold was crossed."
        when = snapshot_time if isinstance(snapshot_time, datetime) else datetime.now(timezone.utc)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        when_text = when.astimezone(ZoneInfo("Asia/Singapore")).strftime("%d %b %Y, %I:%M %p SGT")
        threshold_value = f"{threshold:g}%"
        threshold_text = {
            "drop": f"Price drops by {threshold_value} or more",
            "increase": f"Price rises by {threshold_value} or more",
            "either": f"Price changes by {threshold_value} or more in either direction",
        }.get(str(direction).strip().lower(), f"Price changes by {threshold_value} or more")
        safe_name = escape(str(monitor_name))

        if is_test:
            future_note = "Future triggered alerts will include the previous price, current price and percentage change."
            text = (
                f"Precision Curator\n\nPrice alert test\n\n{label}\n\n"
                f"Monitor: {monitor_name}\nAlert rule: {threshold_text}\nTest sent: {when_text}\n\n"
                f"{future_note}\n\nView Monitor: {monitor_url}\n\n"
                "Prices are based on comparable marketplace listings collected for this Monitor "
                "and may change after collection. Verify current listing details before acting."
            )
            details = f"""<table role="presentation" style="width:100%;border-collapse:collapse;font-size:14px;line-height:1.55">
<tr><td style="padding:7px 0;color:#64748b">Monitor</td><td style="padding:7px 0;text-align:right;font-weight:700">{safe_name}</td></tr>
<tr><td style="padding:7px 0;color:#64748b">Alert rule</td><td style="padding:7px 0;text-align:right;font-weight:700">{escape(threshold_text)}</td></tr>
<tr><td style="padding:7px 0;color:#64748b">Test sent</td><td style="padding:7px 0;text-align:right;font-weight:700">{when_text}</td></tr>
</table>
<p style="margin:18px 0 0;border-radius:8px;background:#f8fafc;padding:12px;font-size:13px;line-height:1.6;color:#64748b">{escape(future_note)}</p>"""
            return text, self._email_html("Price alert test", escape(label), "View Monitor", monitor_url, details)

        movement = "Not applicable" if change_percent is None else f"{change_percent:+.2f}%"
        previous = "Not applicable" if previous_price is None else f"USD {previous_price:.2f}"
        current = "Not applicable" if current_price is None else f"USD {current_price:.2f}"
        heading = "Price decrease detected" if change_percent is not None and change_percent < 0 else "Price increase detected" if change_percent is not None and change_percent > 0 else "Price alert"
        text = (
            f"Precision Curator\n\n{heading}\n\n{label}\n\nMonitor: {monitor_name}\n"
            f"Previous comparable price: {previous}\nCurrent comparable price: {current}\nChange: {movement}\n"
            f"Triggered threshold: {threshold_text}\nSnapshot time: {when_text}\n\n"
            f"View Monitor: {monitor_url}\n\nPrices are based on comparable marketplace listings collected "
            "for this Monitor and may change after collection. Verify current listing details before acting."
        )
        details = f"""<table role="presentation" style="width:100%;border-collapse:collapse;font-size:14px;line-height:1.55">
<tr><td style="padding:7px 0;color:#64748b">Monitor</td><td style="padding:7px 0;text-align:right;font-weight:700">{safe_name}</td></tr>
<tr><td style="padding:7px 0;color:#64748b">Previous comparable price</td><td style="padding:7px 0;text-align:right;font-weight:700">{previous}</td></tr>
<tr><td style="padding:7px 0;color:#64748b">Current comparable price</td><td style="padding:7px 0;text-align:right;font-weight:700">{current}</td></tr>
<tr><td style="padding:7px 0;color:#64748b">Change</td><td style="padding:7px 0;text-align:right;font-weight:700">{movement}</td></tr>
<tr><td style="padding:7px 0;color:#64748b">Triggered threshold</td><td style="padding:7px 0;text-align:right;font-weight:700">{escape(threshold_text)}</td></tr>
<tr><td style="padding:7px 0;color:#64748b">Snapshot time</td><td style="padding:7px 0;text-align:right;font-weight:700">{when_text}</td></tr>
</table>"""
        return text, self._email_html(heading, escape(label), "View Monitor", monitor_url, details)

    def _text_body(self, reset_url, ttl_minutes):
        return (
            "We received a request to reset your Precision Curator password.\n\n"
            f"Use the link below within {ttl_minutes} minutes:\n\n{reset_url}\n\n"
            "If you did not request this change, you can ignore this email."
        )

    def _html_body(self, reset_url, ttl_minutes):
        details = f"""<div style="border-radius:8px;background:#f8fafc;padding:14px;font-size:14px;line-height:1.6;color:#475569">
This link expires in {int(ttl_minutes)} minutes. If you did not request this change, you can ignore this email.
</div>"""
        return self._email_html(
            "Reset your password",
            "We received a request to reset your Precision Curator password.",
            "Reset password",
            reset_url,
            details,
            show_fallback_url=True,
        )

    def _welcome_text_body(self, display_name, login_url, membership_tier="basic"):
        name = " ".join(str(display_name or "").split()) or "there"
        tier = str(membership_tier or "basic").strip().lower()
        if tier not in PLAN_CAPABILITIES:
            tier = "basic"
        tier_label = tier.capitalize()
        capabilities = "\n".join(f"- {item}" for item in PLAN_CAPABILITIES[tier])
        return (
            f"Hello {name},\n\n"
            "Welcome to Precision Curator. Your account was created successfully.\n\n"
            f"Your current plan: {tier_label}\n\n{capabilities}\n\n"
            f"Sign in: {login_url}\n\n"
            "No action is required to activate your account.\n\n"
            "If you did not create this account, contact the project administrator."
        )

    def _welcome_html_body(self, display_name, login_url, membership_tier="basic"):
        safe_name = escape(" ".join(str(display_name or "").split()) or "there")
        tier = str(membership_tier or "basic").strip().lower()
        if tier not in PLAN_CAPABILITIES:
            tier = "basic"
        tier_label = tier.capitalize()
        capabilities = "".join(f'<li style="margin:0 0 7px">{escape(item)}</li>' for item in PLAN_CAPABILITIES[tier])
        details = f"""<div style="border-radius:8px;background:#f8fafc;padding:16px;color:#334155">
<p style="margin:0 0 10px;font-weight:700">Your current plan: {tier_label}</p>
<ul style="margin:0;padding-left:20px;line-height:1.55">{capabilities}</ul>
</div>
<p style="margin:16px 0 0;font-size:13px;line-height:1.6;color:#64748b">No action is required to activate your account. If you did not create this account, contact the project administrator.</p>"""
        return self._email_html(
            "Welcome to Precision Curator",
            f"Hello {safe_name}, your account was created successfully.",
            "Sign in",
            login_url,
            details,
        )


# Temporary class alias for integrations that imported the old class name.
PasswordResetMailService = EmailService
