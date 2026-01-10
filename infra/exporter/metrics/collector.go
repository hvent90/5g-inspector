// Package metrics provides Prometheus metric collection for T-Mobile gateways.
package metrics

import (
	"log"
	"sync"

	"github.com/prometheus/client_golang/prometheus"

	"github.com/tmobile-dashboard/exporter/gateway"
)

// Collector implements prometheus.Collector for T-Mobile gateway metrics.
type Collector struct {
	client gateway.GatewayClient
	mu     sync.Mutex

	// Signal metrics
	rsrpDesc *prometheus.Desc
	rsrqDesc *prometheus.Desc
	sinrDesc *prometheus.Desc
	rssiDesc *prometheus.Desc

	// Cell metrics
	pciDesc  *prometheus.Desc
	enbDesc  *prometheus.Desc
	tacDesc  *prometheus.Desc
	bandDesc *prometheus.Desc

	// Connection metrics
	connectionTypeDesc *prometheus.Desc

	// Scrape metrics
	scrapeSuccessDesc *prometheus.Desc
	scrapeDurationDesc *prometheus.Desc
}

// NewCollector creates a new Collector with the given gateway client.
func NewCollector(client gateway.GatewayClient) *Collector {
	labels := []string{"model"}

	return &Collector{
		client: client,

		// Signal metrics
		rsrpDesc: prometheus.NewDesc(
			"tmobile_signal_rsrp",
			"Reference Signal Received Power in dBm",
			labels,
			nil,
		),
		rsrqDesc: prometheus.NewDesc(
			"tmobile_signal_rsrq",
			"Reference Signal Received Quality in dB",
			labels,
			nil,
		),
		sinrDesc: prometheus.NewDesc(
			"tmobile_signal_sinr",
			"Signal to Interference Noise Ratio in dB",
			labels,
			nil,
		),
		rssiDesc: prometheus.NewDesc(
			"tmobile_signal_rssi",
			"Received Signal Strength Indicator in dBm",
			labels,
			nil,
		),

		// Cell metrics
		pciDesc: prometheus.NewDesc(
			"tmobile_cell_pci",
			"Physical Cell ID",
			labels,
			nil,
		),
		enbDesc: prometheus.NewDesc(
			"tmobile_cell_enb",
			"eNodeB ID",
			labels,
			nil,
		),
		tacDesc: prometheus.NewDesc(
			"tmobile_cell_tac",
			"Tracking Area Code",
			labels,
			nil,
		),
		bandDesc: prometheus.NewDesc(
			"tmobile_cell_band",
			"Current frequency band number",
			labels,
			nil,
		),

		// Connection metrics
		connectionTypeDesc: prometheus.NewDesc(
			"tmobile_connection_type",
			"Connection type (1=4G/LTE, 2=5G)",
			labels,
			nil,
		),

		// Scrape metrics
		scrapeSuccessDesc: prometheus.NewDesc(
			"tmobile_scrape_success",
			"Whether the last scrape was successful",
			nil,
			nil,
		),
		scrapeDurationDesc: prometheus.NewDesc(
			"tmobile_scrape_duration_seconds",
			"Duration of the last scrape in seconds",
			nil,
			nil,
		),
	}
}

// Describe implements prometheus.Collector.
func (c *Collector) Describe(ch chan<- *prometheus.Desc) {
	ch <- c.rsrpDesc
	ch <- c.rsrqDesc
	ch <- c.sinrDesc
	ch <- c.rssiDesc
	ch <- c.pciDesc
	ch <- c.enbDesc
	ch <- c.tacDesc
	ch <- c.bandDesc
	ch <- c.connectionTypeDesc
	ch <- c.scrapeSuccessDesc
	ch <- c.scrapeDurationDesc
}

// Collect implements prometheus.Collector.
func (c *Collector) Collect(ch chan<- prometheus.Metric) {
	c.mu.Lock()
	defer c.mu.Unlock()

	timer := prometheus.NewTimer(prometheus.ObserverFunc(func(v float64) {
		ch <- prometheus.MustNewConstMetric(c.scrapeDurationDesc, prometheus.GaugeValue, v)
	}))
	defer timer.ObserveDuration()

	status, err := c.client.GetStatus()
	if err != nil {
		log.Printf("Error collecting metrics: %v", err)
		ch <- prometheus.MustNewConstMetric(c.scrapeSuccessDesc, prometheus.GaugeValue, 0)
		return
	}

	ch <- prometheus.MustNewConstMetric(c.scrapeSuccessDesc, prometheus.GaugeValue, 1)

	model := status.Model

	// Signal metrics
	ch <- prometheus.MustNewConstMetric(c.rsrpDesc, prometheus.GaugeValue, status.Signal.RSRP, model)
	ch <- prometheus.MustNewConstMetric(c.rsrqDesc, prometheus.GaugeValue, status.Signal.RSRQ, model)
	ch <- prometheus.MustNewConstMetric(c.sinrDesc, prometheus.GaugeValue, status.Signal.SINR, model)
	ch <- prometheus.MustNewConstMetric(c.rssiDesc, prometheus.GaugeValue, status.Signal.RSSI, model)

	// Cell metrics
	ch <- prometheus.MustNewConstMetric(c.pciDesc, prometheus.GaugeValue, float64(status.Cell.PCI), model)
	ch <- prometheus.MustNewConstMetric(c.enbDesc, prometheus.GaugeValue, float64(status.Cell.ENB), model)
	ch <- prometheus.MustNewConstMetric(c.tacDesc, prometheus.GaugeValue, float64(status.Cell.TAC), model)
	ch <- prometheus.MustNewConstMetric(c.bandDesc, prometheus.GaugeValue, float64(status.Cell.Band), model)

	// Connection type (1 = 4G/LTE, 2 = 5G)
	connectionType := 1.0
	if status.Connection.Type == "5G" {
		connectionType = 2.0
	}
	ch <- prometheus.MustNewConstMetric(c.connectionTypeDesc, prometheus.GaugeValue, connectionType, model)
}

// Close releases resources held by the collector.
func (c *Collector) Close() error {
	if c.client != nil {
		return c.client.Close()
	}
	return nil
}
