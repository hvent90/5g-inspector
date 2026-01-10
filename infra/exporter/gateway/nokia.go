package gateway

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
)

// NokiaClient implements GatewayClient for Nokia FastMile 5G gateways.
// Tested with Nokia FastMile 5G Gateway (model 5G21).
type NokiaClient struct {
	config     ClientConfig
	httpClient *http.Client
}

// nokiaRadioStatus represents the JSON response from Nokia's radio status endpoint.
type nokiaRadioStatus struct {
	// 5G NR stats
	Cell5G nokiaCellStats `json:"cell_5G_stats"`
	// LTE stats
	CellLTE nokiaCellStats `json:"cell_LTE_stats"`
}

type nokiaCellStats struct {
	RSRP      interface{} `json:"rsrp"`
	RSRQ      interface{} `json:"rsrq"`
	RSSI      interface{} `json:"rssi"`
	SINR      interface{} `json:"sinr"`
	SNR       interface{} `json:"snr"`
	Band      interface{} `json:"band"`
	PCI       interface{} `json:"pci"`
	CellID    interface{} `json:"cid"`
	EARFCN    interface{} `json:"earfcn"`
	Bandwidth interface{} `json:"bandwidth"`
	TAC       interface{} `json:"tac"`
	// Connection state
	State string `json:"state"`
}

// nokiaDeviceInfo represents the JSON response from Nokia's device info endpoint.
type nokiaDeviceInfo struct {
	Device struct {
		Model        string `json:"model"`
		SerialNumber string `json:"serial"`
		FirmwareVer  string `json:"firmware_version"`
	} `json:"device"`
}

// NewNokiaClient creates a new client for Nokia gateways.
func NewNokiaClient(cfg ClientConfig, httpClient *http.Client) (*NokiaClient, error) {
	if httpClient == nil {
		return nil, fmt.Errorf("httpClient is required")
	}
	return &NokiaClient{
		config:     cfg,
		httpClient: httpClient,
	}, nil
}

// GetStatus retrieves the current gateway status.
func (c *NokiaClient) GetStatus() (*GatewayStatus, error) {
	status := &GatewayStatus{
		Model: string(ModelNokia),
	}

	// Nokia FastMile gateways expose API at /fastmile_radio_status_web_app.cgi
	// similar to Arcadyan, or at /api/model/gateway
	radioStatus, err := c.getRadioStatus()
	if err != nil {
		return nil, fmt.Errorf("failed to get radio status: %w", err)
	}

	// Prefer 5G if available
	connectionType := "LTE"
	var cellStats *nokiaCellStats

	if radioStatus.Cell5G.State == "connected" || c.hasValidSignal(&radioStatus.Cell5G) {
		cellStats = &radioStatus.Cell5G
		connectionType = "5G"
	} else if radioStatus.CellLTE.State == "connected" || c.hasValidSignal(&radioStatus.CellLTE) {
		cellStats = &radioStatus.CellLTE
	}

	if cellStats != nil {
		status.Signal = c.parseSignalMetrics(cellStats)
		status.Cell = c.parseCellInfo(cellStats)
	}

	status.Connection = ConnectionInfo{
		Type:   connectionType,
		Status: "connected",
	}

	return status, nil
}

// hasValidSignal checks if the cell stats contain valid signal data.
func (c *NokiaClient) hasValidSignal(stats *nokiaCellStats) bool {
	rsrp := c.parseNumeric(stats.RSRP)
	return rsrp != 0 && rsrp > -200
}

// getRadioStatus fetches the radio status from the Nokia gateway.
func (c *NokiaClient) getRadioStatus() (*nokiaRadioStatus, error) {
	// Try Nokia-specific endpoint first
	endpoints := []string{
		"/fastmile_radio_status_web_app.cgi",
		"/api/model/gateway",
		"/api/v1/network/status",
	}

	var lastErr error
	for _, endpoint := range endpoints {
		url := fmt.Sprintf("%s%s", c.config.URL, endpoint)
		resp, err := c.httpClient.Get(url)
		if err != nil {
			lastErr = err
			continue
		}
		defer resp.Body.Close()

		if resp.StatusCode != http.StatusOK {
			lastErr = fmt.Errorf("unexpected status code: %d", resp.StatusCode)
			continue
		}

		body, err := io.ReadAll(resp.Body)
		if err != nil {
			lastErr = fmt.Errorf("failed to read response body: %w", err)
			continue
		}

		var radioStatus nokiaRadioStatus
		if err := json.Unmarshal(body, &radioStatus); err != nil {
			// Try alternate response format
			var altResponse map[string]interface{}
			if err2 := json.Unmarshal(body, &altResponse); err2 != nil {
				lastErr = fmt.Errorf("failed to parse JSON response: %w", err)
				continue
			}
			radioStatus = c.parseAlternateFormat(altResponse)
		}

		return &radioStatus, nil
	}

	if lastErr != nil {
		return nil, lastErr
	}
	return nil, fmt.Errorf("could not connect to Nokia gateway")
}

