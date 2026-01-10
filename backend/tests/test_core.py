"""Tests for T-Mobile Dashboard."""
import pytest
from tmobile_dashboard.config import get_config, AppConfig
from tmobile_dashboard.models import SignalMetrics, SignalQuality

class TestConfig:
    def test_default_config(self):
        config = get_config()
        assert config.gateway.host == "192.168.12.1"
        assert config.gateway.port == 80
        assert config.server.port == 8080

class TestModels:
    def test_signal_quality_excellent(self):
        signal = SignalMetrics(sinr=25.0)
        assert signal.get_quality() == SignalQuality.EXCELLENT
    
    def test_signal_quality_good(self):
        signal = SignalMetrics(sinr=15.0)
        assert signal.get_quality() == SignalQuality.GOOD
    
    def test_signal_quality_poor(self):
        signal = SignalMetrics(sinr=-3.0)
        assert signal.get_quality() == SignalQuality.POOR
    
    def test_signal_quality_critical(self):
        signal = SignalMetrics(sinr=-10.0)
        assert signal.get_quality() == SignalQuality.CRITICAL
