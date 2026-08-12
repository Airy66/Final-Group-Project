"""Compatibility imports for the canonical Brevo email service.

New code must import from ``services.email_service``. No SMTP transport remains.
"""

from services.email_service import (
    BREVO_REQUEST_TIMEOUT_SECONDS,
    BREVO_TRANSACTIONAL_EMAIL_URL,
    EmailConfigurationError,
    EmailDeliveryError,
    EmailService,
    EmailServiceError,
    PasswordResetMailService,
)


__all__ = [
    "BREVO_REQUEST_TIMEOUT_SECONDS",
    "BREVO_TRANSACTIONAL_EMAIL_URL",
    "EmailConfigurationError",
    "EmailDeliveryError",
    "EmailService",
    "EmailServiceError",
    "PasswordResetMailService",
]
