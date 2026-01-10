package gateway

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
)

// ArcadyanClient implements GatewayClient for the Arcadyan KVD21 gateway.
type ArcadyanClient struct {
	config     ClientConfig
	httpClient *http.Client
}

// arcadyanRadioStatus represents the JSON response from the radio status endpoint.
type arcadyanRadioStatus struct {
	Cell5GStats []arcadyanCellStats `json:"cell_5G_stats_cfg"`
	CellLTEStats []arcadyanCellStats `json:"cell_LTE_stats_cfg"`
}

type arcadyanCellStats struct {
	StatRSRP      string `json:"stat_RSRP"`
	StatRSRQ      string `json:"stat_RSRQ"`
	StatRSSI      string `json:"stat_RSSI"`
	StatSNR       string `json:"stat_SNR"`
	StatSINR      string `json:"stat_SINR"`
	StatBand      string `json:"stat_Band"`
	StatPCI       string `json:"stat_PCI"`
	StatENBID     string `json:"stat_eNB_ID"`
	StatCellID    string `json:"stat_Cell_ID"`
	StatTAC       string `json:"stat_TAC"`
	PhyCellID     string `json:"stat_PhyCellId"`
	Bandwidth     string `json:"stat_Bandwidth"`
}

// arcadyanGatewayInfo represents the JSON response from the gateway info endpoint.
type arcadyanGatewayInfo struct {
	Device struct {
		Model     string `json:"model"`
		IsCellular bool   `json:"isCellular"`
	} `json:"device"`
	Connection struct {
		ConnectionType string `json:"type"`
		Status         string `json:"status"`
	} `json:"connection"`
}

// NewArcadyanClient creates a new client for Arcadyan KVD21 gateways.
func NewArcadyanClient(cfg ClientConfig, httpClient *http.Client) (*ArcadyanClient, error) {
	if httpClient == nil {
		return nil, fmt.Errorf("httpClient is required")
	}
	return &ArcadyanClient{
		config:     cfg,
		httpClient: httpClient,
	}, nil
}

// GetStatus retrieves the current gateway status.
func (c *ArcadyanClient) GetStatus() (*GatewayStatus, error) {
	status := &GatewayStatus{
		Model: string(ModelArcadyanKVD21),
	}

	// Try the CGI endpoint first (doesn't require authentication)
	radioStatus, err := c.getRadioStatus()
	if err != nil {
		return nil, fmt.Errorf("failed to get radio status: %w", err)
	}

	// Parse 5G stats if available, otherwise fall back to LTE
	var cellStats *arcadyanCellStats
	connectionType := "LTE"

	if len(radioStatus.Cell5GStats) > 0 && radioStatus.Cell5GStats[0].StatRSRP != "" {
		cellStats = &radioStatus.Cell5GStats[0]
		connectionType = "5G"
	} else if len(radioStatus.CellLTEStats) > 0 {
		cellStats = &radioStatus.CellLTEStats[0]
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

// getRadioStatus fetches the radio status from the gateway.
func (c *ArcadyanClient) getRadioStatus() (*arcadyanRadioStatus, error) {
	// Try the CGI endpoint first
	url := fmt.Sprintf("%s/fastmile_radio_status_web_app.cgi", c.config.URL)

	resp, err := c.httpClient.Get(url)
	if err != nil {
		return nil, fmt.Errorf("HTTP request failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("unexpected status code: %d", resp.StatusCode)
	}

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read response body: %w", err)
	}

	var radioStatus arcadyanRadioStatus
	if err := json.Unmarshal(body, &radioStatus); err != nil {
		return nil, fmt.Errorf("failed to parse JSON response: %w", err)
	}

	return &radioStatus, nil
}

// parseSignalMetrics extracts signal metrics from cell stats.
func (c *ArcadyanClient) parseSignalMetrics(stats *arcadyanCellStats) SignalMetrics {
	return SignalMetrics{
		RSRP: parseFloat(stats.StatRSRP),
		RSRQ: parseFloat(stats.StatRSRQ),
		RSSI: parseFloat(stats.StatRSSI),
		SINR: parseSINR(stats.StatSNR, stats.StatSINR),
	}
}

// parseCellInfo extracts cell information from cell stats.
func (c *ArcadyanClient) parseCellInfo(stats *arcadyanCellStats) CellInfo {
	return CellInfo{
		PCI:       parseInt(stats.StatPCI, stats.PhyCellID),
		ENB:       parseInt(stats.StatENBID),
		TAC:       parseInt(stats.StatTAC),
		Band:      parseBand(stats.StatBand),
		Bandwidth: stats.Bandwidth,
	}
}

// GetModel returns the gateway model type.
func (c *ArcadyanClient) GetModel() GatewayModel {
	return ModelArcadyanKVD21
}

// Close releases any resources held by the client.
func (c *ArcadyanClient) Close() error {
	return nil
}

// Helper functions for parsing values

func parseFloat(values ...string) float64 {
	for _, v := range values {
		v = strings.TrimSpace(v)
		if v == "" || v == "N/A" {
			continue
		}
		// Remove any units or suffixes
		v = strings.Split(v, " ")[0]
		if f, err := strconv.ParseFloat(v, 64); err == nil {
			return f
		}
	}
	return 0
}

func parseSINR(values ...string) float64 {
	for _, v := range values {
		v = strings.TrimSpace(v)
		if v == "" || v == "N/A" {
			continue
		}
		v = strings.Split(v, " ")[0]
		if f, err := strconv.ParseFloat(v, 64); err == nil {
			return f
		}
	}
	return 0
}

func parseInt(values ...string) int64 {
	for _, v := range values {
		v = strings.TrimSpace(v)
		if v == "" || v == "N/A" {
			continue
		}
		// Handle hex values
		if strings.HasPrefix(v, "0x") || strings.HasPrefix(v, "0X") {
			if i, err := strconv.ParseInt(v[2:], 16, 64); err == nil {
				return i
			}
		}
		if i, err := strconv.ParseInt(v, 10, 64); err == nil {
			return i
		}
	}
	return 0
}

func parseBand(value string) int64 {
	value = strings.TrimSpace(value)
	// Handle formats like "B66", "n41", "b66", etc.
	value = strings.TrimPrefix(strings.ToLower(value), "b")
	value = strings.TrimPrefix(value, "n")
	if i, err := strconv.ParseInt(value, 10, 64); err == nil {
		return i
	}
	return 0
}
