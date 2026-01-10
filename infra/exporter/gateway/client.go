package gateway

import (
	"crypto/tls"
	"fmt"
	"net/http"
	"time"
)

// ClientConfig contains configuration for connecting to a gateway.
type ClientConfig struct {
	// URL is the base URL of the gateway (e.g., http://192.168.12.1)
	URL string

	// Model is the gateway model (auto-detect if empty)
	Model GatewayModel

	// Timeout for HTTP requests
	Timeout time.Duration

	// Username for authentication (if required)
	Username string

	// Password for authentication (if required)
	Password string

	// InsecureSkipVerify skips TLS certificate verification
	InsecureSkipVerify bool
}

// DefaultConfig returns a ClientConfig with default values.
func DefaultConfig() ClientConfig {
	return ClientConfig{
		URL:                "http://192.168.12.1",
		Model:              ModelUnknown,
		Timeout:            10 * time.Second,
		InsecureSkipVerify: true,
	}
}

// NewClient creates a new gateway client based on the configuration.
// If model is not specified, it attempts to auto-detect the gateway type.
func NewClient(cfg ClientConfig) (GatewayClient, error) {
	httpClient := &http.Client{
		Timeout: cfg.Timeout,
		Transport: &http.Transport{
			TLSClientConfig: &tls.Config{
				InsecureSkipVerify: cfg.InsecureSkipVerify,
			},
		},
	}

	// If model is specified, create the appropriate client
	switch cfg.Model {
	case ModelArcadyanKVD21:
		return NewArcadyanClient(cfg, httpClient)
	case ModelNokia:
		return NewNokiaClient(cfg, httpClient)
	case ModelSagemcom:
		return NewSagemcomClient(cfg, httpClient)
	case ModelUnknown:
		// Auto-detect the gateway model
		return autoDetectClient(cfg, httpClient)
	default:
		return nil, fmt.Errorf("unsupported gateway model: %s", cfg.Model)
	}
}

// autoDetectClient attempts to detect the gateway type and return the appropriate client.
func autoDetectClient(cfg ClientConfig, httpClient *http.Client) (GatewayClient, error) {
	// Try Arcadyan KVD21 first (most common T-Mobile gateway)
	arcadyanClient, err := NewArcadyanClient(cfg, httpClient)
	if err == nil {
		_, err = arcadyanClient.GetStatus()
		if err == nil {
			return arcadyanClient, nil
		}
		arcadyanClient.Close()
	}

	// Try Nokia
	nokiaClient, err := NewNokiaClient(cfg, httpClient)
	if err == nil {
		_, err = nokiaClient.GetStatus()
		if err == nil {
			return nokiaClient, nil
		}
		nokiaClient.Close()
	}

	// Try Sagemcom
	sagemcomClient, err := NewSagemcomClient(cfg, httpClient)
	if err == nil {
		_, err = sagemcomClient.GetStatus()
		if err == nil {
			return sagemcomClient, nil
		}
		sagemcomClient.Close()
	}

	return nil, fmt.Errorf("could not auto-detect gateway model at %s", cfg.URL)
}
