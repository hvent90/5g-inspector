"""Gateway polling module for T-Mobile Dashboard."""
import httpx
from typing import Optional
from .config import get_config
from .models import SignalMetrics

class GatewayPoller:
    """Polls the T-Mobile gateway for signal data."""
    
    def __init__(self):
        config = get_config()
        self.url = f"http://{config.gateway.host}/TMI/v1/gateway?get=all"
        self.timeout = config.gateway.timeout_seconds
        self.poll_interval = config.gateway.poll_interval_ms / 1000.0
        self._last_data: Optional[dict] = None
        self._running = False
    
    async def poll_once(self) -> Optional[dict]:
        """Poll gateway once and return data."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(self.url)
                response.raise_for_status()
                self._last_data = response.json()
                return self._last_data
        except Exception as e:
            print(f"[GATEWAY] Error polling: {e}")
            return None
    
    def parse_signal(self, data: dict, network: str) -> SignalMetrics:
        """Parse signal data for a network (5g or 4g)."""
        sig = data.get("signal", {}).get(network, {})
        return SignalMetrics(
            sinr=sig.get("sinr"),
            rsrp=sig.get("rsrp"),
            rsrq=sig.get("rsrq"),
            rssi=sig.get("rssi"),
            bands=sig.get("bands", []),
            tower_id=sig.get("gNBID" if network == "5g" else "eNBID"),
            cell_id=sig.get("cid"),
        )
    
    @property
    def last_data(self) -> Optional[dict]:
        return self._last_data
