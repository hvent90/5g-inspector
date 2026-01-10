// T-Mobile Gateway Prometheus Exporter
//
// This exporter collects signal metrics from T-Mobile 5G/LTE home internet
// gateways and exposes them in Prometheus format.
//
// Usage:
//
//	tmobile-exporter [flags]
//
// Flags:
//
//	-config string    Path to config file (default: no config file)
//	-port int         Port to serve metrics on (default: 9100)
//	-gateway string   Gateway URL (default: http://192.168.12.1)
//	-model string     Gateway model: arcadyan_kvd21, nokia, sagemcom, auto (default: auto)
//	-interval string  Poll interval (default: 5s)
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"

	"github.com/tmobile-dashboard/exporter/config"
	"github.com/tmobile-dashboard/exporter/gateway"
	"github.com/tmobile-dashboard/exporter/metrics"
)

var (
	version = "dev"
	commit  = "unknown"
	date    = "unknown"
)

func main() {
	// Parse command line flags
	configPath := flag.String("config", "", "Path to config file")
	port := flag.Int("port", 0, "Port to serve metrics on (default: 9100)")
	gatewayURL := flag.String("gateway", "", "Gateway URL (default: http://192.168.12.1)")
	model := flag.String("model", "", "Gateway model: arcadyan_kvd21, nokia, sagemcom, auto (default: auto)")
	interval := flag.String("interval", "", "Poll interval (default: 5s)")
	showVersion := flag.Bool("version", false, "Show version information")
	flag.Parse()

	if *showVersion {
		fmt.Printf("tmobile-exporter %s (commit: %s, built: %s)\n", version, commit, date)
		os.Exit(0)
	}

	// Load configuration
	cfg, err := config.LoadConfig(*configPath)
	if err != nil {
		log.Fatalf("Failed to load config: %v", err)
	}

	// Load environment variables
	config.LoadConfigFromEnv(cfg)

	// Override with command line flags
	if *port != 0 {
		cfg.Metrics.Port = *port
	}
	if *gatewayURL != "" {
		cfg.Gateway.URL = *gatewayURL
	}
	if *model != "" {
		cfg.Gateway.Model = *model
	}
	if *interval != "" {
		if d, err := time.ParseDuration(*interval); err == nil {
			cfg.Gateway.PollInterval = d
		}
	}

	log.Printf("Starting T-Mobile Gateway Exporter %s", version)
	log.Printf("Gateway URL: %s", cfg.Gateway.URL)
	log.Printf("Gateway Model: %s", cfg.Gateway.Model)
	log.Printf("Poll Interval: %s", cfg.Gateway.PollInterval)
	log.Printf("Metrics Port: %d", cfg.Metrics.Port)

	// Create gateway client
	gwClient, err := gateway.NewClient(cfg.ToGatewayConfig())
	if err != nil {
		log.Fatalf("Failed to create gateway client: %v", err)
	}
	defer gwClient.Close()

	log.Printf("Detected gateway model: %s", gwClient.GetModel())

	// Create metrics collector
	collector := metrics.NewCollector(gwClient)
	defer collector.Close()

	// Register collector with Prometheus
	prometheus.MustRegister(collector)

	// Create HTTP server
	mux := http.NewServeMux()
	mux.Handle(cfg.Metrics.Path, promhttp.Handler())
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("OK"))
	})
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`<html>
<head><title>T-Mobile Gateway Exporter</title></head>
<body>
<h1>T-Mobile Gateway Exporter</h1>
<p>Version: ` + version + `</p>
<p>Gateway: ` + cfg.Gateway.URL + `</p>
<p>Model: ` + string(gwClient.GetModel()) + `</p>
<p><a href="` + cfg.Metrics.Path + `">Metrics</a></p>
</body>
</html>`))
	})

	server := &http.Server{
		Addr:         fmt.Sprintf(":%d", cfg.Metrics.Port),
		Handler:      mux,
		ReadTimeout:  10 * time.Second,
		WriteTimeout: 10 * time.Second,
	}

	// Handle graceful shutdown
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)

	go func() {
		<-sigChan
		log.Println("Shutting down...")
		cancel()

		shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer shutdownCancel()

		if err := server.Shutdown(shutdownCtx); err != nil {
			log.Printf("HTTP server shutdown error: %v", err)
		}
	}()

	// Start server
	log.Printf("Serving metrics at http://localhost:%d%s", cfg.Metrics.Port, cfg.Metrics.Path)
	if err := server.ListenAndServe(); err != http.ErrServerClosed {
		log.Fatalf("HTTP server error: %v", err)
	}

	<-ctx.Done()
	log.Println("Exporter stopped")
}
