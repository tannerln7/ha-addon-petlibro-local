package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"net"
	"os"
	"os/signal"
	"time"

	"github.com/AlexxIT/go2rtc/pkg/petlibro"
)

type stringList []string

func (values *stringList) String() string { return fmt.Sprint([]string(*values)) }
func (values *stringList) Set(value string) error {
	*values = append(*values, value)
	return nil
}

func main() {
	var subnets stringList
	var candidateValues stringList
	uid := flag.String("uid", "", "exact 20-character Petlibro camera UID")
	flag.Var(&subnets, "subnet", "IPv4 CIDR to scan; may be repeated")
	cachedIP := flag.String("cached-ip", "", "previously resolved IP to verify first")
	flag.Var(&candidateValues, "candidate", "known candidate IP to try before subnet fallback; may be repeated")
	timeout := flag.Duration("timeout", 15*time.Second, "overall discovery timeout")
	broadcastDuration := flag.Duration("broadcast-duration", 2*time.Second, "broadcast-first stage duration")
	maxUnicastRate := flag.Int("max-unicast-per-second", 32, "maximum paced subnet probes per second")
	jsonOutput := flag.Bool("json", false, "write one JSON result to stdout")
	flag.Parse()

	var candidates []net.IP
	for _, value := range candidateValues {
		candidates = append(candidates, net.ParseIP(value))
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()
	output, err := petlibro.ResolveUIDContext(ctx, petlibro.ResolveOptions{
		UID:                 *uid,
		Subnets:             subnets,
		CachedIP:            net.ParseIP(*cachedIP),
		Candidates:          candidates,
		Timeout:             *timeout,
		BroadcastDuration:   *broadcastDuration,
		MaxUnicastPerSecond: *maxUnicastRate,
	})
	if output.ErrorCode == "" && err != nil {
		output.ErrorCode = "not_found"
		output.Error = err.Error()
	}

	if *jsonOutput {
		_ = json.NewEncoder(os.Stdout).Encode(output)
	} else if output.Resolved {
		fmt.Fprintln(os.Stdout, *output.IPAddress)
	} else {
		fmt.Fprintln(os.Stderr, output.Error)
	}
	if err != nil {
		os.Exit(1)
	}
}
