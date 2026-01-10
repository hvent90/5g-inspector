"""Speedtest tool implementations with pluggable architecture."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import json
import subprocess
import sys
from typing import Any

import structlog

log = structlog.get_logger()


@dataclass
class SpeedtestToolResult:
    """Standardized result from any speedtest tool."""

    status: str  # "success", "error", "timeout"
    download_mbps: float = 0.0
    upload_mbps: float = 0.0
    ping_ms: float = 0.0
    jitter_ms: float | None = None

    # Server info
    server_name: str | None = None
    server_location: str | None = None
    server_host: str | None = None
    server_id: int | None = None

    # Client info
    client_ip: str | None = None
    isp: str | None = None

    # Tool metadata
    tool: str = "unknown"
    result_url: str | None = None

    # Error info
    error_message: str | None = None
    raw_output: dict[str, Any] = field(default_factory=dict)


class SpeedtestTool(ABC):
    """Abstract base class for speedtest tools."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool identifier."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this tool is installed and available."""
        pass

    @abstractmethod
    def run(self, timeout: int = 120, **kwargs) -> SpeedtestToolResult:
        """Run the speedtest. Must be synchronous (called from thread pool)."""
        pass


class OoklaSpeedtestTool(SpeedtestTool):
    """Ookla official CLI tool (speedtest command)."""

    @property
    def name(self) -> str:
        return "ookla-speedtest"

    def is_available(self) -> bool:
        try:
            result = subprocess.run(
                ["speedtest", "--version"],
                capture_output=True,
                timeout=5,
            )
            # Ookla CLI outputs "Speedtest by Ookla" in version
            return result.returncode == 0 and b"Ookla" in result.stdout
        except Exception:
            return False

    def run(
        self,
        timeout: int = 120,
        server_id: int | None = None,
        **kwargs,
    ) -> SpeedtestToolResult:
        cmd = ["speedtest", "--format=json", "--accept-license", "--accept-gdpr"]
        if server_id:
            cmd.extend(["--server-id", str(server_id)])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            if result.returncode == 0:
                data = json.loads(result.stdout)
                return SpeedtestToolResult(
                    status="success",
                    download_mbps=round(
                        data.get("download", {}).get("bandwidth", 0) * 8 / 1_000_000, 2
                    ),
                    upload_mbps=round(
                        data.get("upload", {}).get("bandwidth", 0) * 8 / 1_000_000, 2
                    ),
                    ping_ms=round(data.get("ping", {}).get("latency", 0), 1),
                    jitter_ms=round(data.get("ping", {}).get("jitter", 0), 1),
                    server_name=data.get("server", {}).get("name"),
                    server_location=data.get("server", {}).get("location"),
                    server_host=data.get("server", {}).get("host"),
                    server_id=data.get("server", {}).get("id"),
                    client_ip=data.get("interface", {}).get("externalIp"),
                    isp=data.get("isp"),
                    tool=self.name,
                    result_url=data.get("result", {}).get("url"),
                    raw_output=data,
                )
            else:
                return SpeedtestToolResult(
                    status="error",
                    tool=self.name,
                    error_message=result.stderr or f"Exit code {result.returncode}",
                )

        except subprocess.TimeoutExpired:
            return SpeedtestToolResult(
                status="timeout",
                tool=self.name,
                error_message=f"Timed out after {timeout}s",
            )
        except json.JSONDecodeError as e:
            return SpeedtestToolResult(
                status="error",
                tool=self.name,
                error_message=f"JSON parse error: {e}",
            )
        except Exception as e:
            return SpeedtestToolResult(
                status="error",
                tool=self.name,
                error_message=str(e),
            )


class SpeedtestCliTool(SpeedtestTool):
    """Python speedtest-cli package."""

    @property
    def name(self) -> str:
        return "speedtest-cli"

    def is_available(self) -> bool:
        try:
            result = subprocess.run(
                [sys.executable, "-m", "speedtest", "--version"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def run(self, timeout: int = 120, **kwargs) -> SpeedtestToolResult:
        try:
            result = subprocess.run(
                [sys.executable, "-m", "speedtest", "--json"],
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            if result.returncode == 0:
                data = json.loads(result.stdout)
                server = data.get("server", {})
                client = data.get("client", {})
                return SpeedtestToolResult(
                    status="success",
                    download_mbps=round(data.get("download", 0) / 1_000_000, 2),
                    upload_mbps=round(data.get("upload", 0) / 1_000_000, 2),
                    ping_ms=round(data.get("ping", 0), 1),
                    jitter_ms=None,  # speedtest-cli doesn't provide jitter
                    server_name=server.get("name"),
                    server_location=f"{server.get('name')}, {server.get('country')}",
                    server_host=server.get("host"),
                    server_id=server.get("id"),
                    client_ip=client.get("ip"),
                    isp=client.get("isp"),
                    tool=self.name,
                    raw_output=data,
                )
            else:
                return SpeedtestToolResult(
                    status="error",
                    tool=self.name,
                    error_message=result.stderr or f"Exit code {result.returncode}",
                )

        except subprocess.TimeoutExpired:
            return SpeedtestToolResult(
                status="timeout",
                tool=self.name,
                error_message=f"Timed out after {timeout}s",
            )
        except json.JSONDecodeError as e:
            return SpeedtestToolResult(
                status="error",
                tool=self.name,
                error_message=f"JSON parse error: {e}",
            )
        except Exception as e:
            return SpeedtestToolResult(
                status="error",
                tool=self.name,
                error_message=str(e),
            )


class FastCliTool(SpeedtestTool):
    """Netflix fast.com via fastcli Python package.

    Note: Only provides download speed - no upload or ping.
    This is representative of real-world streaming performance.
    """

    @property
    def name(self) -> str:
        return "fast-cli"

    def is_available(self) -> bool:
        try:
            result = subprocess.run(
                [sys.executable, "-c", "from fastcli.fastcli import run; print('ok')"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def run(self, timeout: int = 120, **kwargs) -> SpeedtestToolResult:
        # fastcli provides a simple API we can call via subprocess
        # API changed in v0.2.0: from fastcli.fastcli import run
        script = """
