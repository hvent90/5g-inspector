import json

overview = {
    "annotations": {"list": [{"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"}, "enable": True, "hide": True, "iconColor": "rgba(0, 211, 255, 1)", "name": "Annotations", "type": "dashboard"}]},
    "editable": True,
    "fiscalYearStartMonth": 0,
    "graphTooltip": 1,
    "id": None,
    "links": [],
    "liveNow": False,
    "panels": [
        {"datasource": {"type": "prometheus", "uid": "prometheus"}, "fieldConfig": {"defaults": {"color": {"mode": "thresholds"}, "mappings": [], "max": -44, "min": -140, "thresholds": {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "orange", "value": -110}, {"color": "yellow", "value": -100}, {"color": "green", "value": -80}]}, "unit": "dBm"}, "overrides": []}, "gridPos": {"h": 8, "w": 6, "x": 0, "y": 0}, "id": 1, "options": {"orientation": "auto", "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "showThresholdLabels": False, "showThresholdMarkers": True}, "pluginVersion": "10.0.0", "targets": [{"datasource": {"type": "prometheus", "uid": "prometheus"}, "expr": "tmobile_signal_rsrp", "refId": "A"}], "title": "Signal Strength (RSRP)", "type": "gauge"},
