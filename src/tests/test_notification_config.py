from ecommerce_price_monitor.config import ConfigManager


def test_project_notification_config_matches_brevo_schema(capsys):
    config = ConfigManager("config/config.yaml").load_config()

    assert config.notification.email_provider == "brevo_api"
    assert config.notification.sender_email == ""
    assert config.notification.sender_name == "Precision Curator"
    assert "Error loading config" not in capsys.readouterr().out
