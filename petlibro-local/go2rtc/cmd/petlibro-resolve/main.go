package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"time"

	"github.com/AlexxIT/go2rtc/pkg/petlibro"
)

type stringList []string

func (values *stringList) String() string { return fmt.Sprint([]string(*values)) }
func (values *stringList) Set(value string) error {
	*values = append(*values, value)
	return nil
}

type result struct {
	UID       string `json:"uid"`
	Subnet    string `json:"subnet,omitempty"`
	IPAddress string `json:"ip_address,omitempty"`
	Resolved  bool   `json:"resolved"`
	Method    string `json:"method"`
	ElapsedMS int64  `json:"elapsed_ms"`
	Error     string `json:"error,omitempty"`
}

func main() {
	var subnets stringList
	uid := flag.String("uid", "", "exact 20-character Petlibro camera UID")
	flag.Var(&subnets, "subnet", "IPv4 CIDR to scan; may be repeated")
	timeout := flag.Duration("timeout", 10*time.Second, "overall discovery timeout")
	jsonOutput := flag.Bool("json", false, "write one JSON result to stdout")
	flag.Parse()

	started := time.Now()
	ip, err := petlibro.ResolveUID(*uid, subnets, *timeout)
	output := result{
		UID:       *uid,
		Resolved:  err == nil,
		Method:    "lan_search3",
		ElapsedMS: time.Since(started).Milliseconds(),
	}
	if len(subnets) == 1 {
		output.Subnet = subnets[0]
	}
	if err != nil {
		output.Error = err.Error()
	} else {
		output.IPAddress = ip.String()
	}

	if *jsonOutput {
		_ = json.NewEncoder(os.Stdout).Encode(output)
	} else if err == nil {
		fmt.Fprintln(os.Stdout, output.IPAddress)
	} else {
		fmt.Fprintln(os.Stderr, output.Error)
	}
	if err != nil {
		os.Exit(1)
	}
}
