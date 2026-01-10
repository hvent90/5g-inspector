// Package gateway provides types and clients for communicating with T-Mobile gateway devices.
package gateway

// SignalMetrics contains the signal quality metrics from the gateway.
type SignalMetrics struct {
	// RSRP - Reference Signal Received Power (dBm)
	// Typical range: -140 to -44 dBm
	RSRP float64

	// RSRQ - Reference Signal Received Quality (dB)
	// Typical range: -20 to -3 dB
	RSRQ float64

	// SINR - Signal to Interference Noise Ratio (dB)
	// Typical range: -20 to 30 dB
	SINR float64

	// RSSI - Received Signal Strength Indicator (dBm)
	// Typical range: -120 to -25 dBm
	RSSI float64
}

// CellInfo contains information about the connected cell tower.
type CellInfo struct {
	// PCI - Physical Cell ID
	PCI int64

	// ENB - eNodeB ID (cell tower identifier)
	ENB int64

	// TAC - Tracking Area Code
	TAC int64

	// Band - Current frequency band number
	Band int64

	// Bandwidth - Channel bandwidth in MHz
	Bandwidth string
}

// ConnectionInfo contains connection type and status.
type ConnectionInfo struct {
	// Type - Connection type (4G, 5G, LTE, etc.)
	Type string

	// Status - Connection status
	Status string
}

// GatewayStatus contains all metrics from the gateway.
type GatewayStatus struct {
	// Signal contains signal quality metrics
	Signal SignalMetrics

	// Cell contains cell tower information
	Cell CellInfo

	// Connection contains connection type and status
	Connection ConnectionInfo

	// Model contains the gateway model identifier
	Model string
}

// GatewayModel represents supported gateway models.
type GatewayModel string

const (
	ModelArcadyanKVD21 GatewayModel = "arcadyan_kvd21"
	ModelNokia         GatewayModel = "nokia"
	ModelSagemcom      GatewayModel = "sagemcom"
	ModelUnknown       GatewayModel = "unknown"
)

// GatewayClient is the interface that gateway implementations must satisfy.
type GatewayClient interface {
	// GetStatus retrieves the current gateway status including signal metrics.
	GetStatus() (*GatewayStatus, error)

	// GetModel returns the gateway model type.
	GetModel() GatewayModel

	// Close releases any resources held by the client.
	Close() error
}