import json
try:
    from fastcli.fastcli import run
    speed = run(timeout=90, verbosity=50)  # Returns download speed in Mbps
    print(json.dumps({"status": "success", "download_mbps": speed}))
except Exception as e:
    print(json.dumps({"status": "error", "error": str(e)}))
"""
        try:
            result = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            if result.returncode == 0:
                data = json.loads(result.stdout.strip())
                if data.get("status") == "success":
                    return SpeedtestToolResult(
                        status="success",
                        download_mbps=round(data.get("download_mbps", 0), 2),
                        upload_mbps=0.0,  # fast.com doesn't measure upload
                        ping_ms=0.0,  # fast.com doesn't measure ping
                        server_name="Netflix CDN",
                        server_location="Netflix CDN (various)",
                        tool=self.name,
                        raw_output=data,
                    )
                else:
                    return SpeedtestToolResult(
                        status="error",
                        tool=self.name,
                        error_message=data.get("error", "Unknown error"),
                    )
            else:
                return SpeedtestToolResult(
                    status="error",
                    tool=self.name,
                    error_message=result.stderr or f"Exit code {result.returncode}",
                )

        except subprocess.TimeoutExpired:
            return SpeedtestToolResult(
                status="timeout",
                tool=self.name,
                error_message=f"Timed out after {timeout}s",
            )
        except json.JSONDecodeError as e:
            return SpeedtestToolResult(
                status="error",
                tool=self.name,
                error_message=f"JSON parse error: {e}",
            )
        except Exception as e:
            return SpeedtestToolResult(
                status="error",
                tool=self.name,
                error_message=str(e),
            )


# Registry of available tools
TOOL_REGISTRY: dict[str, type[SpeedtestTool]] = {
    "ookla-speedtest": OoklaSpeedtestTool,
    "speedtest-cli": SpeedtestCliTool,
    "fast-cli": FastCliTool,
}


def get_tool(name: str) -> SpeedtestTool | None:
    """Get a tool instance by name."""
    tool_class = TOOL_REGISTRY.get(name)
    if tool_class:
        return tool_class()
    return None


def detect_available_tools() -> list[str]:
    """Detect which speedtest tools are installed and available."""
    available = []
    for name, tool_class in TOOL_REGISTRY.items():
        tool = tool_class()
        try:
            if tool.is_available():
                available.append(name)
                log.debug("speedtest_tool_available", tool=name)
            else:
                log.debug("speedtest_tool_not_available", tool=name)
        except Exception as e:
            log.warning("speedtest_tool_check_failed", tool=name, error=str(e))
    return available