// parseAlternateFormat handles different JSON response formats from Nokia gateways.
func (c *NokiaClient) parseAlternateFormat(data map[string]interface{}) nokiaRadioStatus {
	var result nokiaRadioStatus

	// Try to extract 5G stats
	if cell5g, ok := data["cell_5G_stats"].(map[string]interface{}); ok {
		result.Cell5G = c.mapToNokiaCellStats(cell5g)
	} else if cell5g, ok := data["5g"].(map[string]interface{}); ok {
		result.Cell5G = c.mapToNokiaCellStats(cell5g)
	}

	// Try to extract LTE stats
	if cellLTE, ok := data["cell_LTE_stats"].(map[string]interface{}); ok {
		result.CellLTE = c.mapToNokiaCellStats(cellLTE)
	} else if cellLTE, ok := data["lte"].(map[string]interface{}); ok {
		result.CellLTE = c.mapToNokiaCellStats(cellLTE)
	}

	return result
}

// mapToNokiaCellStats converts a generic map to nokiaCellStats.
func (c *NokiaClient) mapToNokiaCellStats(data map[string]interface{}) nokiaCellStats {
	return nokiaCellStats{
		RSRP:      data["rsrp"],
		RSRQ:      data["rsrq"],
		RSSI:      data["rssi"],
		SINR:      data["sinr"],
		SNR:       data["snr"],
		Band:      data["band"],
		PCI:       data["pci"],
		CellID:    data["cid"],
		TAC:       data["tac"],
		Bandwidth: data["bandwidth"],
		State:     fmt.Sprintf("%v", data["state"]),
	}
}

// parseSignalMetrics extracts signal metrics from Nokia cell stats.
func (c *NokiaClient) parseSignalMetrics(stats *nokiaCellStats) SignalMetrics {
	sinr := c.parseNumeric(stats.SINR)
	if sinr == 0 {
		sinr = c.parseNumeric(stats.SNR)
	}

	return SignalMetrics{
		RSRP: c.parseNumeric(stats.RSRP),
		RSRQ: c.parseNumeric(stats.RSRQ),
		RSSI: c.parseNumeric(stats.RSSI),
		SINR: sinr,
	}
}

// parseCellInfo extracts cell information from Nokia cell stats.
func (c *NokiaClient) parseCellInfo(stats *nokiaCellStats) CellInfo {
	return CellInfo{
		PCI:       int64(c.parseNumeric(stats.PCI)),
		ENB:       int64(c.parseNumeric(stats.CellID)),
		TAC:       int64(c.parseNumeric(stats.TAC)),
		Band:      c.parseBandNumber(stats.Band),
		Bandwidth: fmt.Sprintf("%v", stats.Bandwidth),
	}
}

// parseNumeric handles various numeric formats that may come from the API.
func (c *NokiaClient) parseNumeric(v interface{}) float64 {
	if v == nil {
		return 0
	}
	switch val := v.(type) {
	case float64:
		return val
	case float32:
		return float64(val)
	case int:
		return float64(val)
	case int64:
		return float64(val)
	case string:
		val = strings.TrimSpace(val)
		if val == "" || val == "N/A" || val == "null" {
			return 0
		}
		val = strings.Split(val, " ")[0]
		if f, err := strconv.ParseFloat(val, 64); err == nil {
			return f
		}
	}
	return 0
}

// parseBandNumber extracts the band number from various formats.
func (c *NokiaClient) parseBandNumber(v interface{}) int64 {
	if v == nil {
		return 0
	}
	switch val := v.(type) {
	case float64:
		return int64(val)
	case int:
		return int64(val)
	case int64:
		return val
	case string:
		val = strings.TrimSpace(val)
		val = strings.TrimPrefix(strings.ToLower(val), "b")
		val = strings.TrimPrefix(val, "n")
		if i, err := strconv.ParseInt(val, 10, 64); err == nil {
			return i
		}
	}
	return 0
}

// GetModel returns the gateway model type.
func (c *NokiaClient) GetModel() GatewayModel {
	return ModelNokia
}

// Close releases any resources held by the client.
func (c *NokiaClient) Close() error {
	return nil
}
