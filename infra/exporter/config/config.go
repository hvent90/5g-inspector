// Package config provides configuration loading for the T-Mobile gateway exporter.
package config

import (
	"fmt"
	"os"
	"strings"
	"time"

	"gopkg.in/yaml.v3"

	"github.com/tmobile-dashboard/exporter/gateway"
)

// Config holds the application configuration.
type Config struct {
	// Gateway configuration
	Gateway GatewayConfig `yaml:"gateway"`

	// Metrics server configuration
	Metrics MetricsConfig `yaml:"metrics"`

	// Logging configuration
	Logging LoggingConfig `yaml:"logging"`
}

// GatewayConfig holds gateway connection settings.
type GatewayConfig struct {
	// URL is the base URL of the gateway
	URL string `yaml:"url"`

	// Model is the gateway model (arcadyan_kvd21, nokia, sagemcom, or auto)
	Model string `yaml:"model"`

	// PollInterval is how often to poll the gateway
	PollInterval time.Duration `yaml:"poll_interval"`

	// Timeout for gateway requests
	Timeout time.Duration `yaml:"timeout"`

	// Username for gateway authentication (if required)
	Username string `yaml:"username"`

	// Password for gateway authentication (if required)
	Password string `yaml:"password"`

	// InsecureSkipVerify skips TLS certificate verification
	InsecureSkipVerify bool `yaml:"insecure_skip_verify"`
}

// MetricsConfig holds Prometheus metrics server settings.
type MetricsConfig struct {
	// Port to serve metrics on
	Port int `yaml:"port"`

	// Path for metrics endpoint
	Path string `yaml:"path"`
}

// LoggingConfig holds logging settings.
type LoggingConfig struct {
	// Level is the log level (debug, info, warn, error)
	Level string `yaml:"level"`

	// Format is the log format (json, text)
	Format string `yaml:"format"`
}

// DefaultConfig returns a Config with sensible defaults.
func DefaultConfig() Config {
	return Config{
		Gateway: GatewayConfig{
			URL:                "http://192.168.12.1",
			Model:              "auto",
			PollInterval:       5 * time.Second,
			Timeout:            10 * time.Second,
			InsecureSkipVerify: true,
		},
		Metrics: MetricsConfig{
			Port: 9100,
			Path: "/metrics",
		},
		Logging: LoggingConfig{
			Level:  "info",
			Format: "text",
		},
	}
}

// LoadConfig loads configuration from a YAML file.
// If the file doesn't exist, it returns the default configuration.
func LoadConfig(path string) (*Config, error) {
	cfg := DefaultConfig()

	if path == "" {
		return &cfg, nil
	}

	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return &cfg, nil
		}
		return nil, fmt.Errorf("failed to read config file: %w", err)
	}

	if err := yaml.Unmarshal(data, &cfg); err != nil {
		return nil, fmt.Errorf("failed to parse config file: %w", err)
	}

	return &cfg, nil
}

// LoadConfigFromEnv loads configuration from environment variables.
// Environment variables override values from the config file.
func LoadConfigFromEnv(cfg *Config) {
	if url := os.Getenv("TMOBILE_GATEWAY_URL"); url != "" {
		cfg.Gateway.URL = url
	}

	if model := os.Getenv("TMOBILE_GATEWAY_MODEL"); model != "" {
		cfg.Gateway.Model = model
	}

	if interval := os.Getenv("TMOBILE_POLL_INTERVAL"); interval != "" {
		if d, err := time.ParseDuration(interval); err == nil {
			cfg.Gateway.PollInterval = d
		}
	}

	if port := os.Getenv("TMOBILE_METRICS_PORT"); port != "" {
		var p int
		if _, err := fmt.Sscanf(port, "%d", &p); err == nil {
			cfg.Metrics.Port = p
		}
	}

	if username := os.Getenv("TMOBILE_GATEWAY_USERNAME"); username != "" {
		cfg.Gateway.Username = username
	}

	if password := os.Getenv("TMOBILE_GATEWAY_PASSWORD"); password != "" {
		cfg.Gateway.Password = password
	}

	if level := os.Getenv("TMOBILE_LOG_LEVEL"); level != "" {
		cfg.Logging.Level = level
	}
}

// ToGatewayConfig converts the config to a gateway.ClientConfig.
func (c *Config) ToGatewayConfig() gateway.ClientConfig {
	model := gateway.ModelUnknown
	switch strings.ToLower(c.Gateway.Model) {
	case "arcadyan_kvd21", "arcadyan", "kvd21":
		model = gateway.ModelArcadyanKVD21
	case "nokia":
		model = gateway.ModelNokia
	case "sagemcom":
		model = gateway.ModelSagemcom
	case "auto", "":
		model = gateway.ModelUnknown
	}

	return gateway.ClientConfig{
		URL:                c.Gateway.URL,
		Model:              model,
		Timeout:            c.Gateway.Timeout,
		Username:           c.Gateway.Username,
		Password:           c.Gateway.Password,
		InsecureSkipVerify: c.Gateway.InsecureSkipVerify,
	}
}
