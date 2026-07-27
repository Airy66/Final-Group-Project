"""Small mail abstraction for account-security messages."""

from email.message import EmailMessage
from html import escape
import os
import smtplib
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


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


def _env_bool(name, default=False):
    return os.getenv(name, "true" if default else "false").strip().lower() in {"1", "true", "yes", "on"}


class PasswordResetMailService:
    subject = "Reset your Precision Curator password"
    welcome_subject = "Welcome to Precision Curator"

    def __init__(self, mode=None):
        self.mode = (mode or os.getenv("EMAIL_MODE", "console")).strip().lower()

    def send_password_reset(self, recipient, reset_url, ttl_minutes):
        if self.mode == "console":
            self._print_console_message(recipient, reset_url, ttl_minutes)
            return "printed"
        if self.mode == "smtp":
            self._send_smtp(recipient, reset_url, ttl_minutes)
            return "queued"
        raise ValueError("EMAIL_MODE must be 'console' or 'smtp'.")

    def send_registration_welcome(self, recipient, display_name, login_url, membership_tier="basic"):
        """Send one registration confirmation using the configured mail transport."""
        if self.mode == "console":
            self._print_welcome_console_message(recipient, display_name, login_url, membership_tier)
            return "printed"
        if self.mode == "smtp":
            self._send_welcome_smtp(recipient, display_name, login_url, membership_tier)
            return "queued"
        raise ValueError("EMAIL_MODE must be 'console' or 'smtp'.")

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

    def _deliver(self, recipient, subject, text_body, html_body):
        if self.mode == "console":
            print("=" * 50)
            print("PRICE ALERT EMAIL - DEVELOPMENT MODE")
            print(f"To: {recipient}")
            print(f"Subject: {subject}")
            print(text_body)
            print("=" * 50)
            return "printed"
        if self.mode == "smtp":
            self._send_message(recipient, subject, text_body, html_body)
            return "queued"
        raise ValueError("EMAIL_MODE must be 'console' or 'smtp'.")

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
            html = self._email_html("Price alert test", escape(label), "View Monitor", monitor_url, details)
            return text, html

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
        html = self._email_html(heading, escape(label), "View Monitor", monitor_url, details)
        return text, html

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

    def _print_console_message(self, recipient, reset_url, ttl_minutes):
        print("=" * 50)
        print("PASSWORD RESET EMAIL — DEVELOPMENT MODE")
        print(f"To: {recipient}")
        print(f"Subject: {self.subject}")
        print("A password reset link was generated and withheld from application logs.")
        print(f"Expires in: {ttl_minutes} minutes")
        print("=" * 50)

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

    def _print_welcome_console_message(self, recipient, display_name, login_url, membership_tier="basic"):
        print("=" * 50)
        print("REGISTRATION WELCOME EMAIL - DEVELOPMENT MODE")
        print(f"To: {recipient}")
        print(f"Subject: {self.welcome_subject}")
        print(self._welcome_text_body(display_name, login_url, membership_tier))
        print("=" * 50)

    def _send_smtp(self, recipient, reset_url, ttl_minutes):
        self._send_message(
            recipient,
            self.subject,
            self._text_body(reset_url, ttl_minutes),
            self._html_body(reset_url, ttl_minutes),
        )

    def _send_welcome_smtp(self, recipient, display_name, login_url, membership_tier="basic"):
        self._send_message(
            recipient,
            self.welcome_subject,
            self._welcome_text_body(display_name, login_url, membership_tier),
            self._welcome_html_body(display_name, login_url, membership_tier),
        )

    def _send_message(self, recipient, subject, text_body, html_body):
        host = os.getenv("MAIL_HOST", "").strip()
        port = int(os.getenv("MAIL_PORT", "587"))
        username = os.getenv("MAIL_USERNAME", "").strip()
        password = os.getenv("MAIL_PASSWORD", "")
        from_address = os.getenv("MAIL_FROM_ADDRESS", "").strip()
        from_name = os.getenv("MAIL_FROM_NAME", "Precision Curator").strip() or "Precision Curator"
        try:
            timeout = max(1.0, float(os.getenv("MAIL_TIMEOUT_SECONDS", "15")))
        except (TypeError, ValueError):
            timeout = 15.0
        use_ssl = _env_bool("MAIL_USE_SSL", False)
        if not host or not from_address:
            raise RuntimeError("SMTP mail configuration is incomplete.")
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = f"{from_name} <{from_address}>"
        message["To"] = recipient
        message.set_content(text_body)
        message.add_alternative(html_body, subtype="html")
        smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        with smtp_class(host, port, timeout=timeout) as client:
            if _env_bool("MAIL_USE_TLS", True) and not use_ssl:
                client.starttls()
            if getattr(client, "sock", None) is not None:
                client.sock.settimeout(timeout)
            if username:
                client.login(username, password)
            client.send_message(message)
